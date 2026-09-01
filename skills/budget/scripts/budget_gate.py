"""Draft store + approval records + gws gmail execution for W4-3 (제약 1).

NO request mail is ever sent before an owner confirmation (budget_confirm has
the two transports). On confirm the gate appends the external-effect approval
record (the schema the deployed pre_tool_call gate reads for gws_gmail_send)
plus a W0-6 audit record to approvals.jsonl, then executes the exact gws argv
frozen into the draft at draft time, then appends a send-log line.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Final, Protocol

import budget_core

GWS_TIMEOUT_S = 120
BINDING_FIELDS: Final = ("kind", "surface", "channel_id", "policy_version")


class GateError(RuntimeError):
    """Gate refusal with a CLI exit code (1 unconfirmed, 3 config, 6 exec)."""

    def __init__(self, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class Approval:
    """A verified owner confirmation bound to one draft execution."""

    ref: str
    method: str
    owner: str

class ApprovalBindingLike(Protocol):
    """The part of ``approval_surface.ApprovalBinding`` a draft record persists."""

    kind: str
    surface: str
    channel_id: str
    policy_version: int



def gate_dir() -> Path:
    path = Path(os.environ.get("BUDGET_GATE_DIR", "~/.hermes/budget-gate")).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def repo_root() -> Path:
    """The checkout carrying ``automation.interop``, not the mounted-release depth guess."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = [*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents")]
    for candidate in candidates:
        if (candidate / "automation" / "interop").is_dir():
            return candidate
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def repo_module(name: str) -> ModuleType:
    """Import one ``automation.interop`` module lazily — a mounted skill has no package root."""
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError:
        raise GateError(
            f"승인 모듈 {name} 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 거부", 3
        ) from None


def _approval_log() -> Path:
    return Path(
        os.environ.get("BUDGET_APPROVAL_LOG", "/srv/autophagy-agents/logs/approvals.jsonl")
    ).expanduser()


def _send_log() -> Path:
    return gate_dir() / "send-log.jsonl"


def gws_bin() -> str:
    override = os.environ.get("BUDGET_GWS_BIN", "")
    if override:
        return override
    found = shutil.which("gws") or os.path.expanduser("~/.local/bin/gws")
    if not Path(found).exists():
        raise GateError("gws CLI를 찾을 수 없습니다 (BUDGET_GWS_BIN 설정 필요)", 3)
    return found


def mail_to() -> str:
    config = Path(os.environ.get("BUDGET_CONFIG", "~/.hermes/budget/config.json")).expanduser()
    try:
        value = json.loads(config.read_text(encoding="utf-8")).get("mail_to")
    except OSError:
        raise GateError(f"budget config 읽기 실패: {config} (mail_to 필요)", 3) from None
    if not isinstance(value, str) or "@" not in value:
        raise GateError("budget config에 유효한 mail_to가 없습니다", 3)
    return value


def _drafts_dir() -> Path:
    path = gate_dir() / "drafts"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _draft_path(draft_id: str) -> Path:
    if not draft_id.isalnum():
        raise GateError(f"잘못된 드래프트 id: {draft_id!r}", 3)
    return _drafts_dir() / f"{draft_id}.json"


def create_draft(
    *, changes: list[budget_core.Change], subject: str, body: str, recipient: str,
    prev_hash: str, new_hash: str, claim_key: str,
    origin_channel_id: str = "", origin_message_id: str = "",
    project: str = "", year: int = 0,
) -> dict:
    draft_id = secrets.token_hex(3)
    while _draft_path(draft_id).exists():
        draft_id = secrets.token_hex(3)
    record = {
        "argv": list(budget_core.build_gmail_argv(recipient, subject, body)),
        "body": body,
        "changes": [[c.item, c.field, c.old, c.new] for c in changes],
        "claim_key": claim_key,
        "created": budget_core.utc_now(),
        "id": draft_id,
        "mail_to": recipient,
        "message_id": "",
        "new_hash": new_hash,
        "origin_channel_id": origin_channel_id,
        "origin_message_id": origin_message_id,
        "prev_hash": prev_hash,
        "project": project,
        "status": "pending",
        "subject": subject,
        "year": year,
    }
    record["sha256"] = budget_core.draft_sha256(record)
    write_json(_draft_path(draft_id), record)
    return record


def load_draft(draft_id: str) -> dict:
    path = _draft_path(draft_id)
    if not path.exists():
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "pending":
        raise GateError(f"드래프트 {draft_id} 상태={record.get('status')} — pending 아님", 1)
    return record


def _binding_fields(binding: ApprovalBindingLike | None) -> dict[str, str | int]:
    """The four record columns one resolved approval binding contributes."""
    if binding is None:
        return {}
    return {
        "kind": str(binding.kind),
        "surface": str(binding.surface),
        "channel_id": str(binding.channel_id),
        "policy_version": int(binding.policy_version),
    }


