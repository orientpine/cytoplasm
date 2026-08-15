"""Deterministic Obsidian destination planning for relocated operational facts."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

import pytest

from automation.memory_relocate.plan import RelocationPlan, build_relocation_plan
from automation.obsidian_write.config import ObsidianWriteError

_ENTRY = "<primary-node>가 prod이고 <rag-node>는 개인 RAG 전용이다."


def _expected_digest(plan: RelocationPlan) -> str:
    preimage = f"{plan.note_plan.relpath.as_posix()}\0{plan.note_plan.title}\0{plan.note_plan.body}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def test_relocation_plan_is_identical_when_the_entry_is_unchanged() -> None:
    # Given: one operational-fact entry classified as OPS_REFERENCE.
    entry_text = _ENTRY

    # When: the relocation is planned twice.
    first = build_relocation_plan(entry_text)
    second = build_relocation_plan(entry_text)

    # Then: every planned field — path, title, body, hash — is identical.
    assert first == second
    assert first.note_plan.relpath == second.note_plan.relpath
    assert first.note_plan.title == second.note_plan.title
    assert first.note_plan.body == second.note_plan.body
    assert first.note_plan_sha256 == second.note_plan_sha256


def test_relocation_plan_lands_in_personal_para_resource_when_planned() -> None:
    # Given: an operational-fact entry / When: planned.
    plan = build_relocation_plan(_ENTRY)

    # Then: the note lands under the personal PARA Resource bucket, not the KIMM root.
    assert plan.note_plan.relpath.parts[0] == "000_PARA"
    assert "Resource" in plan.note_plan.relpath.parts
    assert plan.note_plan.relpath.name.endswith(".md")


def test_relocation_body_preserves_the_entry_when_it_has_edge_whitespace() -> None:
    # Given: an entry whose exact characters include leading, trailing, and tab whitespace.
    entry_text = "  <primary-node>는 prod이다.\n\n  - 두 번째 줄\t탭 포함  "

    # When: the relocation is planned.
    plan = build_relocation_plan(entry_text)

    # Then: the original entry is recoverable byte-for-byte from the note body.
    assert entry_text in plan.note_plan.body
    assert entry_text.encode("utf-8") in plan.note_plan.body.encode("utf-8")


def test_relocation_body_states_observed_reference_provenance_when_planned() -> None:
    # Given: an operational-fact entry / When: planned.
    body = build_relocation_plan(_ENTRY).note_plan.body

    # Then: the footer marks the note as an observed, reference-authority relocation.
    assert "provenance: observed" in body
    assert "authority: reference" in body
    assert "native memory" in body
    assert "NOT authoritative config" in body


def test_relocation_title_is_a_short_deterministic_summary_when_the_entry_is_long() -> None:
    # Given: an entry far longer than a note title.
    entry_text = "첫 줄 요약 사실\n" + "세부 설명 " * 200

    # When: the relocation is planned twice.
    first = build_relocation_plan(entry_text)
    second = build_relocation_plan(entry_text)

    # Then: the title is non-empty, stable, short, and drawn from the entry's first line.
    assert first.note_plan.title == second.note_plan.title
    assert first.note_plan.title.startswith("첫 줄 요약 사실")
    assert 0 < len(first.note_plan.title) <= 80
    assert "\n" not in first.note_plan.title


def test_relocation_paths_differ_when_the_entries_share_a_long_prefix() -> None:
    # Given: two distinct entries identical for their first 200 characters.
    shared_prefix = "동일한 접두사 " * 30
    first_entry = f"{shared_prefix}첫 번째 사실"
    second_entry = f"{shared_prefix}두 번째 사실"

    # When: both are planned.
    first = build_relocation_plan(first_entry)
    second = build_relocation_plan(second_entry)

    # Then: neither note can overwrite the other.
    assert first.note_plan.relpath != second.note_plan.relpath
    assert first.note_plan.title != second.note_plan.title


def test_note_plan_sha256_binds_path_title_and_body_when_planned() -> None:
    # Given: an operational-fact entry / When: planned.
    plan = build_relocation_plan(_ENTRY)

    # Then: the hash is the NUL-joined preimage of exactly the three planned fields.
    assert plan.note_plan_sha256 == _expected_digest(plan)
    assert re.fullmatch(r"[0-9a-f]{64}", plan.note_plan_sha256)


def test_note_plan_sha256_changes_when_the_entry_changes() -> None:
    # Given: two entries differing only by one character.
    first = build_relocation_plan("<primary-node>가 prod이다.")
    second = build_relocation_plan("<primary-node>가 prod이다!")

    # When / Then: the bound hash separates them.
    assert first.note_plan_sha256 != second.note_plan_sha256


def test_note_plan_sha256_is_independent_of_the_clock_when_planned_repeatedly() -> None:
    # Given: an entry that mentions no date.
    entry_text = _ENTRY

    # When: planned twice and inspected for a rendered timestamp.
    first = build_relocation_plan(entry_text)
    second = build_relocation_plan(entry_text)
    today = datetime.now(UTC).date().isoformat()

    # Then: the hash is clock-free — dates belong to render_note at write time.
    assert first.note_plan_sha256 == second.note_plan_sha256
    assert today not in first.note_plan.body
    assert today not in first.note_plan.title


def test_build_relocation_plan_fails_closed_when_the_entry_is_blank() -> None:
    # Given: an entry with no titleable content / When: planned / Then: nothing is planned.
    with pytest.raises(ObsidianWriteError):
        _ = build_relocation_plan("   \n\t  ")
