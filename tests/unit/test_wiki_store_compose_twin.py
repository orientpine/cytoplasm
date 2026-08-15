from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

wiki_store = import_module("wiki_store")

BODY = "본문 첫 줄\n"
LEGACY_NOTE = (
    "---\n"
    'title: "Decision Twin"\n'
    "tags: [tag-1, 연구]\n"
    "created: 2026-07-15T00:00:00Z\n"
    "updated: 2026-07-15T00:00:00Z\n"
    "links: [other-note]\n"
    "---\n"
    "본문 첫 줄\n"
)


def _legacy_meta() -> dict[str, str | list[str]]:
    return {
        "title": "Decision Twin",
        "tags": ["tag-1", "연구"],
        "created": "2026-07-15T00:00:00Z",
        "updated": "2026-07-15T00:00:00Z",
        "links": ["other-note"],
    }


def _twin_meta() -> dict[str, str | list[str]]:
    return {
        **_legacy_meta(),
        "kind": "decision",
        "authority": "strict",
        "provenance": "observed",
        "status": "active",
        "review_after": "2026-12-31",
        "supersedes": "previous-note",
    }


def test_compose_note_round_trips_all_decision_twin_keys() -> None:
    # Given
    meta = _twin_meta()

    # When
    serialized = wiki_store.compose_note(meta, BODY)

    # Then
    parsed_meta, parsed_body = wiki_store.parse_note(serialized)
    assert parsed_meta == meta
    assert parsed_body == BODY


def test_compose_note_uses_canonical_twin_key_order() -> None:
    # Given
    meta = _twin_meta()

    # When
    serialized = wiki_store.compose_note(meta, BODY)

    # Then
    assert serialized.splitlines()[:13] == [
        "---",
        'title: "Decision Twin"',
        "tags: [tag-1, 연구]",
        "created: 2026-07-15T00:00:00Z",
        "updated: 2026-07-15T00:00:00Z",
        "links: [other-note]",
        "kind: decision",
        "authority: strict",
        "provenance: observed",
        "status: active",
        "review_after: 2026-12-31",
        "supersedes: previous-note",
        "---",
    ]


def test_compose_note_emits_only_present_twin_keys() -> None:
    # Given
    meta = {**_legacy_meta(), "kind": "note"}

    # When
    serialized = wiki_store.compose_note(meta, BODY)

    # Then
    assert serialized.splitlines()[:8] == [
        "---",
        'title: "Decision Twin"',
        "tags: [tag-1, 연구]",
        "created: 2026-07-15T00:00:00Z",
        "updated: 2026-07-15T00:00:00Z",
        "links: [other-note]",
        "kind: note",
        "---",
    ]


def test_compose_note_preserves_legacy_bytes() -> None:
    # Given
    meta = _legacy_meta()

    # When
    serialized = wiki_store.compose_note(meta, BODY)

    # Then
    assert serialized == LEGACY_NOTE


def test_compose_note_rejects_invalid_twin_meta_before_serializing() -> None:
    # Given
    meta = {**_legacy_meta(), "kind": "decision"}

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.compose_note(meta, BODY)

    # Then
    message = str(raised.value)
    assert "authority: kind가 decision일 때 필수입니다" in message
    assert "provenance: kind가 decision일 때 필수입니다" in message
