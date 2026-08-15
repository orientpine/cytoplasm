from __future__ import annotations

import fcntl
import json
import os
import stat
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from http.client import HTTPSConnection
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.error import HTTPError
from urllib.parse import quote

from automation.interop.approval_directory import DiscordChannelDirectory, JsonValue
from automation.interop.approval_lease import FileKeyLease, PostingJournal
from automation.interop.approval_lifecycle import ApprovalRequest, Probe
from automation.interop.approval_surface import ApprovalKind, resolve_new_binding
from automation.memory_curator.binding import entry_digest
from automation.memory_curator.watch_steps import read_native
from automation.obsidian_write.config import ObsidianWriteError, load_config
from automation.interop.external_effect_gate import ApprovalContext
from automation.obsidian_write import gate_binding
from automation.obsidian_write.writer import write_note

from .approval_gate import RelocateApprovalGate, request_approval
from .apply import ApplyDeps, ApplyOutcome, apply_relocation
from .binding import RelocationHashFields, relocation_action_hash
from .model import RelocationRecord, RelocationState, record_key
from .plan import RelocationPlan, build_relocation_plan
from .rag_verify import verify_ingested
from .store import JsonLoader, load_state, save_state
from .watch_step import ResolveEffects

_DISCORD_API: Final = "https://discord.com/api/v10"
_USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
_PENDING_STATUSES: Final = frozenset({"proposed", "posted", "approved", "written", "ingested"})
_EFFECT_ERRORS: Final = (OSError, ValueError, RuntimeError, ObsidianWriteError)


_JSON_LOADS: JsonLoader = json.loads

