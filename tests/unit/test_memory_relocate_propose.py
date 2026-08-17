from __future__ import annotations

from datetime import UTC, datetime

from automation.memory_curator.binding import entry_digest
from automation.memory_relocate.binding import RelocationHashFields, relocation_action_hash
from automation.memory_relocate.model import (
    RelocationRecord,
    RelocationState,
    parse_state,
    record_key,
    serialize_state,
)
from automation.memory_relocate.plan import build_relocation_plan
from automation.memory_relocate.propose import build_proposed_record
from automation.memory_relocate.rag_verify import rag_source_key


_ENTRY = "<primary-node>는 prod이고 <rag-node>는 개인 RAG 전용이다."
_NOW = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)


def _proposed_record() -> RelocationRecord:
    return build_proposed_record(
        _ENTRY,
        source_kind="memory",
        entry_sha256=entry_digest("memory", _ENTRY),
        reclaimable_chars=len(_ENTRY),
        binding_kind="obsidian-write",
        binding_surface="owner-dm",
        binding_channel_id="123456789",
        binding_policy_version=6,
        now=_NOW,
    )


def test_build_proposed_record_when_memory_ops_reference_binds_plan_and_roundtrips() -> None:
    # Given: one classified operational-memory entry and its source-qualified digest.
    digest = entry_digest("memory", _ENTRY)

    # When: the same proposal is built twice at the same instant.
    first = _proposed_record()
    second = _proposed_record()
    plan = build_relocation_plan(_ENTRY)

    # Then: the proposed record is deterministic and binds the exact plan and source.
    expected_hash = relocation_action_hash(
        RelocationHashFields(
            "memory",
            digest,
            plan.note_plan.relpath.as_posix(),
            plan.note_plan_sha256,
        )
    )
    assert first == second
    assert first.status == "proposed"
    assert first.message_id is None
    assert first.action_hash == expected_hash
    assert first.note_relpath == plan.note_plan.relpath.as_posix()
    assert first.note_relpath.startswith("000_PARA/Resource/")
    assert first.note_plan_sha256 == plan.note_plan_sha256
    assert first.rag_source_key == rag_source_key(first.note_relpath)
    state = RelocationState(
        version=1,
        relocations={record_key(first.source_kind, first.entry_sha256): first},
    )
    assert parse_state(serialize_state(state)) == state


def test_build_proposed_record_when_source_is_user_accepts_a_namespaced_plan() -> None:
    # Given: a USER.md operational reference and its source-qualified digest.
    entry = "차의 개인 선호"

    # When: the proposal is built through the production guard and planner.
    record = build_proposed_record(
        entry,
        source_kind="user",
        entry_sha256=entry_digest("user", entry),
        reclaimable_chars=len(entry),
        binding_kind="obsidian-write",
        binding_surface="owner-dm",
        binding_channel_id="123456789",
        binding_policy_version=6,
        now=_NOW,
    )
    user_plan = build_relocation_plan(entry, source_kind="user")
    memory_plan = build_relocation_plan(entry, source_kind="memory")

    # Then: USER is accepted and separated only by its deterministic note path namespace.
    assert record.status == "proposed"
    assert record.source_kind == "user"
    assert record.note_relpath == user_plan.note_plan.relpath.as_posix()
    assert record.note_relpath != memory_plan.note_plan.relpath.as_posix()
    assert record.rag_source_key == rag_source_key(record.note_relpath)
