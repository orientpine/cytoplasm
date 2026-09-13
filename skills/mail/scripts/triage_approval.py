"""Mail-triage adapter for the shared owner-approval lifecycle.

EXACTLY ONE live approval message per ``mail:{kind}:{uid}``. The draft field
bound here is ``message_id`` — the DISCORD approval message id, written ONLY by
:meth:`MailApprovalGate.commit`. It is NOT the RFC 5322 ``Message-ID`` of the
mail being answered: that header never reaches a draft record (the answered mail
is identified by ``uid`` / ``uid_opaque``), so the two cannot be confused.

A stored id is never replaced — only superseded (delete BEFORE drop) or left
alone. The repo façade is imported lazily through ``AUTOPHAGY_REPO_ROOT``; an
``ImportError`` refuses the request instead of falling back to an unguarded post.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Final, assert_never

import triage_binding
import triage_core
import triage_gate
import triage_approval_gate

MailApprovalGate = triage_approval_gate.MailApprovalGate
expire_retired_approval = triage_approval_gate.expire_retired_approval

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from automation.entity_preflight.contracts import JsonValue
    from automation.interop.approval_lease import ApprovalLease, PostingJournal
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalRequest,
        Outcome,
        Verdict,
    )

LEASE_DIRNAME = "approval-leases"
JOURNAL_DIRNAME = "posting-journal"
SUPERSEDED_STATUS = "superseded"
#: Discord refuses a longer message body with HTTP 400. The lifecycle reserves the posting
#: journal BEFORE it posts and mail never enriches that reservation with a message id, so a
#: 400 left a key no later attempt could resolve — every retry then refused with
#: POSTING_JOURNAL_STALE and the draft was wedged for good (repair ticket t_82644d12).
_MESSAGE_LIMIT: Final = 2000
_TRANSPORT_ERRORS = (triage_gate.GateError, OSError, json.JSONDecodeError, KeyError, TypeError)


def repo_root() -> Path:
    """The checkout that actually carries ``automation.interop``.

    Same depth-guess trap `mail_preflight.repo_root` already documents: a mounted
    release runs from ``/srv/autophagy-skills/releases/<skill>/<hash>/scripts``, so
    ``parents[3]`` lands on ``.../releases``, which holds no automation package.
    Measured 2026-08-18 — every compose and every watcher tick died with
    ``GATE-REFUSED … AUTOPHAGY_REPO_ROOT=/srv/autophagy-skills/releases``, i.e. the
    approval surface refused itself. Probe the candidates and take the first that
    really holds the package.
    """
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = [*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents")]
    for candidate in candidates:
        if (candidate / "automation" / "interop").is_dir():
            return candidate
    # Name a real, diagnosable location rather than the meaningless depth guess.
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def _repo_module(name: str) -> ModuleType:
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError:
        raise triage_gate.GateError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부", 3
        ) from None


def lifecycle() -> ModuleType:
    """The shared approval façade — refuses the request when the repo is unreachable."""
    return _repo_module("approval_lifecycle")


def _lease_module() -> ModuleType:
    return _repo_module("approval_lease")


def approval_key(draft: dict) -> str:
    """The logical key one live approval message belongs to."""
    kind, uid = draft.get("kind"), draft.get("uid")
    if kind is None:
        kind = "reply"
    if not isinstance(kind, str) or kind not in {"reply", "compose"} or not isinstance(uid, str) or not uid:
        raise triage_gate.GateError("드래프트 kind/uid 누락 — 승인 키를 만들 수 없음", 3)
    return f"mail:{kind}:{uid}"


approval_directory = triage_binding.approval_directory
stored_binding = triage_binding.stored_binding
reaction_instruction = triage_binding.reaction_instruction


def _approval_content(draft: dict, notice: str) -> str:
    """The exact bytes ``MailApprovalGate.post`` would send — rendered in ONE place.

    The pre-flight size check and the post itself must never render differently, or the
    check would clear a message the post cannot deliver.
    """
    content = (
        triage_core.render_approvals_message(
            draft,
            destination=triage_core.ApprovalRenderDestination.OWNER_DM,
            instruction=reaction_instruction(draft),
        )
        + notice
    )
    _refuse_unpostable_content(content)
    return content


_refuse_unpostable_content = triage_approval_gate.refuse_unpostable_content


def post_channel_id(draft: dict) -> str:
    return str(stored_binding(draft).channel_id)


def confirm_intent(draft: dict) -> ApprovalIntent:
    """The intent one approval post is bound to — key, draft digest, channel."""
    digest = draft.get("sha256")
    if not isinstance(digest, str) or not digest:
        raise triage_gate.GateError("드래프트 sha256 누락 — 승인 게시 거부", 3)
    binding = stored_binding(draft, outstanding=_live_requests(draft))
    triage_gate.set_approval_binding(
        draft,
        kind=triage_binding.draft_kind(draft),
        surface=str(binding.surface),
        channel_id=str(binding.channel_id),
        policy_version=int(binding.policy_version),
        approval_thread_id=triage_binding.approval_thread_id(binding),
        approval_guild_id=binding.guild_id,
    )
    return lifecycle().ApprovalIntent(
        key=approval_key(draft), action_hash=_approval_action_hash(draft), channel_id=str(binding.channel_id)
    )


def confirm_lease() -> ApprovalLease:
    return _lease_module().FileKeyLease(triage_gate.gate_dir() / LEASE_DIRNAME)


def posting_journal() -> PostingJournal:
    return _lease_module().PostingJournal(triage_gate.gate_dir() / JOURNAL_DIRNAME)


def _pending_drafts() -> tuple[tuple[Path, dict, str], ...]:
    """(path, record, key) for every pending draft — ANY unreadable record fails closed."""
    records: list[tuple[Path, dict, str]] = []
    directories = (triage_gate._public_drafts_dir(), triage_gate._sensitive_drafts_dir())
    for path in sorted(item for directory in directories for item in directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise lifecycle().ApprovalRecordsError(str(path)) from error
        if not isinstance(data, dict):
            raise lifecycle().ApprovalRecordsError(str(path))
        if data.get("status") != "pending":
            continue
        try:
            records.append((path, data, approval_key(data)))
        except triage_gate.GateError as error:
            raise lifecycle().ApprovalRecordsError(str(path)) from error
    return tuple(records)


def _bound_message_id(record: dict) -> str:
    message_id = record.get("message_id")
    return message_id if isinstance(message_id, str) else ""


def _approval_action_hash(record: dict) -> str:
    """Use Gmail's external-effect hash while preserving legacy mail draft binding."""
    action_hash = record.get("approval_action_hash")
    if record.get("provider") == "gmail":
        if not isinstance(action_hash, str) or not action_hash.startswith("sha256:"):
            raise triage_gate.GateError("Gmail action hash 누락 — 승인 게시 거부", 3)
        return action_hash
    return str(record["sha256"])


