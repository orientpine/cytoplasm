from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
import sys

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

wiki_store = import_module("wiki_store")

VALID_NOTE = (
    "---\n"
    'title: "Decision Twin"\n'
    "tags: [tag-1, 연구]\n"
    "created: 2026-07-15T00:00:00Z\n"
    "updated: 2026-07-15T00:00:00Z\n"
    "links: [other-note]\n"
    "---\n"
    "본문 첫 줄\n"
)


def test_parse_note_accepts_valid_five_key_frontmatter() -> None:
    # Given
    expected_meta = {
        "title": "Decision Twin",
        "tags": ["tag-1", "연구"],
        "created": "2026-07-15T00:00:00Z",
        "updated": "2026-07-15T00:00:00Z",
        "links": ["other-note"],
    }

    # When
    meta, body = wiki_store.parse_note(VALID_NOTE)

    # Then
    assert meta == expected_meta
    assert body == "본문 첫 줄\n"


@pytest.mark.parametrize("key", ["extra", "unknown"])
def test_parse_note_rejects_any_non_twin_unknown_frontmatter_key(key: str) -> None:
    # Given
    text = VALID_NOTE.replace("---\n본문", f"{key}: value\n---\n본문")

    # When / Then
    with pytest.raises(wiki_store.SchemaError, match="허용되지 않은 키"):
        wiki_store.parse_note(text)


def test_parse_note_rejects_missing_required_frontmatter_key() -> None:
    # Given
    text = VALID_NOTE.replace("links: [other-note]\n", "")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    assert "필수 키 누락: links" in str(raised.value)


def test_parse_note_rejects_tags_containing_whitespace() -> None:
    # Given
    text = VALID_NOTE.replace("tags: [tag-1, 연구]", "tags: [tag-1, two words]")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    assert "tags: 공백을 포함할 수 없습니다" in str(raised.value)


def test_parse_note_rejects_links_that_are_not_slugs() -> None:
    # Given
    text = VALID_NOTE.replace("links: [other-note]", "links: [folder/note]")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    assert "links: 슬러그 형식이 아닙니다" in str(raised.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("created", "2026-07-15 00:00:00"),
        ("updated", "2026-07-15+00:00"),
    ],
)
def test_parse_note_rejects_non_iso8601_created_and_updated_values(
    key: str,
    value: str,
) -> None:
    # Given
    text = VALID_NOTE.replace(f"{key}: 2026-07-15T00:00:00Z", f"{key}: {value}")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    assert f"{key}: UTC ISO-8601" in str(raised.value)


def test_parse_note_rejects_an_iso_shaped_but_impossible_datetime() -> None:
    # Given
    text = VALID_NOTE.replace(
        "created: 2026-07-15T00:00:00Z",
        "created: 2026-02-30T00:00:00Z",
    )

    # When
    with pytest.raises(wiki_store.SchemaError, match="존재하지 않는 날짜/시각"):
        wiki_store.parse_note(text)


def test_compose_note_and_parse_note_round_trip_meta_and_body() -> None:
    # Given
    meta = {
        "title": "Decision Twin",
        "tags": ["tag-1", "연구"],
        "created": "2026-07-15T00:00:00Z",
        "updated": "2026-07-15T00:30:00Z",
        "links": ["other-note"],
    }
    body = "본문 첫 줄\n\n둘째 줄\n"

    # When
    serialized = wiki_store.compose_note(meta, body)
    parsed_meta, parsed_body = wiki_store.parse_note(serialized)

    # Then
    assert parsed_meta == meta
    assert parsed_body == body


def test_compose_note_adds_trailing_newline_to_nonempty_body() -> None:
    # Given
    meta = {
        "title": "Decision Twin",
        "tags": ["tag-1"],
        "created": "2026-07-15T00:00:00Z",
        "updated": "2026-07-15T00:30:00Z",
        "links": [],
    }
    body = "본문 마지막 줄"

    # When
    parsed_meta, parsed_body = wiki_store.parse_note(wiki_store.compose_note(meta, body))

    # Then
    assert parsed_meta == meta
    assert parsed_body == f"{body}\n"


def test_cleanup_suggestions_emits_all_current_suggestion_types(tmp_path: Path) -> None:
    # Given
    notes = [
        (
            "stale",
            {
                "title": "Stale note",
                "tags": ["old"],
                "created": "2025-12-01T00:00:00Z",
                "updated": "2026-01-01T00:00:00Z",
                "links": [],
            },
            "",
        ),
        (
            "untagged",
            {
                "title": "Untagged note",
                "tags": [],
                "created": "2026-07-20T00:00:00Z",
                "updated": "2026-07-20T00:00:00Z",
                "links": ["stale"],
            },
            "",
        ),
        (
            "orphan",
            {
                "title": "Orphan note",
                "tags": ["tagged"],
                "created": "2026-07-20T00:00:00Z",
                "updated": "2026-07-20T00:00:00Z",
                "links": [],
            },
            "",
        ),
        (
            "duplicate-a",
            {
                "title": "Same title",
                "tags": ["tagged"],
                "created": "2026-07-20T00:00:00Z",
                "updated": "2026-07-20T00:00:00Z",
                "links": ["stale"],
            },
            "",
        ),
        (
            "duplicate-b",
            {
                "title": "Same title",
                "tags": ["tagged"],
                "created": "2026-07-20T00:00:00Z",
                "updated": "2026-07-20T00:00:00Z",
                "links": ["stale"],
            },
            "",
        ),
    ]
    for slug, meta, body in notes:
        (tmp_path / f"{slug}.md").write_text(
            wiki_store.compose_note(meta, body),
            encoding="utf-8",
        )
    fixed_now = datetime(2026, 7, 21, tzinfo=timezone.utc)

    # When
    suggestions = wiki_store.cleanup_suggestions(tmp_path, now=fixed_now)

    # Then
    assert any(item.startswith("STALE stale:") for item in suggestions)
    assert any(item.startswith("UNTAGGED untagged:") for item in suggestions)
    assert any(item.startswith("ORPHAN orphan:") for item in suggestions)
    assert any(item.startswith("DUPLICATE-TITLE duplicate-a, duplicate-b:") for item in suggestions)


def test_slugify_normalizes_title_to_a_slug() -> None:
    assert wiki_store.slugify("  My Decision / Note!  ") == "my-decision-note"


def test_slugify_rejects_titles_without_slug_characters() -> None:
    with pytest.raises(wiki_store.SchemaError, match="title로 슬러그를 만들 수 없습니다"):
        wiki_store.slugify("!!!")
