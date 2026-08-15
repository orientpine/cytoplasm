"""Draft store + approval records + mailon send execution for W4-2 (제약 1/6).

NO reply is ever sent before an owner confirmation (triage_confirm has the two
transports). On confirm the gate appends the external-effect approval record
(hash parity with the deployed pre_tool_call gate's mailon_send rule) plus a
W0-6 audit record to approvals.jsonl, then executes the exact mailon send argv
frozen into the draft at draft time, then appends a send-log line.

Sensitive-draft confinement: drafts whose sensitivity gate hit live under
``~agent/mail/triage-drafts`` (inside the 700 mail home) — never in the
generic gate dir or repository plaintext.

Two CONSECUTIVE approved-send failures downgrade the runtime mail-mode to
no-go (triage_mode, source W4-2-runtime) and every execution re-checks the
effective mode fail-closed first.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import mail_wrapper
import triage_core
import triage_mode
import triage_store
from triage_mode import append_record as _append_record
from triage_mode import gate_dir, write_json

if TYPE_CHECKING:
    import gmail_approval_gate

SEND_TIMEOUT_S = 900
FAILURE_DOWNGRADE_THRESHOLD = 2
GMAIL_APPROVAL_TTL = timedelta(minutes=15)

_NON_SEND_FAILURE_CODES = frozenset({
    "attachment_invalid",
    "attachment_unsupported",
    "attachment_upload_failed",
    "auth_error",
    "confirmation_required",
    "external_service_error",
    "validation_error",
})

_SAFE_FAILURE_MESSAGES = {
    "attachment_invalid": "첨부 파일이 유효하지 않아 발송하지 않았습니다.",
    "attachment_unsupported": "현재 첨부 업로드 기능을 사용할 수 없어 발송하지 않았습니다.",
    "attachment_upload_failed": "첨부 업로드에 실패하여 메일을 발송하지 않았습니다.",
    "auth_error": "메일 서비스 인증에 실패했습니다. 재인증 후 다시 시도하세요.",
    "external_service_error": "메일 서비스 연결에 실패했습니다. 잠시 후 다시 시도하세요.",
    "send_failed": "메일 발송에 실패했습니다. 잠시 후 다시 시도하세요.",
    "send_unverified": "메일 발송 여부를 확인하지 못했습니다. 중복 발송 방지를 위해 보낸메일함을 먼저 확인하세요.",
    "validation_error": "메일 입력값이 유효하지 않습니다. 입력을 확인한 뒤 새 초안을 만드세요.",
}


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


def db_path() -> Path:
    return Path(os.environ.get("TRIAGE_DB", "~/state/mail-triage.db")).expanduser()


def mail_home() -> Path:
    path = Path(os.environ.get("TRIAGE_MAIL_HOME", "~/mail")).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _public_drafts_dir() -> Path:
    path = gate_dir() / "drafts"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _sensitive_drafts_dir() -> Path:
    path = mail_home() / "triage-drafts"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _approval_log() -> Path:
    return Path(
        os.environ.get("TRIAGE_APPROVAL_LOG", "/srv/autophagy-agents/logs/approvals.jsonl")
    ).expanduser()


def _send_log() -> Path:
    return gate_dir() / "send-log.jsonl"


# ---------------------------------------------------------------- drafts

def _draft_path(draft_id: str) -> Path | None:
    if not draft_id.isalnum():
        raise GateError(f"잘못된 드래프트 id: {draft_id!r}", 3)
    for directory in (_public_drafts_dir(), _sensitive_drafts_dir()):
        candidate = directory / f"{draft_id}.json"
        if candidate.exists():
            return candidate
    return None


def create_draft(
    *, uid: str, sender: str, mail_subject: str, to: str, subject: str, body: str,
    sensitive: bool, tags: tuple[str, ...], category: str, flags: tuple[str, ...],
    kind: str = "reply", channel_id: str = "",
    attachment_paths: tuple[str | Path, ...] = (),
    cc: str = "",
) -> dict:
    directory = _sensitive_drafts_dir() if sensitive else _public_drafts_dir()
    draft_id = secrets.token_hex(3)
    while (directory / f"{draft_id}.json").exists():
        draft_id = secrets.token_hex(3)
    attachments = triage_core.build_attachment_manifest(attachment_paths)
    private_paths = tuple(item["source_path_private"] for item in attachments)
    argv = triage_core.build_send_argv(
        mailon_python(), to, subject, body, private_paths, cc
    )
    record = {
        "argv": list(argv),
        "body": body,
        "category": category,
        "cc": cc,
        "channel_id": channel_id,
        "created": triage_core.utc_now(),
        "flags": list(flags),
        "id": draft_id,
        "kind": kind,
        "mail_subject": mail_subject,
        "message_id": "",
        "sender": sender,
        "sender_masked": triage_core.mask_value(sender),
        "sensitive": sensitive,
        "status": "pending",
        "subject": subject,
        "surface": None,
        "tags": list(tags),
        "to": to,
        "uid": uid,
        "uid_opaque": triage_core.mask_value(uid),
        "policy_version": None,
    }
    if attachments:
        record["attachments"] = attachments
        record["attachment_manifest_sha256"] = (
            triage_core.attachment_manifest_sha256(attachments)
        )
    record["sha256"] = triage_core.draft_sha256(record)
    write_json(directory / f"{draft_id}.json", record)
    return record


def create_gmail_draft(snapshot: gmail_approval_gate.GmailApprovalSnapshot) -> dict:
    """Persist a canonical Gmail action for the existing mail approval lifecycle."""
    import gmail_approval_gate

    directory = _public_drafts_dir()
    draft_id = secrets.token_hex(3)
    while (directory / f"{draft_id}.json").exists():
        draft_id = secrets.token_hex(3)
    record = gmail_approval_gate.approval_draft(
        snapshot, draft_id=draft_id, created_at=triage_core.utc_now()
    )
    write_json(directory / f"{draft_id}.json", record)
    return record


def load_draft(draft_id: str) -> dict:
    path = _draft_path(draft_id)
    if path is None:
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "pending":
        raise GateError(f"드래프트 {draft_id} 상태={record.get('status')} — pending 아님", 1)
    return record


def set_approval_binding(
    draft: dict, *, kind: str, surface: str, channel_id: str, policy_version: int,
) -> dict:
    path = _draft_path(draft["id"])
    if path is None:
        raise GateError(f"드래프트 없음: {draft['id']}", 3)
    current = json.loads(path.read_text(encoding="utf-8"))
    current_channel = current.get("channel_id")
    if isinstance(current_channel, str) and current_channel and current_channel != channel_id:
        raise GateError("기존 승인 메시지의 채널 바인딩 변경 거부", 3)
    updated = {
        **current,
        "kind": kind,
        "surface": surface,
        "channel_id": channel_id,
        "policy_version": policy_version,
    }
    write_json(path, updated)
    return updated


def set_message_id(draft: dict, message_id: str, channel_id: str = "") -> dict:
    path = _draft_path(draft["id"])
    if path is None:
        raise GateError(f"드래프트 없음: {draft['id']}", 3)
    current = json.loads(path.read_text(encoding="utf-8"))
    bound_message_id = current.get("message_id")
    if isinstance(bound_message_id, str) and bound_message_id and bound_message_id != message_id:
        raise GateError("기존 승인 메시지 id 교체 거부", 3)
    current_channel = current.get("channel_id")
    if isinstance(current_channel, str) and current_channel and current_channel != channel_id:
        raise GateError("기존 승인 메시지의 채널 바인딩 변경 거부", 3)
    updated = {**current, "message_id": message_id, "channel_id": channel_id}
    write_json(path, updated)
    return updated


def discard_draft(draft_id: str) -> None:
    path = _draft_path(draft_id)
    if path is None:
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    path.unlink()


def list_drafts() -> list[dict]:
    records = []
    for directory in (_public_drafts_dir(), _sensitive_drafts_dir()):
        records += [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]
    return records


def has_draft_for(uid: str) -> bool:
    return any(record.get("uid") == uid for record in list_drafts())


# ------------------------------------------------------------------ send

def mailon_python() -> str:
    override = os.environ.get("TRIAGE_MAILON_PYTHON", "")
    if override:
        return override
    return str(mail_wrapper._cfg()["python"])


def _run_send(argv: tuple[str, ...]) -> tuple[int, str, str]:
    cfg = mail_wrapper._cfg()
    try:
        proc = subprocess.run(
            list(argv), cwd=cfg["repo"] if cfg["repo"].is_dir() else None,
            env=mail_wrapper.build_subprocess_env(cfg),
            capture_output=True, text=True, timeout=SEND_TIMEOUT_S, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return 127, "", f"{type(error).__name__}"
    return proc.returncode, proc.stdout, proc.stderr


def _send_payload(stdout: str) -> dict:
    """Parse the mailon JSON contract even when the subprocess exits non-zero."""
    try:
        payload = triage_core.first_json_object(stdout)
    except triage_core.LlmParseError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_info(payload: dict) -> dict:
    """Normalize safe error metadata; never carry provider/raw message text."""
    error_code = payload.get("error_code")
    stage = payload.get("stage")
    retryable = payload.get("retryable")
    if not isinstance(error_code, str) or not error_code:
        error_code = ""
    if not isinstance(stage, str) or not stage:
        stage = "unknown"
    if not isinstance(retryable, bool):
        retryable = error_code not in _NON_SEND_FAILURE_CODES
    return {"error_code": error_code, "stage": stage, "retryable": retryable}


def _record_draft_failure(draft: dict, error: dict) -> None:
    """Persist only safe retry metadata for the approval watcher."""
    path = _draft_path(draft["id"])
    if path is None:
        return
    status = "pending" if error["retryable"] else "blocked"
    write_json(path, {**draft, "status": status, "last_error": error})


def _safe_failure_message(error: dict) -> str:
    return _SAFE_FAILURE_MESSAGES.get(
        error["error_code"], "메일 발송 처리에 실패했습니다."
    )

def execute_draft(draft: dict, approval: Approval) -> None:
    """Validate approved attachments, execute frozen argv, and verify the result."""
    if triage_core.draft_sha256(draft) != draft["sha256"]:
        raise GateError("드래프트 내용 해시 불일치 — 실행 중단", 1)
    if draft.get("provider") == "gmail":
        _execute_gmail_draft(draft, approval)
        return
    mode = triage_mode.effective_mode()
    if mode != "full-go":
        raise GateError(f"mail-mode={mode} — 실발송 비활성(W4-1N 분기)", 3)
    attachments = draft.get("attachments") or []
    if attachments:
        try:
            triage_core.verify_attachment_manifest(
                attachments, draft.get("attachment_manifest_sha256") or ""
            )
        except triage_core.AttachmentPolicyError as error:
            safe_error = {
                "error_code": error.error_code,
                "stage": "validation",
                "retryable": False,
            }
            _record_draft_failure(draft, safe_error)
            raise GateError(
                f"{_safe_failure_message(safe_error)} (code={error.error_code})", 2
            ) from error

    argv = tuple(draft["argv"])
    _append_record(_approval_log(), _approval_record(argv, approval))
    rc, stdout, _stderr = _run_send(argv)
    payload = _send_payload(stdout)
    status = str(payload.get("status") or ("no-json" if rc == 0 else ""))
    error = _error_info(payload)
    result_matches = True
    if attachments and rc == 0 and status == "submitted":
        expected_manifest = draft.get("attachment_manifest_sha256")
        result_matches = (
            isinstance(expected_manifest, str)
            and payload.get("verified") is True
            and payload.get("attachment_count") == len(attachments)
            and payload.get("attachment_manifest_sha256") == expected_manifest
        )
        if not result_matches:
            error = {
                "error_code": "send_unverified",
                "stage": "verify",
                "retryable": False,
            }

    if rc != 0 or status != "submitted" or not result_matches:
        if not error["error_code"]:
            error = {
                "error_code": "send_failed",
                "stage": error["stage"] if error["stage"] != "unknown" else "send",
                "retryable": True,
            }
        _append_record(
            _approval_log(), _audit_record(draft, approval, status="failed", error=error)
        )
        if error["error_code"] not in _NON_SEND_FAILURE_CODES:
            failures = triage_store.bump_send_failures(db_path())
            if failures >= FAILURE_DOWNGRADE_THRESHOLD:
                triage_mode.downgrade_to_no_go(
                    f"approved mailon send failed {failures} consecutive times (rc={rc})"
                )
        else:
            failures = triage_store.consecutive_send_failures(db_path())
        _record_draft_failure(draft, error)
        exit_code = 2 if error["stage"] == "validation" else 6
        raise GateError(
            f"{_safe_failure_message(error)} "
            f"(error_code={error['error_code']} stage={error['stage']} "
            f"retryable={str(error['retryable']).lower()} consecutive={failures})",
            exit_code,
        )

    triage_store.reset_send_failures(db_path())
    _append_record(_approval_log(), _audit_record(draft, approval, status="sent"))
    send_record = {
        "draft_id": draft["id"],
        "method": approval.method,
        "ref": approval.ref,
        "sensitive": draft["sensitive"],
        "sha256": draft["sha256"],
        "status": "sent",
        "timestamp": triage_core.utc_now(),
        "to_masked": triage_core.mask_value(draft["to"]),
        "uid": draft["uid_opaque"],
    }
    if attachments:
        send_record["attachment_count"] = len(attachments)
        send_record["attachment_manifest_sha256"] = draft["attachment_manifest_sha256"]
    _append_record(_send_log(), send_record)
    path = _draft_path(draft["id"])
    if path is not None:
        write_json(path, {**draft, "status": "executed",
                          "approval_ref": approval.ref, "method": approval.method})


def _execute_gmail_draft(draft: dict, approval: Approval) -> None:
    """Reuse the mail reaction resolver before the Gmail-only final gate."""
    import gmail_approval_gate
    import mail_gmail_send
    import triage_confirm

    if approval.owner != triage_confirm.owner_id():
        raise GateError("Gmail 발송 승인 소유자가 일치하지 않음 — 실행 중단", 1)
    match approval.method:
        case "manual_reaction":
            if approval.ref != f"reaction:{draft['message_id']}":
                raise GateError("Gmail 발송 승인 참조 불일치 — 실행 중단", 1)
            if triage_confirm.resolve_reaction(draft) != triage_confirm.APPROVE_EMOJI:
                raise GateError("Gmail 소유자 승인이 유효하지 않음 — 실행 중단", 1)
            approval_method: gmail_approval_gate.GmailApprovalMethod = "manual_reaction"
        case "signed_injection_e2e":
            if os.environ.get("E2E_TEST_MODE") != "1":
                raise GateError("Gmail 텍스트 승인은 E2E_TEST_MODE에서만 허용", 1)
            approval_method = "signed_injection_e2e"
        case _:
            raise GateError("Gmail 발송 승인 방법이 허용되지 않음 — 실행 중단", 1)
    snapshot = gmail_approval_gate.snapshot_from_draft(draft)
    now = datetime.now(UTC)
    _append_record(
        _approval_log(),
        gmail_approval_gate.approval_record(
            snapshot,
            gmail_approval_gate.OwnerApproval(
                owner_id=approval.owner,
                message_id=draft["message_id"],
                approved_at=now,
                expires_at=now + GMAIL_APPROVAL_TTL,
            ),
            method=approval_method,
        ),
    )
    action = mail_gmail_send.CanonicalMailAction(
        account="gmail",
        argv=snapshot.argv,
        attachment_manifest=tuple(
            mail_gmail_send.AttachmentManifestEntry(
                source_path_private=attachment.source_path_private,
                filename=attachment.filename,
                size_bytes=attachment.size_bytes,
                mime_type=attachment.mime_type,
                sha256=attachment.sha256,
            )
            for attachment in snapshot.attachments
        ),
        attachment_manifest_sha256=snapshot.attachment_manifest_sha256,
    )
    result = gmail_approval_gate.execute_approved_gmail(
        snapshot,
        action,
        gmail_approval_gate.GmailExecutionContext(
            approval_log=_approval_log(),
            owner_id=approval.owner,
            now=now,
            runner=mail_gmail_send.subprocess.run,
            environment=dict(os.environ),
            e2e_test_mode=os.environ.get("E2E_TEST_MODE") == "1",
        ),
    )
    if result.returncode != 0:
        raise GateError("Gmail 발송에 실패했습니다. 재승인이 필요합니다", 6)
    path = _draft_path(draft["id"])
    if path is not None:
        write_json(path, {**draft, "status": "executed", "approval_ref": approval.ref, "method": approval.method})

def _approval_record(argv: tuple[str, ...], approval: Approval) -> dict:
    return {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": approval.ref,
            "method": approval.method,
            "owner_id": approval.owner,
        },
        "hash": triage_core.external_effect_action_hash(argv),
        "result": {"status": "approved"},
        "target_id": triage_core.EXTERNAL_EFFECT_TARGET_ID,
        "timestamp": triage_core.utc_now(),
    }


def _mark_draft_blocked(draft: dict, error_code: str) -> None:
    """Persist a terminal safe state without recording private diagnostics."""
    path = _draft_path(draft["id"])
    if path is not None:
        write_json(path, {**draft, "status": "blocked", "error_code": error_code})


def _audit_record(
    draft: dict, approval: Approval, *, status: str, error: dict | None = None,
) -> dict:
    kind = draft.get("kind", "reply")
    if kind == "compose":
        action = "mail.compose_send"
        target_id = f"mail:compose:{triage_core.mask_value(draft['to'])}"
        channel = "owner-dm" if draft.get("channel_id") else "approvals"
    else:
        action = "mail.reply_send"
        target_id = f"mail:reply:{triage_core.mask_value(draft['to'])}"
        channel = "approvals"
    approval_field = {"channel": channel, "method": approval.method, "ref": approval.ref}
    canonical = json.dumps(
        {
            "action": action,
            "approval": approval_field,
            "payload": {"draft_sha256": draft["sha256"]},
            "target_id": target_id,
        },
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    result = {"status": status}
    if error is not None:
        result.update(error)
    return {
        "action": action,
        "approval": approval_field,
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        "result": result,
        "target_id": target_id,
        "timestamp": triage_core.utc_now(),
    }
