from __future__ import annotations

import hashlib
from pathlib import Path

from skills.prompt.scripts import prompt_schema, prompt_store

REPO = Path(__file__).resolve().parents[2]


NOW = "2026-07-16T00:00:00Z"


def _store(tmp_path: Path) -> prompt_store.PromptStore:
    return prompt_store.PromptStore(
        prompt_store.StorePaths(
            canonical_root=tmp_path / "canonical",
            overlay_root=tmp_path / "overlay",
            private_root=tmp_path / "private",
            rules_file=REPO / "configs" / "sensitivity-rules.yaml",
        ),
        clock=lambda: NOW,
    )


def _seed(
    root: Path,
    entry_id: str,
    version: int,
    body: str,
) -> Path:
    path = root / entry_id / f"v{version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = prompt_schema.PromptMetadata(
        id=entry_id,
        version=version,
        category="task",
        purpose="fixture-purpose",
        model="any",
        tags=("fixture",),
        created=NOW,
        updated=NOW,
        sensitivity="none",
        body_ref="inline",
    )
    _ = path.write_text(prompt_schema.compose_entry(metadata, body), encoding="utf-8")
    return path


def _draft(entry_id: str, body: str) -> prompt_store.PromptDraft:
    return prompt_store.PromptDraft(
        id=entry_id,
        category="task",
        purpose="fixture-purpose",
        model="any",
        tags=("fixture",),
        body=body,
    )


def test_search_when_body_contains_query_returns_indexed_entry(tmp_path: Path) -> None:
    # Given
    store = _store(tmp_path)
    _ = _seed(store.paths.canonical_root, "lookup-entry", 1, "index-token-01")

    # When
    results = store.search("index-token-01")

    # Then
    assert [(entry.metadata.id, entry.metadata.version) for entry in results] == [
        ("lookup-entry", 1)
    ]


def test_get_when_version_is_omitted_returns_latest_entry(tmp_path: Path) -> None:
    # Given
    store = _store(tmp_path)
    _ = _seed(store.paths.canonical_root, "versioned-entry", 1, "v1-token")
    _ = _seed(store.paths.canonical_root, "versioned-entry", 2, "v2-token")

    # When
    entry = store.get("versioned-entry")

    # Then
    assert entry.metadata.version == 2


def test_get_when_version_is_explicit_returns_requested_entry(tmp_path: Path) -> None:
    # Given
    store = _store(tmp_path)
    _ = _seed(store.paths.canonical_root, "versioned-entry", 1, "v1-token")
    _ = _seed(store.paths.canonical_root, "versioned-entry", 2, "v2-token")

    # When
    entry = store.get("versioned-entry", version=1)

    # Then
    assert entry.metadata.version == 1


def test_add_when_id_is_new_writes_first_version_to_overlay(tmp_path: Path) -> None:
    # Given
    store = _store(tmp_path)

    # When
    result = store.add(_draft("new-entry", "asset-token-01"))

    # Then
    assert result.entry.metadata.version == 1
    assert result.path == store.paths.overlay_root / "new-entry" / "v1.md"
    assert result.path.is_file()


def test_add_when_id_exists_creates_next_immutable_version(tmp_path: Path) -> None:
    # Given
    store = _store(tmp_path)
    first = store.add(_draft("increment-entry", "asset-token-01"))

    # When
    second = store.add(_draft("increment-entry", "asset-token-02"))

    # Then
    assert first.entry.metadata.version == 1
    assert second.entry.metadata.version == 2
    assert first.path.read_bytes() != second.path.read_bytes()


def test_add_when_classifier_matches_writes_private_body_and_metadata_stub(tmp_path: Path) -> None:
    # Given
    store = _store(tmp_path)
    body = (REPO / "skills" / "meeting" / "fixtures" / "meeting-patent.md").read_text(
        encoding="utf-8"
    )

    # When
    result = store.add(_draft("classified-entry", body))

    # Then
    metadata, stub_body = prompt_schema.parse_entry(result.path.read_text(encoding="utf-8"))
    assert result.entry.metadata.sensitivity == "patent-sensitive"
    assert metadata.sensitivity == "patent-sensitive"
    assert metadata.body_ref.startswith("private:")
    assert stub_body == ""
    assert result.private_path is not None
    assert result.private_path.parent.stat().st_mode & 0o777 == 0o700
    assert hashlib.sha256(result.private_path.read_bytes()).hexdigest() == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()


def test_index_when_legacy_file_exists_exposes_read_only_adapter(tmp_path: Path) -> None:
    # Given
    store = _store(tmp_path)
    legacy = store.paths.canonical_root.parent / "meeting-extraction-v7.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    original = "header\n<<<PROMPT>>>\nlegacy-token-07\n"
    _ = legacy.write_text(original, encoding="utf-8")

    # When
    entry = store.get("meeting-extraction", version=7)

    # Then
    assert entry.source == "legacy"
    assert entry.metadata.version == 7
    assert legacy.read_text(encoding="utf-8") == original
