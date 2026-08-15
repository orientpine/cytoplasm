"""Unit tests for RAG ingest verification."""

from __future__ import annotations

import json
from pathlib import Path

from automation.memory_relocate.rag_verify import RagVerdict, rag_source_key, verify_ingested
from automation.rag_ingest.chunking import chunk_markdown, parse_frontmatter
from automation.rag_ingest.hashing import doc_fingerprint


def _compute_fp(source_key: str, body: str) -> str:
    _, parsed_body = parse_frontmatter(body)
    chunk_texts = chunk_markdown(parsed_body, 1500)
    chunk_sources_and_contents = [
        (f"{source_key}#c{index:04d}", text)
        for index, text in enumerate(chunk_texts)
    ]
    return doc_fingerprint(chunk_sources_and_contents)


def test_rag_source_key() -> None:
    assert rag_source_key("foo/bar.md") == "obsidian:foo/bar.md"


def test_verify_ingested_state_missing(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    verdict = verify_ingested(state_path, "test.md", "body")
    assert verdict == RagVerdict(False, "state_missing", "obsidian:test.md", None)


def test_verify_ingested_malformed_json(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{malformed", encoding="utf-8")
    
    mtime_before = state_path.stat().st_mtime
    verdict = verify_ingested(state_path, "test.md", "body")
    
    assert verdict == RagVerdict(False, "state_missing", "obsidian:test.md", None)
    assert state_path.stat().st_mtime == mtime_before


def test_verify_ingested_source_absent(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"documents": {}}), encoding="utf-8")
    
    verdict = verify_ingested(state_path, "test.md", "body")
    assert verdict == RagVerdict(False, "source_absent", "obsidian:test.md", None)


def test_verify_ingested_no_points(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "documents": {
            "obsidian:test.md": {
                "fingerprint": "fp123",
                "point_ids": []
            }
        }
    }), encoding="utf-8")
    
    verdict = verify_ingested(state_path, "test.md", "body")
    assert verdict == RagVerdict(False, "no_points", "obsidian:test.md", "fp123")


def test_verify_ingested_fingerprint_mismatch(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "documents": {
            "obsidian:test.md": {
                "fingerprint": "wrong_fp",
                "point_ids": ["uuid-1"]
            }
        }
    }), encoding="utf-8")
    
    verdict = verify_ingested(state_path, "test.md", "body")
    assert verdict == RagVerdict(False, "fingerprint_mismatch", "obsidian:test.md", "wrong_fp")


def test_verify_ingested_happy_path(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    note_body = "---\ntitle: Test\n---\n\n# Heading\n\nSome content."
    source_key = "obsidian:test.md"
    
    expected_fp = _compute_fp(source_key, note_body)
    
    state_path.write_text(json.dumps({
        "documents": {
            source_key: {
                "fingerprint": expected_fp,
                "point_ids": ["uuid-1"]
            }
        }
    }), encoding="utf-8")
    
    # Capture state before
    mtime_before = state_path.stat().st_mtime
    bytes_before = state_path.read_bytes()
    files_before = set(tmp_path.iterdir())
    
    verdict = verify_ingested(state_path, "test.md", note_body)
    
    assert verdict == RagVerdict(True, "", source_key, expected_fp)
    
    # Assert read-only
    assert state_path.stat().st_mtime == mtime_before
    assert state_path.read_bytes() == bytes_before
    assert set(tmp_path.iterdir()) == files_before
