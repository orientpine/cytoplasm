from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.model import MemoryKind
from automation.memory_curator.watch_steps import read_native
from automation.memory_relocate.apply import ApplyDeps, ApplyOutcome, apply_relocation
from automation.memory_relocate.binding import RelocationHashFields, relocation_action_hash
from automation.memory_relocate.discover import select_candidate
from automation.memory_relocate.model import (
    RelocationRecord,
    RelocationState,
    RelocationStatus,
    record_key,
)
from automation.memory_relocate.propose import build_proposed_record
from automation.memory_relocate.rag_verify import rag_source_key
from automation.memory_relocate.store import load_state
from automation.memory_relocate.watch_step import ResolveEffects, resolve_tick

_KINDS: Final[tuple[MemoryKind, ...]] = ("memory", "user")
_NOW: Final = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
_SHARED_ENTRY: Final = "same operational fact for both native stores"
_REMAINDERS: Final = {
    "memory": "memory entry that must remain",
    "user": "user entry that must remain",
}
_NOTE_BODY: Final = f"# Relocated native entry\n\n{_SHARED_ENTRY}\n"
_COMPAT_FIELDS: Final = (
    b"000_PARA/Resource/same-operational-fact-for-both-native-stores-a1a40adc--e63338421328.md",
    b"74e01a7510763cc01d9c05b6c0561a4b4b6f83349bd13f2b2e1f813993893887",
    b"obsidian:000_PARA/Resource/same-operational-fact-for-both-native-stores-a1a40adc--e63338421328.md",
    b"sha256:d4e21fa14f37a3553f13fbe10e3cb0989bd0952ce4c56137a0207df36cc3ada7",
)
_MEMORY_RECORD_FIXTURE: Final = """{
  "version": 1,
  "relocations": {
    "memory:2a16baf5edf06a94433947e502ec338a0e12bd0b79feb6668e6d99547af0661f": {
      "version": 1, "source_kind": "memory",
      "entry_sha256": "2a16baf5edf06a94433947e502ec338a0e12bd0b79feb6668e6d99547af0661f",
      "note_relpath": "000_PARA/Resource/same-operational-fact-for-both-native-stores-a1a40adc--e63338421328.md",
      "note_plan_sha256": "74e01a7510763cc01d9c05b6c0561a4b4b6f83349bd13f2b2e1f813993893887",
      "reclaimable_chars": 44, "status": "$STATUS",
      "action_hash": "sha256:d4e21fa14f37a3553f13fbe10e3cb0989bd0952ce4c56137a0207df36cc3ada7",
      "kind": "obsidian-write", "surface": "owner-dm",
      "channel_id": "", "policy_version": 6,
      "message_id": $MESSAGE_ID,
      "created_at": "2026-07-31T10:00:00+00:00",
      "approved_at": null, "written_at": $WRITTEN_AT,
      "reconciled_at": null, "remote_ref": null,
      "note_content_sha256": null, "rag_fingerprint": null,
      "rag_source_key": "obsidian:000_PARA/Resource/same-operational-fact-for-both-native-stores-a1a40adc--e63338421328.md",
      "backup_path": null, "last_block_reason": null
    }
  }
}
"""


def _action_hash(record: RelocationRecord) -> str:
    fields = RelocationHashFields(
        record.source_kind, record.entry_sha256, record.note_relpath, record.note_plan_sha256
    )
    return relocation_action_hash(fields)


def _build_record(source_kind: MemoryKind, entry_text: str) -> RelocationRecord:
    return build_proposed_record(
        entry_text,
        source_kind=source_kind,
        entry_sha256=entry_digest(source_kind, entry_text),
        reclaimable_chars=len(entry_text),
        binding_kind="obsidian-write",
        binding_surface="owner-dm",
        binding_channel_id="",
        binding_policy_version=6,
        now=_NOW,
    )


def _write_two_store_fixture(memory_dir: Path) -> dict[MemoryKind, bytes]:
    originals: dict[MemoryKind, bytes] = {}
    for source_kind in _KINDS:
        filename = "MEMORY.md" if source_kind == "memory" else "USER.md"
        raw = f"{_SHARED_ENTRY}\n§\n{_REMAINDERS[source_kind]}".encode()
        _ = (memory_dir / filename).write_bytes(raw)
        originals[source_kind] = raw
    return originals


