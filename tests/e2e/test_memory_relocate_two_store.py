from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.model import MemoryKind
from automation.memory_curator.watch_steps import read_native
from automation.memory_relocate.apply import ApplyDeps, ApplyOutcome, apply_relocation
from automation.memory_relocate.binding import RelocationHashFields, relocation_action_hash
from automation.memory_relocate.discover import select_candidate
from automation.memory_relocate.model import RelocationRecord, RelocationState, record_key
from automation.memory_relocate.propose import build_proposed_record
from automation.memory_relocate.rag_verify import rag_source_key
from automation.memory_relocate.store import load_state, save_state
from automation.memory_relocate.watch_step import ResolveEffects, resolve_tick

_KINDS: Final[tuple[MemoryKind, ...]] = ("memory", "user")
_NOW: Final = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
_ENTRIES: Final = {
    "memory": "memory-store operational reference",
    "user": "user-store operational reference",
}
_REMAINDERS: Final = {
    "memory": "memory fact that remains native",
    "user": "user fact that remains native",
}


def _action_hash(record: RelocationRecord) -> str:
    fields = RelocationHashFields(
        record.source_kind, record.entry_sha256, record.note_relpath, record.note_plan_sha256
    )
    return relocation_action_hash(fields)


def _seed_native(root: Path) -> dict[MemoryKind, bytes]:
    originals: dict[MemoryKind, bytes] = {}
    for kind in _KINDS:
        filename = "MEMORY.md" if kind == "memory" else "USER.md"
        content = f"{_ENTRIES[kind]}\n§\n{_REMAINDERS[kind]}".encode()
        _ = (root / filename).write_bytes(content)
        originals[kind] = content
    return originals


def _candidate(
    root: Path,
    kind: MemoryKind,
    known: frozenset[str] = frozenset(),
) -> RelocationRecord | None:
    files = {source: read_native(root, source)[1] for source in _KINDS}
    verdict = EntryVerdict(
        source_kind=kind,
        entry_text=_ENTRIES[kind],
        route="OPS_REFERENCE",
        evidence="offline-fixture",
        reason="offline-fixture",
        veto=None,
        llm_called=False,
    )
    selected = select_candidate((verdict,), files, known)
    if selected is None:
        return None
    return build_proposed_record(
        selected.entry_text,
        source_kind=selected.source_kind,
        entry_sha256=selected.entry_sha256,
        reclaimable_chars=selected.reclaimable_chars,
        binding_kind="obsidian-write",
        binding_surface="owner-dm",
        binding_channel_id="",
        binding_policy_version=6,
        now=_NOW,
    )


def _state(*records: RelocationRecord) -> RelocationState:
    return RelocationState(
        version=1,
        relocations={record_key(record.source_kind, record.entry_sha256): record for record in records},
    )


@dataclass(frozen=True, slots=True)
class _OfflineEffects:
    root: Path
    approved: frozenset[MemoryKind]
    rag_ready: frozenset[MemoryKind]
    notes: dict[str, bytes] = field(default_factory=dict)
    rag_index: set[str] = field(default_factory=set)
    calls: list[tuple[str, MemoryKind]] = field(default_factory=list)

    def post(self, record: RelocationRecord) -> tuple[str, str]:
        self.calls.append(("post", record.source_kind))
        return (f"fixture-{record.source_kind}", "offline-owner-dm")

    def probe(self, record: RelocationRecord) -> str:
        self.calls.append(("probe", record.source_kind))
        return "approved" if record.source_kind in self.approved else "pending"

    def write(self, record: RelocationRecord) -> tuple[str, str]:
        self.calls.append(("write", record.source_kind))
        content = f"# relocated\n\n{_ENTRIES[record.source_kind]}\n".encode()
        self.notes[record.note_relpath] = content
        if record.source_kind in self.rag_ready:
            self.rag_index.add(rag_source_key(record.note_relpath))
        return (f"fixture:{record.note_relpath}", hashlib.sha256(content).hexdigest())

    def verify(self, record: RelocationRecord) -> bool:
        self.calls.append(("verify", record.source_kind))
        return record.rag_source_key in self.rag_index

    def verify_note(self, note_relpath: str, note_body: str) -> bool:
        return (
            rag_source_key(note_relpath) in self.rag_index
            and self.notes.get(note_relpath) == note_body.encode()
        )

    def apply(self, record: RelocationRecord) -> ApplyOutcome:
        self.calls.append(("apply", record.source_kind))
        body = self.notes[record.note_relpath].decode()
        deps = ApplyDeps(self.root, self.notes.get, self.verify_note, _action_hash, _NOW)
        return apply_relocation(record, body, deps=deps)

    def injected(self) -> ResolveEffects:
        return ResolveEffects(self.post, self.probe, self.write, self.verify, self.apply, _NOW)


