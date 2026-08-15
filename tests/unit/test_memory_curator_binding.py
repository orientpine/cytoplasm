"""Deletion-safety bindings for native-memory promotion."""

from __future__ import annotations

import re

import pytest

from automation.memory_curator.binding import (
    MARKER_VERSION,
    DeletionMarker,
    PromotionReceipt,
    entry_digest,
    parse_marker,
    promoted_slug,
    promotion_key,
    render_marker,
)


def test_entry_digest_is_stable_when_inputs_are_unchanged() -> None:
    # Given: one exact native-memory entry.
    source_kind = "memory"
    entry_text = "exact entry"

    # When: its digest is calculated repeatedly.
    first = entry_digest(source_kind, entry_text)
    second = entry_digest(source_kind, entry_text)

    # Then: the digest is stable, lowercase hexadecimal, and bound to the v1 preimage.
    assert first == second == "048b85b425d8cf5244d05f0177f0c869db6a1afdbc167e9a3fa05fe5bae70d70"
    assert re.fullmatch(r"[0-9a-f]{64}", first)


def test_entry_digest_is_whitespace_sensitive_when_spacing_changes() -> None:
    # Given: two entries differing only by one space / When hashed / Then they remain distinct.
    assert entry_digest("memory", "a b") != entry_digest("memory", "a  b")


def test_binding_identifiers_are_source_qualified_when_text_matches() -> None:
    # Given: identical text from the two native-memory files.
    memory_digest = entry_digest("memory", "same text")
    user_digest = entry_digest("user", "same text")

    # When: deletion and wiki identifiers are derived.
    memory_key = promotion_key("memory", memory_digest)
    user_key = promotion_key("user", user_digest)
    memory_slug = promoted_slug("memory", memory_digest)
    user_slug = promoted_slug("user", user_digest)

    # Then: every binding remains source-qualified.
    assert memory_digest != user_digest
    assert memory_key != user_key
    assert memory_slug != user_slug
    assert memory_key == f"memory:{memory_digest}"
    assert user_key == f"user:{user_digest}"


def test_promoted_slug_obeys_wiki_slug_contract() -> None:
    # Given: a source-qualified digest / When made into a slug / Then it is wiki-safe.
    slug = promoted_slug("memory", entry_digest("memory", "한국어 entry"))

    assert re.match(r"^[0-9A-Za-z가-힣._-]+$", slug)
    assert len(slug) <= 64
    assert slug == slug.lower()


def test_rendered_marker_round_trips_to_equal_value() -> None:
    # Given: an authorization marker for one exact entry.
    digest = entry_digest("memory", "delete only this exact entry")
    marker = DeletionMarker(
        version=MARKER_VERSION,
        promotion_key=promotion_key("memory", digest),
        source_kind="memory",
        entry_digest=digest,
        delete_after_persist=True,
    )

    # When: rendered into and parsed from a wiki note body.
    rendered = render_marker(marker)
    parsed = parse_marker(f"# Note\n\nBody\n\n{rendered}\n")

    # Then: the marker is single-line, deterministic, and lossless.
    assert "\n" not in rendered
    assert render_marker(marker) == rendered
    assert parsed == marker


def test_parse_marker_returns_none_when_marker_is_absent() -> None:
    # Given: ordinary wiki text / When parsed / Then no deletion authority is inferred.
    assert parse_marker("# Note\n\nNo marker here.") is None


def test_parse_marker_returns_none_when_marker_is_malformed() -> None:
    # Given: a marker-shaped comment missing required fields / When parsed / Then it fails closed.
    malformed = "<!-- mc-marker-v1 key=memory:abc kind=memory delete-after-persist=true -->"

    assert parse_marker(malformed) is None


def test_parse_marker_returns_none_when_marker_version_is_wrong() -> None:
    # Given: a structurally complete marker with an unsupported version.
    digest = entry_digest("memory", "versioned entry")
    wrong_version = (
        f"<!-- mc-marker-v0 key=memory:{digest} kind=memory digest={digest} "
        "delete-after-persist=true -->"
    )

    # When parsed / Then it grants no deletion authority.
    assert parse_marker(wrong_version) is None


def test_parse_marker_returns_none_when_two_markers_are_present() -> None:
    # Given: two independently well-formed markers in one note.
    digest = entry_digest("user", "ambiguous entry")
    marker = DeletionMarker(
        version=MARKER_VERSION,
        promotion_key=promotion_key("user", digest),
        source_kind="user",
        entry_digest=digest,
        delete_after_persist=False,
    )
    rendered = render_marker(marker)

    # When parsed / Then ambiguity fails closed.
    assert parse_marker(f"{rendered}\n{rendered}") is None


@pytest.mark.parametrize("unsafe_value", ["line1\nline2", "value-->suffix"])
def test_render_marker_rejects_unsafe_field_values(unsafe_value: str) -> None:
    # Given: a field value capable of escaping the single-line HTML comment.
    marker = DeletionMarker(
        version=MARKER_VERSION,
        promotion_key=unsafe_value,
        source_kind="memory",
        entry_digest="a" * 64,
        delete_after_persist=True,
    )

    # When rendered / Then unsafe wiki markup is rejected.
    with pytest.raises(ValueError):
        _ = render_marker(marker)


def test_promotion_receipt_preserves_all_persistence_bindings() -> None:
    # Given and When: the downstream persistence receipt is constructed.
    receipt = PromotionReceipt(
        draft_id="draft-1",
        confirm_message_id="message-1",
        slug="memory-promoted-memory-deadbeef",
        note_sha256="f" * 64,
    )

    # Then: every frozen API field remains available unchanged.
    assert receipt == PromotionReceipt(
        draft_id="draft-1",
        confirm_message_id="message-1",
        slug="memory-promoted-memory-deadbeef",
        note_sha256="f" * 64,
    )