def _request_channel_id(record: dict) -> str:
    return str(stored_binding(record).channel_id)


def request_of(record: dict) -> ApprovalRequest:
    """Project one persisted mail draft onto the shared lifecycle identity."""
    message_id = _bound_message_id(record)
    if not message_id:
        raise triage_gate.GateError("승인 메시지 바인딩 누락", 3)
    return lifecycle().ApprovalRequest(
        key=approval_key(record),
        action_hash=_approval_action_hash(record),
        message_id=message_id,
        channel_id=_request_channel_id(record),
        created_at=str(record.get("approval_created_at", record["created"])),
    )


def _live_requests(draft: dict) -> tuple[ApprovalRequest, ...]:
    """This key's live requests, read exactly as the gate's ``outstanding`` reads them.

    재사용 판정에만 쓰는 기회적 읽기다: 읽을 수 없는 저장소를 여기서 판정하지 않는다 —
    파사드가 곧바로 같은 읽기를 다시 하고 store-unreadable 로 거부한다.
    """
    try:
        return MailApprovalGate(draft).outstanding(approval_key(draft))
    except (lifecycle().ApprovalRecordsError, triage_gate.GateError):
        return ()


def _prepare_request(draft: dict[str, JsonValue], notice: str) -> tuple[ApprovalIntent, MailApprovalGate]:
    """Prepare final bytes before resolving a new surface."""
    cards = _repo_module("approval_card")
    version = draft.get("render_version", "1" if draft.get("message_id") else None)
    try:
        card = cards.prepare(
            lambda selected: _approval_content({**draft, "render_version": selected}, notice), version,
        )
    except cards.CardRenderError as error:
        raise triage_gate.GateError(str(error), 3) from error
    prepared = {**draft, "render_version": card.render_version}
    return confirm_intent(prepared), MailApprovalGate(prepared, notice, card.content)


def request_approval(draft: dict, *, notice: str = "") -> Verdict:
    """Resolve existing bindings under the lease before any card preparation."""
    facade = lifecycle()
    key = approval_key(draft)
    live = _live_requests(draft)
    journal = posting_journal()
    channel = live[0].channel_id if live else (journal.outstanding(key) or {}).get("channel_id", "")
    intent = facade.ApprovalIntent(key, _approval_action_hash(draft), channel)
    return facade.request_owner_approval(
        intent, MailApprovalGate(draft, notice), confirm_lease(), journal,
        prepare=lambda: _prepare_request(draft, notice),
    )


def _refusal(verdict: Verdict) -> triage_gate.GateError:
    reason = verdict.reason.value if verdict.reason is not None else "unknown"
    exit_code = 3 if reason in {"store-unreadable", "posting-journal-stale"} else 1
    return triage_gate.GateError(
        f"승인 메시지를 게시하지 않았습니다 ({verdict.outcome.value}:{reason})"
        " — 기존 승인 메시지가 유효합니다", exit_code,
    )


def _owns(request: ApprovalRequest, draft: dict) -> bool:
    """True iff the live request is bound to THIS draft record, never a sibling."""
    binding = (request.action_hash, request.message_id)
    try:
        records = _pending_drafts()
    except lifecycle().ApprovalRecordsError:
        return False
    return any(
        str(record["id"]) == str(draft["id"])
        and (_approval_action_hash(record), _bound_message_id(record)) == binding
        for _, record, _ in records
    )


def bound_message_id(verdict: Verdict, draft: dict) -> str:
    """Map one lifecycle verdict onto the legacy ``_post_draft_for_approval`` contract."""
    outcome: type[Outcome] = lifecycle().Outcome
    match verdict.outcome:
        case outcome.POSTED:
            posted = verdict.posted
            if posted is None:
                raise triage_gate.GateError("승인 게시 결과가 비어 있음 — 거부", 3)
            return posted.message_id
        case outcome.PENDING:
            live = verdict.live
            if live is None or not _owns(live, draft):
                raise triage_gate.GateError(
                    "다른 초안이 이 키의 승인 메시지를 보유 중 — 게시 거부", 1
                )
            return live.message_id
        case outcome.DEFERRED | outcome.REFUSED:
            raise _refusal(verdict)
        case unreachable:
            assert_never(unreachable)


def post_for_approval(draft: dict, *, notice: str = "") -> str:
    """Producer entry point — one guarded post/reaction/bind sequence per key."""
    return bound_message_id(request_approval(draft, notice=notice), draft)
