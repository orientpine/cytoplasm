from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
import sys
from typing import Final

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

wiki_store = import_module("wiki_store")

FIXED_NOW = datetime(2026, 7, 21, tzinfo=timezone.utc)
BASE_META: Final = {
    "title": "Twin note",
    "tags": ["decision"],
    "created": "2026-07-20T00:00:00Z",
    "updated": "2026-07-20T00:00:00Z",
    "links": [],
    "kind": "decision",
    "authority": "default",
    "provenance": "stated",
    "status": "active",
}


def _write_note(root: Path, slug: str, changes: Mapping[str, str | list[str]]) -> None:
    meta = {**BASE_META, "title": slug.replace("-", " ")}
    meta.update(changes)
    (root / f"{slug}.md").write_text(wiki_store.compose_note(meta, ""), encoding="utf-8")


def test_cleanup_suggestions_flags_expired_review_and_not_future_or_today(
    tmp_path: Path,
) -> None:
    # Given: date-only UTC comparison; review_after == today is not expired.
    _write_note(tmp_path, "expired", {"review_after": "2026-07-20", "links": ["future"]})
    _write_note(tmp_path, "future", {"review_after": "2026-07-22", "links": ["today"]})
    _write_note(tmp_path, "today", {"review_after": "2026-07-21", "links": ["expired"]})

    # When
    suggestions = wiki_store.cleanup_suggestions(tmp_path, now=FIXED_NOW)

    # Then
    assert (
        "REVIEW-EXPIRED expired: review_after 2026-07-20 경과 — 재확인/강등 검토"
        in suggestions
    )
    assert not any(item.startswith("REVIEW-EXPIRED future:") for item in suggestions)
    assert not any(item.startswith("REVIEW-EXPIRED today:") for item in suggestions)


def test_cleanup_suggestions_flags_dangling_supersedes_but_not_existing_target(
    tmp_path: Path,
) -> None:
    # Given
    _write_note(tmp_path, "dangling", {"supersedes": "nonexistent-slug", "links": ["target"]})
    _write_note(tmp_path, "target", {})
    _write_note(tmp_path, "existing", {"supersedes": "target", "links": ["target"]})

    # When
    suggestions = wiki_store.cleanup_suggestions(tmp_path, now=FIXED_NOW)

    # Then
    dangling = [item for item in suggestions if item.startswith("SUPERSEDES-DANGLING")]
    assert len(dangling) == 1
    assert dangling[0].startswith("SUPERSEDES-DANGLING dangling:")
    assert not any(item.startswith("SUPERSEDES-DANGLING existing:") for item in suggestions)


def test_cleanup_suggestions_keeps_legacy_vault_to_existing_four_types(
    tmp_path: Path,
) -> None:
    # Given
    legacy_notes = [
        (
            "stale",
            {
                "title": "Stale note",
                "tags": ["old"],
                "created": "2025-12-01T00:00:00Z",
                "updated": "2026-01-01T00:00:00Z",
                "links": ["untagged"],
            },
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
        ),
    ]
    for slug, meta in legacy_notes:
        (tmp_path / f"{slug}.md").write_text(
            wiki_store.compose_note(meta, ""),
            encoding="utf-8",
        )

    # When
    suggestions = wiki_store.cleanup_suggestions(tmp_path, now=FIXED_NOW)

    # Then
    assert sorted(item.split(" ", 1)[0] for item in suggestions) == sorted([
        "STALE",
        "UNTAGGED",
        "ORPHAN",
        "DUPLICATE-TITLE",
    ])