def _run_ticks(
    root: Path,
    state: RelocationState,
    effects: _OfflineEffects,
    *,
    count: int,
) -> RelocationState:
    state_path = root / "relocations.json"
    save_state(state_path, state)
    for _ in range(count):
        current = load_state(state_path)
        resolved = resolve_tick(current, effects=effects.injected(), max_posts=2)
        save_state(state_path, resolved.state)
    return load_state(state_path)


def _record_for(root: Path, kind: MemoryKind) -> RelocationRecord:
    record = _candidate(root, kind)
    assert record is not None
    return record


@pytest.mark.parametrize("kind", _KINDS)
def test_offline_store_completes_full_owner_gated_relocation(
    tmp_path: Path,
    kind: MemoryKind,
) -> None:
    # Given: one store has a proposed record and every offline effect is ready.
    originals = _seed_native(tmp_path)
    record = _record_for(tmp_path, kind)
    effects = _OfflineEffects(tmp_path, frozenset({kind}), frozenset({kind}))

    # When: persisted state crosses post, approval, write, RAG, and deletion ticks.
    completed = _run_ticks(tmp_path, _state(record), effects, count=4)

    # Then: the target alone is reclaimed and the other store remains byte-exact.
    final = completed.relocations[record_key(kind, record.entry_sha256)]
    assert final.status == "reconciled"
    assert tuple(entry.text for entry in read_native(tmp_path, kind)[1].entries) == (
        _REMAINDERS[kind],
    )
    other: MemoryKind = "user" if kind == "memory" else "memory"
    assert read_native(tmp_path, other)[0] == originals[other]


@pytest.mark.parametrize("kind", _KINDS)
def test_offline_store_rolls_back_reclaim_when_rag_fails(
    tmp_path: Path,
    kind: MemoryKind,
) -> None:
    # Given: owner approval and note writing work, but RAG never indexes the note.
    originals = _seed_native(tmp_path)
    record = _record_for(tmp_path, kind)
    effects = _OfflineEffects(tmp_path, frozenset({kind}), frozenset())

    # When: the state machine reaches the mid-flow RAG failure.
    blocked = _run_ticks(tmp_path, _state(record), effects, count=4)

    # Then: the written record remains retryable and native bytes are unchanged.
    assert next(iter(blocked.relocations.values())).status == "written"
    assert read_native(tmp_path, kind)[0] == originals[kind]
    assert not any(call == ("apply", kind) for call in effects.calls)


@pytest.mark.parametrize("kind", _KINDS)
def test_offline_store_suppresses_rediscovery_of_the_same_entry(
    tmp_path: Path,
    kind: MemoryKind,
) -> None:
    # Given: the same source-qualified entry already has a relocation record.
    _seed_native(tmp_path)
    record = _record_for(tmp_path, kind)

    # When: discovery sees the unchanged entry and the persisted known digest.
    repeated = _candidate(tmp_path, kind, frozenset({record.entry_sha256}))

    # Then: no second proposal is created.
    assert repeated is None


@pytest.mark.parametrize("kind", _KINDS)
def test_offline_store_never_deletes_without_owner_approval(
    tmp_path: Path,
    kind: MemoryKind,
) -> None:
    # Given: a proposal posts, but the fake owner never approves it.
    originals = _seed_native(tmp_path)
    record = _record_for(tmp_path, kind)
    effects = _OfflineEffects(tmp_path, frozenset(), frozenset({kind}))

    # When: repeated offline ticks only observe a pending reaction.
    pending = _run_ticks(tmp_path, _state(record), effects, count=4)

    # Then: no note write, RAG check, or native deletion occurs.
    assert next(iter(pending.relocations.values())).status == "posted"
    assert read_native(tmp_path, kind)[0] == originals[kind]
    assert effects.notes == {}
    assert not any(action in {"write", "apply"} for action, _kind in effects.calls)


def test_offline_store_failure_does_not_block_the_other_store(
    tmp_path: Path,
) -> None:
    # Given: both stores are approved, but only USER reaches the synthetic RAG index.
    originals = _seed_native(tmp_path)
    memory = _record_for(tmp_path, "memory")
    user = _record_for(tmp_path, "user")
    effects = _OfflineEffects(tmp_path, frozenset(_KINDS), frozenset({"user"}))

    # When: both records advance through the same persisted offline ticks.
    final = _run_ticks(tmp_path, _state(memory, user), effects, count=4)

    # Then: MEMORY stays written while USER independently reconciles and reclaims.
    assert final.relocations[record_key("memory", memory.entry_sha256)].status == "written"
    assert final.relocations[record_key("user", user.entry_sha256)].status == "reconciled"
    assert read_native(tmp_path, "memory")[0] == originals["memory"]
    assert tuple(entry.text for entry in read_native(tmp_path, "user")[1].entries) == (
        _REMAINDERS["user"],
    )
