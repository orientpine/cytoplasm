from __future__ import annotations

import os
import stat
from dataclasses import replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import ApprovalRequest, Probe
from automation.interop.approval_surface import (
    ApprovalKind,
    RequestThread,
    resolve_new_binding,
    reuse_request_thread,
)
from automation.interop.reaction_approval import (
    DiscordTransport,
    DiscordTransportError,
    record_push_approval,
    thread_candidates,
)
from automation.memory_curator.binding import entry_digest
from automation.memory_curator.watch_steps import read_native
from automation.obsidian_write.config import ObsidianWriteError, load_config
from automation.interop.external_effect_gate import ApprovalContext
from automation.obsidian_write import gate_binding
from automation.obsidian_write.writer import write_note

from .approval_gate import RelocateApprovalGate, request_approval
from .apply import ApplyDeps, ApplyOutcome, apply_relocation
from .binding import RelocationHashFields, relocation_action_hash
from .model import RelocationRecord, record_key
from .plan import RelocationPlan, build_relocation_plan
from .rag_verify import verify_ingested
from .relocation_store import RelocationStore, RelocationStoreError
from .watch_step import ResolveEffects

_EFFECT_ERRORS: Final = (OSError, ValueError, RuntimeError, ObsidianWriteError)

#: Re-exported for the callers and tests that reached for them here while this module
#: still owned the transport; ``automation.interop.reaction_approval`` is the source.
__all__ = [
    "DiscordTransport",
    "DiscordTransportError",
    "RelocationStore",
    "RelocationStoreError",
    "build_effects",
    "recover_entry_text",
    "record_push_approval",
]



def recover_entry_text(memory_dir: Path, record: RelocationRecord) -> str | None:
    """Return the exact current native entry only when its bound digest still matches."""
    _, memory_file = read_native(memory_dir, record.source_kind)
    return next((entry.text for entry in memory_file.entries if entry_digest(record.source_kind, entry.text) == record.entry_sha256), None)


