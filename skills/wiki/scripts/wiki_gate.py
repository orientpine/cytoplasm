"""Draft → owner-confirm → save gate for the personal wiki (W2-2, constraint 1).

NOTHING is ever written into WIKI_ROOT before an owner confirmation:
- production: cha's own DM reply ``저장 <draft-id>`` is independently verified
  through Discord REST (author must equal the interop owner_id, bots rejected);
- unattended E2E: an HMAC-signed injected event (W1-6 injection adapter) under
  ``E2E_TEST_MODE=1``. The production gateway refuses that env at boot (W1-6).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import wiki_store

GATE_DIR = Path(os.environ.get("WIKI_GATE_DIR", "~/.hermes/wiki-gate")).expanduser()
INTEROP_CONFIG = Path(
    os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")
).expanduser()
INTEROP_RUNTIME = Path(
    os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")
).expanduser()
API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
DM_SCAN_LIMIT = 50
APPROVE_EMOJI = "\u2705"
CANCEL_EMOJI = "\u26d4"
CONFIRM_MAX_CHARS = 1900  # Discord 2000자 상한 아래의 안전 여유


def _wiki_root() -> Path:
    return Path(os.environ.get("WIKI_ROOT", "~/wiki")).expanduser()


class GateError(RuntimeError):
    """Gate refusal with a CLI exit code (1 unconfirmed, 2 schema, 3 config)."""

    def __init__(self, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _drafts_dir() -> Path:
    GATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = GATE_DIR / "drafts"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _draft_path(draft_id: str) -> Path:
    if not draft_id.isalnum():
        raise GateError(f"잘못된 드래프트 id: {draft_id!r}", 3)
    return _drafts_dir() / f"{draft_id}.json"


def create_draft(
    action: str,
    slug: str,
    note_text: str,
    channel_id: str,
    *,
    summary: str | None = None,
) -> dict:
    wiki_store.parse_note(note_text)  # raises SchemaError before anything persists
    draft_id = secrets.token_hex(3)
    while _draft_path(draft_id).exists():
        draft_id = secrets.token_hex(3)
    record = {
        "action": action,
        "channel_id": channel_id,
        "created": wiki_store.utc_now(),
        "id": draft_id,
        "note_text": note_text,
        "sha256": hashlib.sha256(note_text.encode("utf-8")).hexdigest(),
        "slug": slug,
        "status": "pending",
    }
    # 요약은 선택 사항 — 없으면 레코드 모양은 예전과 완전히 동일하다.
    if isinstance(summary, str) and summary:
        record["summary"] = summary
    _write_json(_draft_path(draft_id), record)
    return record


def load_draft(draft_id: str) -> dict:
    path = _draft_path(draft_id)
    if not path.exists():
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "pending":
        raise GateError(f"드래프트 {draft_id} 상태={record.get('status')} — pending 아님", 1)
    return record


def discard_draft(draft_id: str) -> None:
    path = _draft_path(draft_id)
    if not path.exists():
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    path.unlink()


def list_drafts() -> list[dict]:
    if not (_drafts_dir()).is_dir():
        return []
    records = []
    for path in sorted(_drafts_dir().glob("*.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def _owner_dm_surface() -> str | None:
    """공유 enum의 owner-DM 표면 값. 해석 불가하면 None — 요약을 생략할 뿐 게시는 막지 않는다."""
    try:
        import wiki_binding

        surface = wiki_binding._repo_module("approval_surface").ApprovalSurface.OWNER_DM
    except (ImportError, AttributeError, GateError):
        return None
    return str(surface)


def confirm_text(draft: dict, *, surface: str | None = None) -> str:
    """레거시 한 줄이 정본. 요약은 owner-dm 표면에서, 명시적으로 주어졌을 때만 덧붙인다.

    위키 본문·제목은 그 표면 밖으로 나가면 안 되므로(skills/AGENTS.md), 표면을
    확인할 수 없으면 요약을 생략한다(fail-closed).
    """
    legacy = f"저장 {draft['id']} sha256:{draft['sha256']}"
    summary = draft.get("summary")
    if not isinstance(summary, str) or not summary:
        return legacy
    effective_surface = surface if surface is not None else draft.get("surface")
    owner_dm = _owner_dm_surface()
    if owner_dm is None or effective_surface != owner_dm:
        return legacy
    budget = CONFIRM_MAX_CHARS - len(legacy) - 1  # 개행 1자
    if budget <= 0:
        return legacy
    if len(summary) > budget:
        summary = summary[: budget - 1] + "…"
    return f"{legacy}\n{summary}"


def post_confirm_message(draft: dict) -> dict:
    """Keep EXACTLY ONE live confirm message per approval key — a stored
    ``confirm_message_id`` is never replaced, only superseded (delete BEFORE drop)."""
    import wiki_approval  # deferred: wiki_approval imports this module
    import wiki_binding

    facade = wiki_approval.lifecycle()
    binding = wiki_binding.stored_binding(draft)
    verdict = facade.request_owner_approval(
        wiki_approval.confirm_intent(draft, binding),
        wiki_approval.WikiApprovalGate(draft=draft, binding=binding),
        wiki_approval.confirm_lease(),
        wiki_approval.posting_journal(),
    )
    return wiki_approval.apply_verdict(verdict, draft)


def _add_reaction(channel_id: str, message_id: str, emoji: str) -> None:
    _api(
        "PUT",
        f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me",
    )


def owner_id() -> str:
    try:
        owner = json.loads(INTEROP_CONFIG.read_text(encoding="utf-8")).get("owner_id")
    except OSError:
        raise GateError(f"interop config 읽기 실패: {INTEROP_CONFIG}", 3) from None
    if not isinstance(owner, str) or not owner:
        raise GateError("interop config에 owner_id가 없습니다", 3)
    return owner


def _adapter() -> Any:
    sys.path.insert(0, str(INTEROP_RUNTIME))
    try:
        from automation.interop import injection_adapter
    except ImportError:
        raise GateError(f"injection adapter 불가 (INTEROP_RUNTIME={INTEROP_RUNTIME})", 3) from None
    return injection_adapter


def _require_e2e_secret() -> str:
    if os.environ.get("E2E_TEST_MODE") != "1":
        raise GateError("주입 경로는 E2E_TEST_MODE=1 전용입니다 (프로덕션 게이트웨이 금지)", 1)
    secret = os.environ.get("INTEROP_E2E_SECRET", "")
    if not secret:
        raise GateError("INTEROP_E2E_SECRET 누락", 1)
    return secret


def _persisted_injection_channel_id(draft: dict) -> str:
    import wiki_binding

    channel_id = wiki_binding.persisted_channel_id(draft)
    if channel_id is None:
        raise GateError("주입 승인에는 저장된 승인 바인딩이 필요함 — 거부", 1)
    return channel_id


def confirm_via_injection(draft: dict, injection_path: Path) -> str:
    secret = _require_e2e_secret()
    adapter = _adapter()
    envelope = json.loads(injection_path.read_text(encoding="utf-8"))
    event = adapter.InboundEvent(
        event_id=str(envelope["event"]["event_id"]),
        user_id=str(envelope["event"]["user_id"]),
        channel_id=str(envelope["event"]["channel_id"]),
        text=str(envelope["event"]["text"]),
    )
    if not adapter.accept_test_event(
        event, str(envelope["signature"]), secret.encode("utf-8"), e2e_test_mode=True
    ):
        raise GateError("주입 승인 서명 불일치 — 거부", 1)
    if event.user_id != owner_id():
        raise GateError("주입 승인 발신자가 소유자가 아님 — 거부", 1)
    if event.channel_id != _persisted_injection_channel_id(draft):
        raise GateError("주입 승인 채널 불일치 — 거부", 1)
    if event.text != confirm_text(draft):
        raise GateError("주입 승인 텍스트/해시 불일치 — 거부", 1)
    reaction_emoji = envelope["event"].get("reaction_emoji")
    if reaction_emoji is not None:
        if reaction_emoji == APPROVE_EMOJI:
            return "injected-reaction:approve"
        if reaction_emoji == CANCEL_EMOJI:
            raise GateError("주입 취소 리액션으로 취소됨 — 저장하지 않습니다", 1)
        raise GateError("주입 리액션 값이 승인/취소가 아님 — 거부", 1)
    return f"injected:{event.event_id}"


def sign_injection(
    draft: dict,
    out_path: Path,
    user_id: str | None,
    channel_id: str | None,
    forge_signature: bool,
    reaction_emoji: str | None = None,
) -> None:
    secret = _require_e2e_secret()
    adapter = _adapter()
    event = adapter.InboundEvent(
        event_id=str(uuid.uuid4()),
        user_id=user_id or owner_id(),
        channel_id=channel_id or _persisted_injection_channel_id(draft),
        text=confirm_text(draft),
    )
    signature = "0" * 64 if forge_signature else adapter.sign_event(event, secret.encode("utf-8"))
    _write_json(
        out_path,
        {
            "event": {
                "event_id": event.event_id,
                "user_id": event.user_id,
                "channel_id": event.channel_id,
                "text": event.text,
                **({"reaction_emoji": reaction_emoji} if reaction_emoji is not None else {}),
            },
            "signature": signature,
        },
    )


#: 배포 게이트(`automation/skill_gate.py`)와 같은 값. 소유자의 결정을 읽는 경로는
#: 429 를 존중해야 한다 — 2026-08-03 실측으로 이 게이트가 429 에 죽으면서 소유자가
#: 이미 누른 ⛔ 4건이 반영되지 않은 채 "아직 안 누름"과 구분되지 않았다.
_RATE_LIMIT_ATTEMPTS = 5
_RATE_LIMIT_FALLBACK_SECONDS = 1.0


def _retry_after(error: HTTPError) -> float:
    """Discord 가 요청한 대기 시간. 값이 없거나 읽을 수 없어도 백오프는 한다."""
    value = error.headers.get("Retry-After") if error.headers is not None else None
    try:
        return max(float(value), 0.0) if value is not None else _RATE_LIMIT_FALLBACK_SECONDS
    except (TypeError, ValueError):
        return _RATE_LIMIT_FALLBACK_SECONDS


def _send(request: Request) -> Any:
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _api(method: str, path: str, payload: dict | None = None) -> Any:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise GateError("DISCORD_BOT_TOKEN 누락 — 프로덕션 확인 경로 사용 불가", 3)
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    for _ in range(_RATE_LIMIT_ATTEMPTS - 1):
        try:
            return _send(request)
        except HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(_retry_after(error))
    return _send(request)  # 마지막 시도는 실패해도 그대로 올린다


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def confirm_via_owner_scan(draft: dict) -> str:
    owner = owner_id()
    import wiki_binding

    channel_id = wiki_binding.approval_directory().owner_dm()
    messages = _api("GET", f"/channels/{channel_id}/messages?limit={DM_SCAN_LIMIT}")
    accepted = {f"저장 {draft['id']}", confirm_text(draft)}
    for message in messages:
        author = message.get("author", {})
        if str(author.get("id", "")) != owner or bool(author.get("bot", False)):
            continue
        if str(message.get("content", "")).strip() not in accepted:
            continue
        if _parse_ts(str(message["timestamp"])) < _parse_ts(draft["created"]):
            continue
        return f"dm:{message['id']}"
    raise GateError(
        f"소유자의 '저장 {draft['id']}' DM 확인을 찾지 못함 — 저장하지 않습니다", 1
    )


def resolve_reaction(draft: dict) -> Path | None:
    """Resolve one bound owner reaction; missing/unreacted messages stay pending."""
    channel_id = _draft_channel_id(draft)
    message_id = _confirm_message_id(draft)
    if not message_id:
        return None
    _verify_draft_hash(draft)
    message = _confirm_message(channel_id, message_id)
    if message is None:
        return None
    _verify_message_binding(message, channel_id, draft["sha256"])
    owner = owner_id()
    if _owner_reacted(_reaction_users(channel_id, message_id, CANCEL_EMOJI), owner):
        discard_draft(draft["id"])
        raise GateError(f"소유자의 {CANCEL_EMOJI} 리액션으로 취소됨 — 저장하지 않습니다", 1)
    if _owner_reacted(_reaction_users(channel_id, message_id, APPROVE_EMOJI), owner):
        return apply_draft(_wiki_root(), draft, f"reaction:{message_id}", "reaction")
    return None


def _draft_channel_id(draft: dict) -> str:
    import wiki_binding

    return wiki_binding.stored_binding(draft).channel_id


def _confirm_message_id(draft: dict) -> str:
    message_id = draft.get("confirm_message_id")
    if message_id is None:
        message_id = draft.get("message_id", "")
    if not isinstance(message_id, str):
        raise GateError("승인 메시지 id가 유효하지 않음 — 거부", 1)
    return message_id


def _verify_draft_hash(draft: dict) -> None:
    note_text = draft.get("note_text")
    digest = draft.get("sha256")
    if not isinstance(note_text, str) or not isinstance(digest, str):
        raise GateError("드래프트 해시 필드가 유효하지 않음 — 거부", 1)
    if hashlib.sha256(note_text.encode("utf-8")).hexdigest() != digest:
        raise GateError("드래프트 내용 해시 불일치 — 저장 중단", 1)


def _confirm_message(channel_id: str, message_id: str) -> dict | None:
    try:
        message = _api("GET", f"/channels/{channel_id}/messages/{message_id}")
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    if not isinstance(message, dict):
        raise GateError("승인 메시지 응답이 유효하지 않음 — 거부", 1)
    return message


def _verify_message_binding(message: dict, channel_id: str, digest: str) -> None:
    message_channel = message.get("channel_id")
    if message_channel is not None and str(message_channel) != channel_id:
        raise GateError("승인 메시지 채널 바인딩 불일치 — 거부", 1)
    if digest not in str(message.get("content", "")):
        raise GateError("승인 메시지가 이 드래프트 해시를 참조하지 않음 — 거부", 1)


def _owner_reacted(users: list[dict], owner: str) -> bool:
    return any(str(user.get("id", "")) == owner and not bool(user.get("bot", False)) for user in users)


def _reaction_users(channel_id: str, message_id: str, emoji: str) -> list[dict]:
    try:
        users = _api(
            "GET",
            f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}?limit=100",
        )
    except HTTPError as error:
        if error.code == 404:
            return []
        raise
    if not isinstance(users, list) or not all(isinstance(user, dict) for user in users):
        raise GateError("승인 리액션 응답이 유효하지 않음 — 거부", 1)
    return users


def apply_draft(wiki_root: Path, draft: dict, approval_ref: str, method: str) -> Path:
    import wiki_binding

    channel_id = wiki_binding.persisted_channel_id(draft)
    if channel_id is None:
        draft_id = draft.get("id")
        if isinstance(draft_id, str):
            draft = load_draft(draft_id)
            channel_id = wiki_binding.persisted_channel_id(draft)
    if channel_id is None:
        raise GateError("저장에는 저장된 승인 바인딩이 필요함 — 거부", 1)
    draft = {
        **draft,
        "channel_id": channel_id,
        "kind": draft["kind"],
        "policy_version": draft["policy_version"],
        "surface": draft["surface"],
    }
    note_text = draft["note_text"]
    if hashlib.sha256(note_text.encode("utf-8")).hexdigest() != draft["sha256"]:
        raise GateError("드래프트 내용 해시 불일치 — 저장 중단", 1)
    wiki_store.parse_note(note_text)  # re-validate at save time (fail-closed)
    wiki_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(wiki_root, 0o700)
    path = wiki_store.note_path(wiki_root, draft["slug"])
    if draft["action"] == "create" and path.exists():
        raise GateError(f"이미 존재하는 노트: {draft['slug']} (수정은 --edit 사용)", 2)
    if draft["action"] == "edit" and not path.exists():
        raise GateError(f"수정 대상 노트 없음: {draft['slug']}", 2)
    path.write_text(note_text, encoding="utf-8")
    path.chmod(0o600)
    draft = {**draft, "status": "saved", "approval_ref": approval_ref, "method": method}
    _write_json(_draft_path(draft["id"]), draft)
    _append_audit(draft, approval_ref, method)
    return path


def _append_audit(draft: dict, approval_ref: str, method: str) -> None:
    action = f"wiki.{draft['action']}"
    audit_method = "dm_text" if method == "owner_dm_reply" else method
    payload = {
        "action": action,
        "approval": {"channel": draft.get("channel_id", "dm"), "method": audit_method, "ref": approval_ref},
        "payload": {"note_sha256": draft["sha256"]},
        "target_id": f"note:{draft['slug']}",
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    record = {
        "action": action,
        "approval": payload["approval"],
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        "result": {"status": "saved"},
        "target_id": f"note:{draft['slug']}",
        "timestamp": wiki_store.utc_now(),
    }
    audit = GATE_DIR / "audit.jsonl"
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    audit.chmod(0o600)


def _write_json(path: Path, record: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
