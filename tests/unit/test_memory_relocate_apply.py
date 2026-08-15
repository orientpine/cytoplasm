from __future__ import annotations

import fcntl
import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.curator import parse_memory_file
from automation.memory_relocate.apply import ApplyDeps, ApplyOutcome, apply_relocation
from automation.memory_relocate.model import RelocationRecord

ENTRY: Final = "owner memory selected for relocation"
OTHER_ENTRY: Final = "native memory that must remain"
ORIGINAL: Final = f"{ENTRY}\n§\n{OTHER_ENTRY}".encode()
NOTE_BODY: Final = f"# Relocated memory\n\n{ENTRY}\n"
NOTE_BYTES: Final = NOTE_BODY.encode()
NOTE_SHA256: Final = hashlib.sha256(NOTE_BYTES).hexdigest()
ACTION_HASH: Final = f"sha256:{'a' * 64}"
NOW: Final = datetime(2026, 7, 31, 12, 34, 56, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FakeControls:
    note_bytes: bytes | None = NOTE_BYTES
    rag_ingested: bool = True
    action_hash: str = ACTION_HASH


DEFAULT_CONTROLS: Final = FakeControls()


def _record() -> RelocationRecord:
    return RelocationRecord(
        version=1,
        source_kind="memory",
        entry_sha256=entry_digest("memory", ENTRY),
        note_relpath="Areas/relocated-memory.md",
        note_plan_sha256="b" * 64,
        reclaimable_chars=len(ENTRY),
        action_hash=ACTION_HASH,
        status="approved",
        kind="memory_relocation",
        surface="owner_dm",
        channel_id="owner-dm-1",
        policy_version=1,
        message_id="approval-1",
        created_at="2026-07-31T12:00:00Z",
        approved_at="2026-07-31T12:01:00Z",
        written_at=None,
        reconciled_at=None,
        remote_ref=None,
        note_content_sha256=NOTE_SHA256,
        rag_source_key=None,
        rag_fingerprint=None,
        backup_path=None,
        last_block_reason=None,
    )


def _deps(
    memory_dir: Path,
    controls: FakeControls = DEFAULT_CONTROLS,
) -> tuple[ApplyDeps, list[str]]:
    calls: list[str] = []

    def read_twin(_note_relpath: str) -> bytes | None:
        calls.append("read_twin")
        return controls.note_bytes

    def verify_rag(_note_relpath: str, _note_body: str) -> bool:
        calls.append("verify_rag")
        return controls.rag_ingested

    def recompute_action_hash(_record: RelocationRecord) -> str:
        calls.append("recompute_action_hash")
        return controls.action_hash

    return (
        ApplyDeps(memory_dir, read_twin, verify_rag, recompute_action_hash, NOW),
        calls,
    )


def test_apply_when_status_is_not_approved_blocks_before_other_gates(tmp_path: Path) -> None:
    # Given: the native entry exists but owner approval is absent.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    before = memory_path.read_bytes()
    deps, calls = _deps(tmp_path)

    # When: destructive apply evaluates the first gate.
    outcome = apply_relocation(replace(_record(), status="proposed"), NOTE_BODY, deps=deps)

    # Then: no later dependency runs and native bytes remain exact.
    assert outcome == ApplyOutcome(False, "not_approved", None, 0)
    assert calls == []
    assert memory_path.read_bytes() == before
    assert tuple(tmp_path.glob("MEMORY.md.deleted-*")) == ()


def test_apply_when_action_hash_drifted_blocks_before_write_proof(tmp_path: Path) -> None:
    # Given: approval exists but the record no longer reproduces its approved hash.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    before = memory_path.read_bytes()
    deps, calls = _deps(tmp_path, FakeControls(action_hash=f"sha256:{'f' * 64}"))

    # When: destructive apply recomputes the owner-bound hash.
    outcome = apply_relocation(_record(), NOTE_BODY, deps=deps)

    # Then: it stops at hash drift without reading the destination.
    assert outcome == ApplyOutcome(False, "action_hash_drift", None, 0)
    assert calls == ["recompute_action_hash"]
    assert memory_path.read_bytes() == before
    assert tuple(tmp_path.glob("MEMORY.md.deleted-*")) == ()


def test_apply_when_obsidian_note_is_absent_blocks_before_rag(tmp_path: Path) -> None:
    # Given: approval is bound but no regular destination note can be read.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    before = memory_path.read_bytes()
    deps, calls = _deps(tmp_path, FakeControls(note_bytes=None))

    # When: destructive apply checks Obsidian write proof.
    outcome = apply_relocation(_record(), NOTE_BODY, deps=deps)

    # Then: RAG and native deletion are not attempted.
    assert outcome == ApplyOutcome(False, "obsidian_not_written", None, 0)
    assert calls == ["recompute_action_hash", "read_twin"]
    assert memory_path.read_bytes() == before
    assert tuple(tmp_path.glob("MEMORY.md.deleted-*")) == ()


def test_apply_when_obsidian_note_digest_differs_blocks_before_rag(tmp_path: Path) -> None:
    # Given: a note exists at the path but differs from the recorded write receipt.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    before = memory_path.read_bytes()
    deps, calls = _deps(tmp_path, FakeControls(note_bytes=b"different note"))

    # When: destructive apply validates the stored note digest.
    outcome = apply_relocation(_record(), NOTE_BODY, deps=deps)

    # Then: the mismatched destination cannot authorize native deletion.
    assert outcome == ApplyOutcome(False, "obsidian_not_written", None, 0)
    assert calls == ["recompute_action_hash", "read_twin"]
    assert memory_path.read_bytes() == before
    assert tuple(tmp_path.glob("MEMORY.md.deleted-*")) == ()


def test_apply_when_rag_is_not_ingested_blocks_before_native_read(tmp_path: Path) -> None:
    # Given: owner approval and destination write proof pass, but RAG proof does not.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    before = memory_path.read_bytes()
    deps, calls = _deps(tmp_path, FakeControls(rag_ingested=False))

    # When: destructive apply checks the destination digest in RAG.
    outcome = apply_relocation(_record(), NOTE_BODY, deps=deps)

    # Then: native memory and backups remain untouched.
    assert outcome == ApplyOutcome(False, "rag_not_ingested", None, 0)
    assert calls == ["recompute_action_hash", "read_twin", "verify_rag"]
    assert memory_path.read_bytes() == before
    assert tuple(tmp_path.glob("MEMORY.md.deleted-*")) == ()


def test_apply_when_native_entry_was_edited_blocks_as_absent(tmp_path: Path) -> None:
    # Given: every external proof passes but the native entry text was edited.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(f"{ENTRY} edited\n§\n{OTHER_ENTRY}".encode())
    before = memory_path.read_bytes()
    deps, _ = _deps(tmp_path)

    # When: destructive apply compares source-qualified entry digests.
    outcome = apply_relocation(_record(), NOTE_BODY, deps=deps)

    # Then: the stale target is treated as absent without mutation.
    assert outcome == ApplyOutcome(False, "entry_absent", None, 0)
    assert memory_path.read_bytes() == before
    assert tuple(tmp_path.glob("MEMORY.md.deleted-*")) == ()


def test_apply_when_native_entry_is_duplicated_blocks_as_ambiguous(tmp_path: Path) -> None:
    # Given: every external proof passes but two exact native targets exist.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(f"{ENTRY}\n§\n{OTHER_ENTRY}\n§\n{ENTRY}".encode())
    before = memory_path.read_bytes()
    deps, _ = _deps(tmp_path)

    # When: destructive apply searches for the bound entry digest.
    outcome = apply_relocation(_record(), NOTE_BODY, deps=deps)

    # Then: singularity failure blocks deletion and backup creation.
    assert outcome == ApplyOutcome(False, "entry_ambiguous", None, 0)
    assert memory_path.read_bytes() == before
    assert tuple(tmp_path.glob("MEMORY.md.deleted-*")) == ()


def test_apply_when_deletion_lock_is_contended_blocks_as_absent(tmp_path: Path) -> None:
    # Given: all five gates pass but another process owns the deletion lock.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    before = memory_path.read_bytes()
    deps, _ = _deps(tmp_path)
    lock_path = tmp_path / "MEMORY.md.lock"

    # When: the sole deletion path reports lock contention.
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        outcome = apply_relocation(_record(), NOTE_BODY, deps=deps)

    # Then: uncertainty becomes a retryable block without mutation.
    assert outcome == ApplyOutcome(False, "entry_absent", None, 0)
    assert memory_path.read_bytes() == before
    assert tuple(tmp_path.glob("MEMORY.md.deleted-*")) == ()


def test_apply_when_all_five_gates_pass_deletes_exactly_one_entry(tmp_path: Path) -> None:
    # Given: every proof is valid and exactly one bound native entry exists.
    memory_path = tmp_path / "MEMORY.md"
    _ = memory_path.write_bytes(ORIGINAL)
    before = memory_path.read_bytes()
    deps, calls = _deps(tmp_path)

    # When: destructive apply reaches the curator's deletion path.
    outcome = apply_relocation(_record(), NOTE_BODY, deps=deps)

    # Then: one entry is removed and the exact original has a durable backup.
    after = memory_path.read_bytes()
    parsed = parse_memory_file(after.decode(), kind="memory")
    assert outcome.deleted is True
    assert outcome.reason is None
    assert outcome.backup_path is not None
    assert Path(outcome.backup_path).read_bytes() == before
    assert outcome.freed_chars > 0
    assert tuple(entry.text for entry in parsed.entries) == (OTHER_ENTRY,)
    assert len(after.decode()) < len(before.decode())
    assert calls == ["recompute_action_hash", "read_twin", "verify_rag"]
