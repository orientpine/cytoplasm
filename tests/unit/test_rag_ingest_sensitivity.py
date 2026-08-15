from __future__ import annotations

from pathlib import Path

import pytest

from automation.rag_ingest.sensitivity import classify, load_rules
from automation.rag_ingest.sources.obsidian import (
    DEFAULT_EXCLUDE_NAMES,
    ObsidianSyncError,
    scan_obsidian,
)

PERSPECTIVE = {
    "agent_id": "agent",
    "owner": "cha",
    "role": "personal-research-agent",
    "project": "autophagy",
    "interest_tags": "autophagy,rag",
}


def _rules_path() -> Path:
    return Path(__file__).parents[2] / "configs" / "sensitivity-rules.yaml"


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def test_classify_tags_patent_sensitive_when_korean_keyword_matches() -> None:
    # Given
    rules = load_rules(_rules_path())

    # When
    tags = classify("새 특허 명세서 초안", rules)

    # Then
    assert tags == frozenset({"patent-sensitive"})


def test_classify_tags_patent_sensitive_when_regex_pattern_matches() -> None:
    # Given
    rules = load_rules(_rules_path())

    text = "The disclosure mentions a PCT route before publication."

    # When
    tags = classify(text, rules)

    # Then
    assert tags == frozenset({"patent-sensitive"})


def test_scan_obsidian_tags_whole_document_when_any_chunk_matches_keyword(
    tmp_path: Path,
) -> None:
    # Given
    text = "# A\n특허 메모\n\n# B\n공개 가능한 일반 문단입니다."
    _write(tmp_path, "000_PARA/patent.md", text)

    # When
    documents, present = scan_obsidian(
        tmp_path,
        DEFAULT_EXCLUDE_NAMES,
        PERSPECTIVE,
        16,
        sensitivity_rules_path=_rules_path(),
    )

    # Then
    assert present == {"obsidian:000_PARA/patent.md"}
    assert len(documents) == 1
    assert len(documents[0].chunks) > 1
    assert all(
        chunk.metadata["sensitivity"] == "patent-sensitive"
        for chunk in documents[0].chunks
    )


def test_scan_obsidian_tags_document_when_regex_pattern_matches(tmp_path: Path) -> None:
    # Given
    _write(tmp_path, "000_PARA/pct.md", "# Route\nPCT filing route memo")

    # When
    documents, _present = scan_obsidian(
        tmp_path,
        DEFAULT_EXCLUDE_NAMES,
        PERSPECTIVE,
        1500,
        sensitivity_rules_path=_rules_path(),
    )

    # Then
    assert documents[0].chunks[0].metadata["sensitivity"] == "patent-sensitive"


def test_scan_obsidian_omits_sensitivity_metadata_when_document_is_clean(
    tmp_path: Path,
) -> None:
    # Given
    _write(tmp_path, "000_PARA/clean.md", "# 공개 노트\n일반 연구 일정")

    # When
    documents, _present = scan_obsidian(
        tmp_path,
        DEFAULT_EXCLUDE_NAMES,
        PERSPECTIVE,
        1500,
        sensitivity_rules_path=_rules_path(),
    )

    # Then
    assert "sensitivity" not in documents[0].chunks[0].metadata


def test_scan_obsidian_raises_when_rules_file_is_missing(tmp_path: Path) -> None:
    # Given
    _write(tmp_path, "000_PARA/note.md", "# Note\n특허")

    # When / Then
    with pytest.raises(ObsidianSyncError):
        scan_obsidian(
            tmp_path,
            DEFAULT_EXCLUDE_NAMES,
            PERSPECTIVE,
            1500,
            sensitivity_rules_path=tmp_path / "missing.yaml",
        )


def test_scan_obsidian_raises_when_rules_file_is_unparseable(tmp_path: Path) -> None:
    # Given
    _write(tmp_path, "000_PARA/note.md", "# Note\n특허")
    rules_path = tmp_path / "bad.yaml"
    _ = rules_path.write_text(
        "version: 1\ntags:\n  patent-sensitive:\n    patterns:\n      - \"(?i)(broken\"\n",
        encoding="utf-8",
    )

    # When / Then
    with pytest.raises(ObsidianSyncError):
        scan_obsidian(
            tmp_path,
            DEFAULT_EXCLUDE_NAMES,
            PERSPECTIVE,
            1500,
            sensitivity_rules_path=rules_path,
        )


def test_scan_obsidian_without_rules_path_stays_backward_compatible(
    tmp_path: Path,
) -> None:
    # Given
    _write(tmp_path, "000_PARA/patent.md", "# Note\n특허")

    # When
    documents, _present = scan_obsidian(
        tmp_path,
        DEFAULT_EXCLUDE_NAMES,
        PERSPECTIVE,
        1500,
    )

    # Then
    assert "sensitivity" not in documents[0].chunks[0].metadata
