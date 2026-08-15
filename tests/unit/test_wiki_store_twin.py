"""DT-B1: decision-twin frontmatter schema v1 — typed optional twin keys.

Twin keys are validation-only in this wave (compose_note serialization is DT-B2),
so twin frontmatter is built as raw text here.
"""
from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

wiki_store = import_module("wiki_store")

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


def _note_with_twin(*lines: str) -> str:
    inserted = "".join(f"{line}\n" for line in lines)
    return LEGACY_NOTE.replace("---\n본문", f"{inserted}---\n본문")


def test_parse_note_accepts_legacy_note_without_twin_keys() -> None:
    # Given: a pre-twin note carrying exactly the 5 required keys (whole existing vault shape)

    # When
    meta, body = wiki_store.parse_note(LEGACY_NOTE)

    # Then
    assert set(meta) == set(wiki_store.REQUIRED_KEYS)
    assert body == "본문 첫 줄\n"


def test_parse_note_accepts_decision_with_authority_and_provenance() -> None:
    # Given
    text = _note_with_twin("kind: decision", "authority: default", "provenance: stated")

    # When
    meta, _ = wiki_store.parse_note(text)

    # Then
    assert meta["kind"] == "decision"
    assert meta["authority"] == "default"
    assert meta["provenance"] == "stated"


def test_parse_note_accepts_note_kind_with_all_optional_twin_keys() -> None:
    # Given
    text = _note_with_twin(
        "kind: note",
        "status: superseded",
        "review_after: 2026-12-31",
        "supersedes: other-note",
    )

    # When
    meta, _ = wiki_store.parse_note(text)

    # Then
    assert meta["kind"] == "note"
    assert meta["status"] == "superseded"
    assert meta["review_after"] == "2026-12-31"
    assert meta["supersedes"] == "other-note"


def test_parse_note_rejects_twin_key_without_kind() -> None:
    # Given
    text = _note_with_twin("authority: default")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    message = str(raised.value)
    assert "kind" in message
    assert "필수" in message


@pytest.mark.parametrize("kind", ["decision", "principle", "preference"])
def test_parse_note_rejects_judgment_kind_without_authority_and_provenance(kind: str) -> None:
    # Given
    text = _note_with_twin(f"kind: {kind}")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    message = str(raised.value)
    assert f"authority: kind가 {kind}일 때 필수입니다" in message
    assert f"provenance: kind가 {kind}일 때 필수입니다" in message


@pytest.mark.parametrize(
    ("lines", "key", "allowed"),
    [
        (("kind: rule",), "kind", "decision, principle, preference, note"),
        (
            ("kind: decision", "authority: binding", "provenance: stated"),
            "authority",
            "strict, default, advisory",
        ),
        (
            ("kind: decision", "authority: default", "provenance: guessed"),
            "provenance",
            "stated, observed, inferred",
        ),
        (("kind: note", "status: retired"), "status", "active, superseded, archived"),
    ],
)
def test_parse_note_rejects_enum_violation_listing_allowed_values(
    lines: tuple[str, ...],
    key: str,
    allowed: str,
) -> None:
    # Given
    text = _note_with_twin(*lines)

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    message = str(raised.value)
    assert f"{key}:" in message
    assert allowed in message


def test_parse_note_rejects_review_after_not_in_date_format() -> None:
    # Given
    text = _note_with_twin("kind: note", "review_after: 2026/12/31")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    assert "review_after: YYYY-MM-DD" in str(raised.value)


def test_parse_note_rejects_review_after_impossible_date() -> None:
    # Given
    text = _note_with_twin("kind: note", "review_after: 2026-13-40")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    assert "review_after: 존재하지 않는 날짜" in str(raised.value)


def test_parse_note_rejects_supersedes_that_is_not_a_slug() -> None:
    # Given
    text = _note_with_twin("kind: note", "supersedes: folder/note")

    # When
    with pytest.raises(wiki_store.SchemaError) as raised:
        wiki_store.parse_note(text)

    # Then
    assert "supersedes: 슬러그 형식이 아닙니다" in str(raised.value)


def test_parse_note_still_rejects_unknown_key_confidence() -> None:
    # Given: `confidence` was explicitly killed in schema v1 — not in TWIN_KEYS
    text = _note_with_twin("confidence: high")

    # When / Then
    with pytest.raises(wiki_store.SchemaError, match="허용되지 않은 키"):
        wiki_store.parse_note(text)