def _safe_read_note(clone_dir: Path, note_relpath: str) -> bytes | None:
    relpath = PurePosixPath(note_relpath)
    if relpath.is_absolute() or ".." in relpath.parts:
        return None
    try:
        descriptor = os.open(clone_dir / Path(relpath), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    except OSError:
        return None
    finally:
        os.close(descriptor)


def build_effects(*, memory_dir: Path, state_path: Path, rag_state_path: Path, token: str, owner_id: str, now: datetime) -> ResolveEffects:
    """Assemble the production retrying effects without bypassing any approval gate."""
    store = RelocationStore(state_path)
    transport = DiscordTransport(token, owner_id)
    directory = DiscordChannelDirectory(token, owner_id, transport.api, state_path.parent / "approval-directory.json")
    lease = FileKeyLease(state_path.parent / "approval-leases")
    journal = PostingJournal(state_path.parent / "posting-journal")
    write_config = load_config()
    push_approval_log = state_path.parent / "relocate-push-approvals.jsonl"

    def plan_for(record: RelocationRecord) -> RelocationPlan | None:
        try:
            text = recover_entry_text(memory_dir, record)
            return None if text is None else build_relocation_plan(text, source_kind=record.source_kind)
        except _EFFECT_ERRORS:
            return None

    def live_requests(key: str) -> tuple[RelocationRecord, ...]:
        """The records whose approval message is live for this key — the gate's own read."""
        try:
            return tuple(
                pending
                for pending in store.all_pending()
                if record_key(pending.source_kind, pending.entry_sha256) == key
                and pending.message_id
            )
        except _EFFECT_ERRORS:
            return ()

    def post(record: RelocationRecord) -> tuple[str, str] | None:
        try:
            text = recover_entry_text(memory_dir, record)
            if text is None:
                return None
            key = record_key(record.source_kind, record.entry_sha256)
            # One approval key keeps ONE thread: this runs before the façade decides
            # PENDING, so a live request of the same key lends its thread instead of
            # leaving an empty one behind per tick. A request whose message went MISSING
            # has no live candidate at all, so the record's own thread is offered too —
            # otherwise the re-post resolves a fresh binding and opens a SECOND thread.
            # The thread name carries the pending id ONLY — never the note path, title
            # or the entry text being moved.
            binding = reuse_request_thread(
                ApprovalKind.OBSIDIAN_WRITE,
                thread_candidates(
                    live_requests(key),
                    approval_thread_id=record.approval_thread_id,
                    rebind=lambda thread_id: replace(record, channel_id=thread_id),
                ),
                directory,
                owner_id,
            ) or resolve_new_binding(
                ApprovalKind.OBSIDIAN_WRITE,
                directory,
                owner_id,
                request=RequestThread(title=key),
            )
            bound = replace(record, approval_thread_id=binding.channel_id)
            store.update(bound)
            verdict = request_approval(
                bound,
                text,
                store=store,
                transport=transport,
                binding=binding,
                lease=lease,
                journal=journal,
            )
        except _EFFECT_ERRORS:
            return None
        if verdict.posted is not None:
            return verdict.posted.message_id, verdict.posted.channel_id
        if verdict.live is not None:
            return verdict.live.message_id, verdict.live.channel_id
        return None

    def probe(record: RelocationRecord) -> str:
        try:
            text = recover_entry_text(memory_dir, record)
            # An unbound surface cannot be verified; fail closed rather than treating it as gone and discarding ✅.
            if text is None or record.message_id is None or not record.channel_id:
                return "pending"
            request = ApprovalRequest(
                record_key(record.source_kind, record.entry_sha256),
                record.action_hash,
                record.message_id,
                record.channel_id,
                record.created_at,
            )
            decision = RelocateApprovalGate(record, text, store, transport).probe(request)
        except _EFFECT_ERRORS:
            return "pending"
        match decision:  # noqa: F401  # noqa: MATCH_OK - Probe is statically exhaustive.
            case Probe.APPROVED:
                return "approved"
            case Probe.CANCELLED:
                return "cancelled"
            case Probe.MISSING:
                return "missing"
            case Probe.BOUND_PENDING | Probe.BINDING_MISMATCH | Probe.UNVERIFIABLE:
                return "pending"

    def write(record: RelocationRecord) -> tuple[str, str] | None:
        plan = plan_for(record)
        if plan is None:
            return None
        # The push is a denylisted mutation: the gate needs the owner approval for THIS tool call.
        # We are only here because the approval gate probed a real owner ✅ on the bound message,
        # and that ✅ approved this exact note (the composite hash binds relpath+title+body), so
        # transcribe it once into the gate's log before pushing.
        context = ApprovalContext(
            approval_log=push_approval_log, owner_id=owner_id, e2e_test_mode=False
        )
        try:
            decision = gate_binding.evaluate(plan.note_plan, context=context)
            if not decision.allowed and record.message_id:
                record_push_approval(
                    push_approval_log,
                    action_hash=decision.action_hash,
                    target_id=decision.target_id,
                    owner_id=owner_id,
                    message_id=record.message_id,
                    now=now,
                )
            receipt = write_note(plan.note_plan, write_config, approval_context=context)
        except _EFFECT_ERRORS:
            return None
        return receipt.remote_ref, receipt.content_sha256

    def note_text(record: RelocationRecord) -> str | None:
        """The note content the RAG ingester actually indexed — the RENDERED file, not the plan.

        ``render_note`` adds the title/callout (and dated lines) at write time, so the plan body
        alone never reproduces the ingested document's fingerprint.  Read the pushed note back
        from the write clone instead.
        """
        raw = _safe_read_note(write_config.clone_dir, record.note_relpath)
        return None if raw is None else raw.decode("utf-8", errors="replace")

    def verified(record: RelocationRecord) -> bool:
        try:
            body = note_text(record)
            return (
                False
                if body is None
                else verify_ingested(rag_state_path, record.note_relpath, body).ingested
            )
        except _EFFECT_ERRORS:
            return False

    def apply(record: RelocationRecord) -> ApplyOutcome:
        plan = plan_for(record)
        if plan is None:
            return ApplyOutcome(False, "entry_absent", None, 0)
        deps = ApplyDeps(
            memory_dir,
            lambda relpath: _safe_read_note(write_config.clone_dir, relpath),
            lambda relpath, body: verify_ingested(rag_state_path, relpath, body).ingested,
            lambda current: relocation_action_hash(
                RelocationHashFields(
                    current.source_kind,
                    current.entry_sha256,
                    current.note_relpath,
                    current.note_plan_sha256,
                )
            ),
            now,
        )
        try:
            return apply_relocation(record, note_text(record) or "", deps=deps)
        except _EFFECT_ERRORS:
            return ApplyOutcome(False, "entry_absent", None, 0)

    return ResolveEffects(post, probe, write, verified, apply, now)
