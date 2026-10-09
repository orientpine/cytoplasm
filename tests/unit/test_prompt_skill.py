from __future__ import annotations

from pathlib import Path
import pytest

from skills.prompt.scripts import prompt_schema, prompt_store

REPO = Path(__file__).resolve().parents[2]


NOW = "2026-07-16T00:00:00Z"


def _store(tmp_path: Path) -> prompt_store.PromptStore:
    return prompt_store.PromptStore(
        prompt_store.StorePaths(
            canonical_root=tmp_path / "canonical",
            overlay_root=tmp_path / "overlay",
            private_root=tmp_path / "private",
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


@pytest.mark.parametrize("body", ["ordinary asset", "특허 patent 기밀"])
def test_add_keywords_uses_same_inline_overlay(tmp_path: Path, body: str) -> None:
    # Given
    store = _store(tmp_path)

    # When
    result = store.add(_draft("keyword-entry", body))

    # Then
    metadata, stored_body = prompt_schema.parse_entry(result.path.read_text(encoding="utf-8"))
    assert metadata.body_ref == "inline"
    assert stored_body == body + "\n"
    assert store.get("keyword-entry").body == body + "\n"
    assert result.path.parent.stat().st_mode & 0o777 == 0o700
    assert result.path.stat().st_mode & 0o777 == 0o600
    assert not store.paths.private_root.exists()


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


def test_old_separate_body_is_read_without_moving_or_rewriting_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = _seed(store.paths.overlay_root, "old-entry", 1, "")
    opaque = "a" * 32
    original = path.read_text().replace(
        "body_ref: inline", f"sensitivity: retired\nbody_ref: private:{opaque}"
    )
    path.write_text(original)
    store.paths.private_root.mkdir()
    body_path = store.paths.private_root / f"{opaque}.md"
    body_path.write_text("stored original\n")

    entry = store.get("old-entry")

    assert entry.body == "stored original\n"
    assert path.read_text() == original
    assert body_path.read_text() == "stored original\n"


@pytest.mark.parametrize("entry_id", ["../escape", "/absolute", "a/b"])
def test_add_refuses_unsafe_identifiers(tmp_path: Path, entry_id: str) -> None:
    store = _store(tmp_path)
    with pytest.raises(prompt_schema.PromptSchemaError):
        store.add(_draft(entry_id, "body"))
    assert not store.paths.overlay_root.exists()


def test_old_body_reference_cannot_traverse_private_root(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = _seed(store.paths.overlay_root, "old-entry", 1, "")
    path.write_text(path.read_text().replace("body_ref: inline", "body_ref: private:../escape"))
    with pytest.raises(prompt_schema.PromptSchemaError):
        store.get("old-entry")