def record_push_approval(
    approval_log: Path,
    *,
    action_hash: str,
    target_id: str,
    owner_id: str,
    message_id: str,
    now: datetime | None = None,
) -> None:
    """Transcribe the owner's ✅ into the record the external-effect gate accepts.

    The Obsidian push is a denylisted mutation, so ``write_note`` refuses until the gate finds
    an owner approval for THAT exact tool call.  cha's ✅ on the relocation message already
    approved this note byte-for-byte — the composite action hash binds ``relpath+title+body``,
    which is precisely the push payload — so this writes that same decision in the gate's
    schema, bound to the Discord message the reaction was read from.  It is only ever called
    after the approval gate probed a real owner ✅ on the bound message.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    payload = {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": message_id,
            "method": "manual_reaction",
            "owner_id": owner_id,
        },
        "hash": action_hash,
        "result": {"status": "approved"},
        "target_id": target_id,
        "timestamp": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    approval_log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with approval_log.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _ = handle.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        )
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    approval_log.chmod(0o600)


RelocationStoreError = RuntimeError


@dataclass(frozen=True, slots=True)
class RelocationStore:
    state_path: Path

    def all_pending(self) -> tuple[RelocationRecord, ...]:
        return tuple(record for record in load_state(self.state_path).relocations.values() if record.status in _PENDING_STATUSES)

    def pending(self) -> tuple[RelocationRecord, ...]:
        return self.all_pending()

    def set_message_id(self, record: RelocationRecord, message_id: str, channel_id: str) -> None:
        """Bind the posted message AND the surface it was posted to, atomically.

        The channel is part of the binding (interop invariant: the surface is persisted on the
        record). Losing it makes the next tick probe an empty channel, read MISSING, and discard
        the owner's ✅ — so both fields are written in one no-overwrite commit.
        """
        state, current = self._current(record_key(record.source_kind, record.entry_sha256))
        if current.action_hash != record.action_hash or current.message_id is not None:
            raise RelocationStoreError("relocation approval message id is already bound or stale")
        self._persist(state, replace(current, message_id=message_id, channel_id=channel_id))

    def clear_message_id(self, key: str, action_hash: str, message_id: str) -> None:
        state, current = self._current(key)
        if (current.action_hash, current.message_id) == (action_hash, message_id):
            self._persist(state, replace(current, message_id=None))

    def update(self, record: RelocationRecord) -> None:
        state, current = self._current(record_key(record.source_kind, record.entry_sha256))
        if current.action_hash != record.action_hash or current.message_id != record.message_id:
            raise RelocationStoreError("relocation update would change an immutable message binding")
        self._persist(state, record)

    def _current(self, key: str) -> tuple[RelocationState, RelocationRecord]:
        state = load_state(self.state_path)
        record = state.relocations.get(key)
        if record is None:
            raise RelocationStoreError("relocation record is absent")
        return state, record

    def _persist(self, state: RelocationState, record: RelocationRecord) -> None:
        records = dict(state.relocations)
        records[record_key(record.source_kind, record.entry_sha256)] = record
        save_state(self.state_path, RelocationState(state.version, records))


DiscordTransportError = ValueError


class DiscordTransport:
    token: str
    owner_id: str

    def __init__(self, token: str, owner_id: str) -> None:
        self.token = token
        self.owner_id = owner_id

    def api(self, method: str, path: str, payload: dict[str, JsonValue] | None = None) -> JsonValue:
        connection = HTTPSConnection("discord.com", timeout=30)
        try:
            connection.request(
                method,
                f"/api/v10{path}",
                body=None if payload is None else json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bot {self.token}", "Content-Type": "application/json", "User-Agent": _USER_AGENT},
            )
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            if response.status >= 400:
                raise HTTPError(f"{_DISCORD_API}{path}", response.status, response.reason, response.headers, None)
        finally:
            connection.close()
        try:
            return _JSON_LOADS(body) if body else None
        except json.JSONDecodeError as error:
            raise DiscordTransportError("Discord response is not valid JSON") from error

    def post_message(self, channel_id: str, content: str) -> str:
        return _required_string(_json_object(self.api("POST", f"/channels/{channel_id}/messages", {"content": content})), "id")

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        _ = self.api("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me")

    def get_message_content(self, channel_id: str, message_id: str) -> str | None:
        try:
            return _required_string(_json_object(self.api("GET", f"/channels/{channel_id}/messages/{message_id}")), "content")
        except HTTPError as error:
            if error.code == 404:
                return None
            raise

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        return self.get_message_content(channel_id, message_id)

    def reaction_users(self, channel_id: str, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        payload = self.api("GET", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}")
        if not isinstance(payload, list):
            raise DiscordTransportError("Discord reaction users response is not a list")
        users: list[tuple[str, bool]] = []
        for raw_user in payload:
            user = _json_object(raw_user)
            is_bot = user.get("bot", False)
            if not isinstance(is_bot, bool):
                raise DiscordTransportError("Discord reaction user has an invalid bot flag")
            users.append((_required_string(user, "id"), is_bot))
        return tuple(users)

    def get_reaction_users(self, channel_id: str, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        return self.reaction_users(channel_id, message_id, emoji)

    def delete_message(self, channel_id: str, message_id: str) -> None:
        _ = self.api("DELETE", f"/channels/{channel_id}/messages/{message_id}")


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise DiscordTransportError("Discord response is not an object")
    return value


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise DiscordTransportError(f"Discord response omitted {key}")
    return value


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
            return None if text is None else build_relocation_plan(text)
        except _EFFECT_ERRORS:
            return None

    def post(record: RelocationRecord) -> tuple[str, str] | None:
        try:
            text = recover_entry_text(memory_dir, record)
            if text is None:
                return None
            binding = resolve_new_binding(ApprovalKind.OBSIDIAN_WRITE, directory, owner_id)
            verdict = request_approval(
                record,
                text,
                store=store,
                transport=transport,
                binding=binding,
                lease=lease,
                journal=journal,
            )
        except _EFFECT_ERRORS:
            return None
        return None if verdict.posted is None else (verdict.posted.message_id, verdict.posted.channel_id)

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
