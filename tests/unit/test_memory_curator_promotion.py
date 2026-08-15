"""Contract for turning promotion candidates into twin-draft proposals.

The curator is a *proposer*: per SI-3 it caps its own proposals at
``provenance: observed`` / ``authority: advisory`` — cha's gate ✅ is what
activates (and may upgrade) them.  It never writes the twin itself here;
it only builds the draft args, deterministically and idempotently (a given
entry is proposed once, keyed on a whitespace-insensitive content hash).
"""

from __future__ import annotations

import dataclasses

import pytest

from automation.memory_curator import binding
from automation.memory_curator.effects import _twin_meta_and_body
from automation.memory_curator.model import MemoryEntry
from automation.memory_curator.promotion import (
    PromotionProposal,
    build_proposal,
    content_hash,
    infer_twin_kind,
    new_proposals,
)
from skills.wiki.scripts import wiki_store


def test_content_hash_is_stable_and_whitespace_insensitive() -> None:
    assert content_hash("배려 원칙") == content_hash("  배려   원칙  ")
    assert content_hash("A fact") != content_hash("B fact")


def test_infer_twin_kind() -> None:
    assert infer_twin_kind("앞으로 호의를 갚는 것을 원칙으로 한다") == "principle"
    assert infer_twin_kind("파란색을 선호한다") == "preference"
    assert infer_twin_kind("이 건은 A안으로 결정했다") == "decision"
    assert infer_twin_kind("그냥 어떤 사실") == "principle"  # conservative default


def test_build_proposal_is_observed_advisory_capped() -> None:
    proposal = build_proposal("앞으로 배려를 원칙으로 한다", source_kind="user")
    assert isinstance(proposal, PromotionProposal)
    assert proposal.provenance == "observed"  # SI-3 proposer cap
    assert proposal.authority == "advisory"
    assert proposal.twin_kind == "principle"
    assert proposal.entry_text == "앞으로 배려를 원칙으로 한다"
    assert proposal.slug.startswith("memory-promoted-")
    assert "배려" in proposal.body


def test_build_proposal_body_carries_kind_template_headings() -> None:
    principle = build_proposal("앞으로 X를 원칙으로 한다", source_kind="user")
    assert "## Rule" in principle.body
    preference = build_proposal("커피를 선호한다", source_kind="user")
    assert "## Preference" in preference.body
    decision = build_proposal("이 건은 B로 결정했다", source_kind="memory")
    assert "## Decision" in decision.body


def test_build_proposal_binds_exact_entry_to_slug_and_key() -> None:
    entry_text = "앞으로 X를 원칙으로 한다"
    proposal = build_proposal(entry_text, source_kind="user")
    digest = binding.entry_digest("user", entry_text)

    assert proposal.entry_digest == digest
    assert proposal.promotion_key == binding.promotion_key("user", digest)
    assert proposal.slug == binding.promoted_slug("user", digest)


def test_build_proposal_body_ends_with_deletion_marker() -> None:
    proposal = build_proposal("앞으로 X를 원칙으로 한다", source_kind="memory")
    expected = binding.DeletionMarker(
        version=binding.MARKER_VERSION,
        promotion_key=proposal.promotion_key,
        source_kind=proposal.source_kind,
        entry_digest=proposal.entry_digest,
        delete_after_persist=True,
    )
    marker = binding.parse_marker(proposal.body)

    assert marker == expected
    assert proposal.body.endswith(binding.render_marker(expected))


def test_build_proposal_title_warns_that_approval_deletes_native_entry() -> None:
    proposal = build_proposal("앞으로 X를 원칙으로 한다", source_kind="user")

    assert "삭제" in proposal.title


def test_source_kind_scopes_digest_key_and_slug() -> None:
    entry_text = "동일 내용"
    memory = build_proposal(entry_text, source_kind="memory")
    user = build_proposal(entry_text, source_kind="user")

    assert memory.entry_digest != user.entry_digest
    assert memory.promotion_key != user.promotion_key
    assert memory.slug != user.slug


def test_marker_survives_wiki_note_compose_and_parse() -> None:
    proposal = build_proposal("앞으로 X를 원칙으로 한다", source_kind="user")
    meta, _body = _twin_meta_and_body(proposal, "2026-01-01T00:00:00Z")

    note_text = wiki_store.compose_note(meta, proposal.body)
    parsed_meta, _parsed_body = wiki_store.parse_note(note_text)

    assert parsed_meta == meta
    assert binding.parse_marker(note_text) == binding.parse_marker(proposal.body)


def test_slug_is_stable_for_same_content() -> None:
    a = build_proposal("동일 내용", source_kind="user")
    b = build_proposal("동일 내용", source_kind="user")
    assert a.slug == b.slug


def test_new_proposals_skips_already_proposed() -> None:
    candidates = (
        MemoryEntry("앞으로 배려를 원칙으로 한다"),
        MemoryEntry("파란색 선호"),
    )
    already = {content_hash("앞으로 배려를 원칙으로 한다")}
    proposals = new_proposals(candidates, already, source_kind="user")
    texts = [p.entry_text for p in proposals]
    assert "파란색 선호" in texts
    assert "앞으로 배려를 원칙으로 한다" not in texts


def test_new_proposals_dedupes_within_a_batch() -> None:
    candidates = (MemoryEntry("같은 원칙"), MemoryEntry(" 같은  원칙 "))
    proposals = new_proposals(candidates, set(), source_kind="memory")
    assert len(proposals) == 1


def test_promotion_proposal_is_frozen() -> None:
    proposal = build_proposal("원칙으로 한다", source_kind="memory")

    assert dataclasses.is_dataclass(PromotionProposal)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(proposal, "title", "변경 금지")