def _discover_proposal(memory_dir: Path, source_kind: MemoryKind) -> RelocationRecord:
    files = {kind: read_native(memory_dir, kind)[1] for kind in _KINDS}
    verdict = EntryVerdict(
        source_kind=source_kind,
        entry_text=_SHARED_ENTRY,
        route="OPS_REFERENCE",
        evidence="fixture",
        reason="fixture",
        veto=None,
        llm_called=False,
    )
    candidate = select_candidate((verdict,), files, frozenset())
    assert candidate is not None
    return _build_record(candidate.source_kind, candidate.entry_text)


def _state(record: RelocationRecord) -> RelocationState:
    key = record_key(record.source_kind, record.entry_sha256)
    return RelocationState(version=1, relocations={key: record})


@dataclass(frozen=True, slots=True)
class _Lifecycle:
    memory_dir: Path
    rag_ready: bool = True
    owner_verdict: str = "approved"
    notes: dict[str, bytes] = field(default_factory=dict)
    rag_index: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def post(self, record: RelocationRecord) -> tuple[str, str]:
        self.calls.append(f"post:{record.source_kind}")
        return (f"approval-{record.source_kind}", "owner-dm")

    def probe(self, record: RelocationRecord) -> str:
        self.calls.append(f"probe:{record.source_kind}")
        return self.owner_verdict

    def write(self, record: RelocationRecord) -> tuple[str, str]:
        self.calls.append(f"write:{record.source_kind}")
        note_bytes = _NOTE_BODY.encode()
        self.notes[record.note_relpath] = note_bytes
        if self.rag_ready:
            self.rag_index.add(rag_source_key(record.note_relpath))
        return (f"fixture:{record.note_relpath}", hashlib.sha256(note_bytes).hexdigest())

    def verify(self, record: RelocationRecord) -> bool:
        self.calls.append(f"verify:{record.source_kind}")
        return record.rag_source_key in self.rag_index

    def verify_note(self, note_relpath: str, note_body: str) -> bool:
        return (
            rag_source_key(note_relpath) in self.rag_index
            and self.notes.get(note_relpath) == note_body.encode()
        )

    def apply(self, record: RelocationRecord) -> ApplyOutcome:
        self.calls.append(f"apply:{record.source_kind}")
        note_body = self.notes[record.note_relpath].decode()
        deps = ApplyDeps(
            self.memory_dir,
            self.notes.get,
            self.verify_note,
            _action_hash,
            _NOW,
        )
        return apply_relocation(record, note_body, deps=deps)

    def effects(self) -> ResolveEffects:
        return ResolveEffects(self.post, self.probe, self.write, self.verify, self.apply, _NOW)


def _run_four_ticks(record: RelocationRecord, lifecycle: _Lifecycle) -> RelocationState:
    state = _state(record)
    for _ in range(4):
        state = resolve_tick(state, effects=lifecycle.effects()).state
    return state


@pytest.mark.parametrize(
    ("status", "message_id", "written_at"),
    (
        ("proposed", "null", "null"),
        ("posted", '"approval-existing"', "null"),
        ("written", '"approval-existing"', '"2026-07-31T10:03:00+00:00"'),
    ),
)
def test_existing_memory_records_remain_byte_compatible(
    tmp_path: Path,
    status: RelocationStatus,
    message_id: str,
    written_at: str,
) -> None:
    # Given: a persisted v1 MEMORY record from before two-store support.
    fixture = (
        _MEMORY_RECORD_FIXTURE.replace("$STATUS", status)
        .replace("$MESSAGE_ID", message_id)
        .replace("$WRITTEN_AT", written_at)
    )
    fixture_path = tmp_path / "relocations.json"
    _ = fixture_path.write_text(fixture, encoding="utf-8")

    # When: the old record is loaded and the same MEMORY proposal is freshly planned.
    loaded = next(iter(load_state(fixture_path).relocations.values()))
    fresh = _build_record("memory", _SHARED_ENTRY)

    # Then: all externally persisted identity bytes remain exactly unchanged.
    assert loaded.status == status
    assert tuple(
        value.encode()
        for value in (
            loaded.note_relpath,
            loaded.note_plan_sha256,
            loaded.rag_source_key or "",
            loaded.action_hash,
        )
    ) == _COMPAT_FIELDS
    assert tuple(
        value.encode()
        for value in (
            fresh.note_relpath,
            fresh.note_plan_sha256,
            fresh.rag_source_key or "",
            fresh.action_hash,
        )
    ) == _COMPAT_FIELDS