def stored_binding(draft_id: str) -> dict[str, str | int]:
    """Binding columns already on disk — a stale in-memory draft must never drop them."""
    try:
        record = json.loads(_draft_path(draft_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(record, dict):
        return {}
    return {key: record[key] for key in BINDING_FIELDS if key in record}


def set_message_id(
    draft: dict, message_id: str, binding: ApprovalBindingLike | None = None
) -> dict:
    """Bind one approval message id, persisting the surface it was posted to."""
    updated = {
        **draft,
        **stored_binding(str(draft["id"])),
        **_binding_fields(binding),
        "message_id": message_id,
    }
    write_json(_draft_path(draft["id"]), updated)
    return updated


def discard_draft(draft_id: str) -> None:
    path = _draft_path(draft_id)
    if not path.exists():
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    path.unlink()


def list_drafts() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(_drafts_dir().glob("*.json"))
    ]


def execute_draft(draft: dict, approval: Approval) -> None:
    """Append approval+audit records, run the exact frozen argv, log the send."""
    if budget_core.draft_sha256(draft) != draft["sha256"]:
        raise GateError("드래프트 내용 해시 불일치 — 실행 중단", 1)
    argv = tuple(draft["argv"])
    _append_record(_approval_log(), _approval_record(argv, approval))
    binary = gws_bin()
    result = subprocess.run(  # noqa: S603
        [binary, *argv[1:]], capture_output=True, text=True, timeout=GWS_TIMEOUT_S,
        check=False, cwd=str(gate_dir()),  # gws는 빈 응답을 cwd에 파일로 쓰려 함 (W3-1 gotcha)
    )
    if result.returncode != 0:
        _append_record(_approval_log(), _audit_record(draft, approval, status="failed"))
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise GateError(f"gws gmail 발송 실패 (rc={result.returncode}): {' / '.join(tail)}", 6)
    _append_record(_approval_log(), _audit_record(draft, approval, status="sent"))
    _append_record(_send_log(), {
        "draft_id": draft["id"],
        "mail_to_masked": budget_core.mask_value(draft["mail_to"]),
        "method": approval.method,
        "ref": approval.ref,
        "sha256": draft["sha256"],
        "snapshot": f"{draft['prev_hash'][:12]}->{draft['new_hash'][:12]}",
        "status": "sent",
        "timestamp": budget_core.utc_now(),
    })
    write_json(
        _draft_path(draft["id"]),
        {**draft, "status": "executed", "approval_ref": approval.ref, "method": approval.method},
    )


def _approval_record(argv: tuple[str, ...], approval: Approval) -> dict:
    return {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": approval.ref,
            "method": approval.method,
            "owner_id": approval.owner,
        },
        "hash": budget_core.external_effect_action_hash(argv),
        "result": {"status": "approved"},
        "target_id": budget_core.EXTERNAL_EFFECT_TARGET_ID,
        "timestamp": budget_core.utc_now(),
    }


def _audit_record(draft: dict, approval: Approval, *, status: str) -> dict:
    target_id = f"mail:budget-request:{budget_core.mask_value(draft['mail_to'])}"
    approval_field = {"channel": "approvals", "method": approval.method, "ref": approval.ref}
    canonical = json.dumps(
        {
            "action": "budget.request_mail",
            "approval": approval_field,
            "payload": {"draft_sha256": draft["sha256"]},
            "target_id": target_id,
        },
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    return {
        "action": "budget.request_mail",
        "approval": approval_field,
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        "result": {"status": status},
        "target_id": target_id,
        "timestamp": budget_core.utc_now(),
    }


def _append_record(path: Path, record: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)


def write_json(path: Path, record: dict) -> None:
    """임시 파일에 쓴 뒤 이름을 갈아끼운다 — 독자가 잘린 레코드를 보지 않게.

    제자리 truncate(`write_text`)는 쓰는 동안 읽는 쪽에게 **빈 파일**을 보여준다.
    승인 producer 와 confirm 워처는 같은 레코드를 동시에 만진다(2026-08-01 실측:
    mail 경로에서 producer 가 `JSONDecodeError: ... (char 0)` 으로 사망). 같은 구현이
    여기에도 복제돼 있어 함께 고친다.

    임시 이름은 `.`로 시작하고 `.json` 으로 끝나지 않는다 — 대기 레코드를 훑는
    `*.json` glob 이 쓰다 말은 파일을 레코드로 읽으면 안 되기 때문이다.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", mode="w", encoding="utf-8", delete=False
        ) as handle:
            temporary = Path(handle.name)
            _ = handle.write(serialized)
            # flush 까지만 한다 — 찢어진 읽기를 막는 것은 `os.replace` 이고, fsync 는
            # 호출당 0.12ms 를 3.5ms 로 만든다(실측). 내구성은 PostingJournal 이 맡는다.
            handle.flush()
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
