"""Wiki confirm-gate adapter for the shared owner-approval lifecycle.

EXACTLY ONE live confirm message per ``wiki:{action}:{slug}``. The draft's
``confirm_message_id`` is written ONLY by :meth:`WikiApprovalGate.commit`; a stored id is
never replaced, only superseded (delete BEFORE drop) or left alone. The repo façade is
imported lazily through ``AUTOPHAGY_REPO_ROOT`` — an ``ImportError`` refuses the request
instead of falling back to an unguarded post (fail-closed).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Literal, assert_never
from urllib.error import HTTPError

import wiki_gate
import wiki_binding

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalRequest,
        PostedApproval,
        Probe,
Verdict,
    )
    from automation.interop.approval_lease import ApprovalLease, PostingJournal

LEASE_DIRNAME = "approval-leases"
JOURNAL_DIRNAME = "posting-journal"
_TRANSPORT_ERRORS = (OSError, json.JSONDecodeError, KeyError, TypeError, wiki_gate.GateError)


def repo_root() -> Path:
    default = Path(__file__).resolve().parents[3]
    return Path(os.environ.get("AUTOPHAGY_REPO_ROOT", str(default))).expanduser()


def _repo_module(name: str) -> ModuleType:
    root = repo_root()
    sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError:
        raise wiki_gate.GateError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부", 3
        ) from None


def lifecycle() -> ModuleType:
    """The shared approval façade — refuses the request when the repo is unreachable."""
    return _repo_module("approval_lifecycle")


def _lease_module() -> ModuleType:
    return _repo_module("approval_lease")


def approval_key(draft: dict) -> str:
    """The logical key one live confirm message belongs to."""
    action = draft.get("action")
    slug = draft.get("slug")
    if not isinstance(action, str) or not isinstance(slug, str) or not action or not slug:
        raise wiki_gate.GateError("드래프트 action/slug 누락 — 승인 키를 만들 수 없음", 3)
    return f"wiki:{action}:{slug}"


def confirm_intent(
    draft: dict, binding: wiki_binding.ApprovalBindingLike | None = None
) -> ApprovalIntent:
    """The intent one confirm post is bound to — key, draft digest, approval channel."""
    digest = draft.get("sha256")
    if not isinstance(digest, str) or not digest:
        raise wiki_gate.GateError("드래프트 sha256 누락 — 승인 게시 거부", 3)
    resolved = binding or wiki_binding.stored_binding(draft)
    return lifecycle().ApprovalIntent(
        key=approval_key(draft),
        action_hash=digest,
        channel_id=resolved.channel_id,
    )


def confirm_lease() -> ApprovalLease:
    return _lease_module().FileKeyLease(wiki_gate.GATE_DIR / LEASE_DIRNAME)


def posting_journal() -> PostingJournal:
    return _lease_module().PostingJournal(wiki_gate.GATE_DIR / JOURNAL_DIRNAME)


def _pending_drafts() -> tuple[tuple[Path, dict, str], ...]:
    """(path, record, key) for every pending draft — ANY unreadable record fails closed."""
    records: list[tuple[Path, dict, str]] = []
    directory = wiki_gate.GATE_DIR / "drafts"
    if not directory.is_dir():
        return ()
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise lifecycle().ApprovalRecordsError(str(path)) from error
        if not isinstance(data, dict):
            raise lifecycle().ApprovalRecordsError(str(path))
        if data.get("status") != "pending":
            continue
        try:
            key = approval_key(data)
        except wiki_gate.GateError as error:
            raise lifecycle().ApprovalRecordsError(str(path)) from error
        records.append((path, data, key))
    return tuple(records)


def _bound_message_id(record: dict) -> str:
    message_id = record.get("confirm_message_id")
    return message_id if isinstance(message_id, str) else ""


@dataclass(frozen=True, slots=True)
class WikiApprovalGate:
    """``approval_lifecycle.ApprovalGate`` over the wiki draft store + its Discord REST."""

    draft: dict
    binding: wiki_binding.ApprovalBindingLike | None = None

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        request_type = lifecycle().ApprovalRequest
        requests: list[ApprovalRequest] = []
        for _, record, record_key in _pending_drafts():
            if record_key != key or not _bound_message_id(record):
                continue
            channel_id = wiki_binding.persisted_channel_id(record)
            if channel_id is None:
                raise wiki_gate.GateError("저장된 승인 바인딩이 불완전함 — 승인 거부", 1)
            requests.append(
                request_type(
                    key=key,
                    action_hash=str(record["sha256"]),
                    message_id=_bound_message_id(record),
                    channel_id=channel_id,
                    created_at=str(record["created"]),
                )
            )
        return tuple(requests)

    def probe(self, request: ApprovalRequest) -> Probe:
        state = lifecycle().Probe
        try:
            message = wiki_gate._confirm_message(request.channel_id, request.message_id)
            if message is None:
                return state.MISSING
            content = str(message.get("content", ""))
            if not content:
                return state.MISSING
            if request.action_hash not in content:
                return state.BINDING_MISMATCH
            owner = wiki_gate.owner_id()
            channel, message_id = request.channel_id, request.message_id
            cancelled = wiki_gate._reaction_users(channel, message_id, wiki_gate.CANCEL_EMOJI)
            approved = wiki_gate._reaction_users(channel, message_id, wiki_gate.APPROVE_EMOJI)
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        if wiki_gate._owner_reacted(cancelled, owner):
            return state.CANCELLED
        if wiki_gate._owner_reacted(approved, owner):
            return state.APPROVED
        return state.BOUND_PENDING

    def delete(self, request: ApprovalRequest) -> None:
        path = f"/channels/{request.channel_id}/messages/{request.message_id}"
        try:
            wiki_gate._api("DELETE", path)
        except HTTPError as error:
            if error.code != 404:
                raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error

    def edit_settled(
        self,
        channel_id: str,
        message_id: str,
        action_hash: str,
        suffix: str,
    ) -> Literal["edited", "already-settled", "missing", "binding-mismatch"]:
        """Append a settlement suffix while preserving the bound approval content."""
        try:
            message = wiki_gate._confirm_message(channel_id, message_id)
            if message is None:
                return "missing"
            content = str(message.get("content", ""))
            if action_hash not in content:
                return "binding-mismatch"
            if content.endswith(suffix):
                return "already-settled"
            wiki_gate._api(
                "PATCH",
                f"/channels/{channel_id}/messages/{message_id}",
                {"content": content + suffix},
            )
        except HTTPError as error:
            if error.code == 404:
                return "missing"
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        except _TRANSPORT_ERRORS as error:
            raise lifecycle().ApprovalSurfaceError(str(error)) from error
        return "edited"

    def drop(self, request: ApprovalRequest) -> None:
        """Compare-and-swap: unbind the record ONLY while it still holds this exact message."""
        bound = (request.action_hash, request.message_id)
        for path, record, key in _pending_drafts():
            if key != request.key or (str(record["sha256"]), _bound_message_id(record)) != bound:
                continue
            wiki_gate._write_json(
                path, {name: value for name, value in record.items() if name != "confirm_message_id"}
            )
            return

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        channel_id = intent.channel_id
        # 게시 시점의 draft에는 아직 surface가 없다(commit이 쓴다) — 바인딩이 유일한 출처.
        surface = str(self._binding_for(intent).surface)
        message = wiki_gate._api(
            "POST",
            f"/channels/{channel_id}/messages",
            {"content": wiki_gate.confirm_text(self.draft, surface=surface)},
        )
        if not isinstance(message, dict) or not isinstance(message.get("id"), str):
            raise wiki_gate.GateError("승인 메시지 게시 응답이 유효하지 않음 — 거부", 3)
        message_id = str(message["id"])
        wiki_gate._add_reaction(channel_id, message_id, wiki_gate.APPROVE_EMOJI)
        wiki_gate._add_reaction(channel_id, message_id, wiki_gate.CANCEL_EMOJI)
        return lifecycle().PostedApproval(message_id=message_id, channel_id=channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        """The ONLY writer of ``confirm_message_id`` in the wiki skill."""
        binding = self._binding_for(intent)
        if posted.channel_id != binding.channel_id:
            raise lifecycle().ApprovalRecordsError("wiki approval channel binding changed")
        updated = {
            **self.draft,
            "channel_id": binding.channel_id,
            "confirm_message_id": posted.message_id,
            "kind": str(binding.kind),
            "policy_version": binding.policy_version,
            "surface": str(binding.surface),
        }
        wiki_gate._write_json(wiki_gate._draft_path(str(updated["id"])), updated)

    def _binding_for(self, intent: ApprovalIntent) -> wiki_binding.ApprovalBindingLike:
        binding = self.binding or wiki_binding.stored_binding(self.draft)
        if intent.channel_id != binding.channel_id:
            raise lifecycle().ApprovalSurfaceError("wiki approval intent binding changed")
        return binding


def _refusal(verdict: Verdict) -> wiki_gate.GateError:
    reason = verdict.reason.value if verdict.reason is not None else "unknown"
    exit_code = 3 if reason in {"store-unreadable", "posting-journal-stale"} else 1
    return wiki_gate.GateError(
        f"승인 메시지를 게시하지 않았습니다 ({verdict.outcome.value}:{reason})"
        " — 기존 승인 메시지가 유효합니다", exit_code
    )


def apply_verdict(verdict: Verdict, draft: dict) -> dict:
    """Map one lifecycle verdict onto the legacy ``post_confirm_message`` return contract."""
    outcome = lifecycle().Outcome
    match verdict.outcome:
        case outcome.POSTED:
            posted = verdict.posted
            if posted is None:
                raise wiki_gate.GateError("승인 게시 결과가 비어 있음 — 거부", 3)
            return {**draft, "confirm_message_id": posted.message_id}
        case outcome.PENDING:
            live = verdict.live
            if live is None:
                raise wiki_gate.GateError("기존 승인 메시지 정보를 읽지 못함 — 거부", 3)
            return {**draft, "confirm_message_id": live.message_id}
        case outcome.DEFERRED | outcome.REFUSED:
            raise _refusal(verdict)
        case unreachable:
            assert_never(unreachable)