@pytest.mark.parametrize("source_kind", _KINDS)
def test_each_store_reclaims_only_after_approval_write_and_rag(
    tmp_path: Path,
    source_kind: MemoryKind,
) -> None:
    # Given: MEMORY.md and USER.md each hold the same relocatable fact plus one survivor.
    originals = _write_two_store_fixture(tmp_path)
    record = _discover_proposal(tmp_path, source_kind)
    lifecycle = _Lifecycle(tmp_path)

    # When: discovery, proposal, approval, write, RAG indexing, and apply all complete.
    completed = _run_four_ticks(record, lifecycle)

    # Then: only the approved store entry is reclaimed after the full effect chain.
    final = completed.relocations[record_key(source_kind, record.entry_sha256)]
    assert final.status == "reconciled"
    assert tuple(entry.text for entry in read_native(tmp_path, source_kind)[1].entries) == (
        _REMAINDERS[source_kind],
    )
    other_kind: MemoryKind = "user" if source_kind == "memory" else "memory"
    assert read_native(tmp_path, other_kind)[0] == originals[other_kind]
    assert lifecycle.calls == [
        f"post:{source_kind}",
        f"probe:{source_kind}",
        f"write:{source_kind}",
        f"verify:{source_kind}",
        f"apply:{source_kind}",
    ]


@pytest.mark.parametrize("source_kind", _KINDS)
def test_each_store_rolls_back_reclaim_when_rag_indexing_fails(
    tmp_path: Path,
    source_kind: MemoryKind,
) -> None:
    # Given: approval and Obsidian write succeed, but the synthetic RAG index stays empty.
    originals = _write_two_store_fixture(tmp_path)
    record = _discover_proposal(tmp_path, source_kind)
    lifecycle = _Lifecycle(tmp_path, rag_ready=False)

    # When: four ticks reach the RAG verification boundary.
    blocked = _run_four_ticks(record, lifecycle)

    # Then: the note may exist, but native bytes and deletion backups remain untouched.
    assert next(iter(blocked.relocations.values())).status == "written"
    assert read_native(tmp_path, source_kind)[0] == originals[source_kind]
    assert record.note_relpath in lifecycle.notes
    assert tuple(tmp_path.glob("*.deleted-*")) == ()
    assert not any(call.startswith("apply:") for call in lifecycle.calls)


@pytest.mark.parametrize("source_kind", _KINDS)
def test_each_store_has_zero_deletions_without_owner_approval(
    tmp_path: Path,
    source_kind: MemoryKind,
) -> None:
    # Given: a discovered proposal whose owner reaction remains pending.
    originals = _write_two_store_fixture(tmp_path)
    record = _discover_proposal(tmp_path, source_kind)
    lifecycle = _Lifecycle(tmp_path, owner_verdict="pending")

    # When: posting and reaction probing run without an approval.
    posted = resolve_tick(_state(record), effects=lifecycle.effects()).state
    pending = resolve_tick(posted, effects=lifecycle.effects()).state

    # Then: neither destination writing nor source deletion is attempted.
    assert next(iter(pending.relocations.values())).status == "posted"
    assert read_native(tmp_path, source_kind)[0] == originals[source_kind]
    assert lifecycle.notes == {}
    assert lifecycle.calls == [f"post:{source_kind}", f"probe:{source_kind}"]


def test_same_text_in_both_stores_uses_distinct_note_paths() -> None:
    # Given: the exact same text is proposed through the real guarded path for both stores.
    memory_record = _build_record("memory", _SHARED_ENTRY)
    user_record = _build_record("user", _SHARED_ENTRY)

    # When / Then: source namespace alone prevents path and RAG-key collision.
    assert memory_record.note_relpath != user_record.note_relpath
    assert memory_record.rag_source_key == rag_source_key(memory_record.note_relpath)
    assert user_record.rag_source_key == rag_source_key(user_record.note_relpath)
