from __future__ import annotations

from pathlib import Path

from automation.rag_ingest.chunking import chunk_markdown, parse_frontmatter
from automation.rag_ingest.documents import build_document
from automation.rag_ingest.hashing import content_sha256, doc_fingerprint, document_id
from automation.rag_ingest.metadata import build_metadata
from automation.rag_ingest.sources.discord_team import parse_peer_report

PERSPECTIVE = {
    "agent_id": "agent",
    "owner": "cha",
    "role": "personal-research-agent",
    "project": "autophagy",
    "interest_tags": "autophagy,rag",
}


def test_document_id_mirrors_server_uuid5_derivation() -> None:
    # Given — the exact derivation in configs/rag/mcp/src/rag_mcp/store.py
    from uuid import NAMESPACE_URL, uuid5

    source, content = "wiki:노트.md#c0000", "본문 내용"

    # When / Then
    assert document_id(source, content) == str(uuid5(NAMESPACE_URL, f"{source}\n{content}"))


def test_same_content_always_yields_same_ids_and_fingerprint() -> None:
    # Given
    pairs = [("wiki:a.md#c0000", "alpha"), ("wiki:a.md#c0001", "beta")]

    # When / Then — deterministic => server upsert overwrites, 0 duplicates
    assert doc_fingerprint(pairs) == doc_fingerprint(list(pairs))
    assert document_id(*pairs[0]) == document_id(*pairs[0])
    assert content_sha256("alpha") == content_sha256("alpha")


def test_changed_content_changes_fingerprint_and_id() -> None:
    assert doc_fingerprint([("s", "one")]) != doc_fingerprint([("s", "two")])
    assert document_id("s", "one") != document_id("s", "two")


def test_parse_frontmatter_wiki_format_round_trips() -> None:
    # Given — the exact 5-key format written by skills/wiki wiki_store.compose_note
    text = (
        "---\n"
        'title: "W2-4 검증 노트"\n'
        "tags: [rag, 검증]\n"
        "created: 2026-07-15T07:00:00Z\n"
        "updated: 2026-07-15T07:30:00Z\n"
        "links: [w2-2-백링크-소스]\n"
        "---\n"
        "본문 첫 줄\n"
    )

    # When
    meta, body = parse_frontmatter(text)

    # Then
    assert meta["title"] == "W2-4 검증 노트"
    assert meta["tags"] == "rag,검증"
    assert meta["created"] == "2026-07-15T07:00:00Z"
    assert meta["links"] == "w2-2-백링크-소스"
    assert body == "본문 첫 줄"


def test_parse_frontmatter_absent_returns_full_body() -> None:
    meta, body = parse_frontmatter("그냥 노트")
    assert meta == {}
    assert body == "그냥 노트"


def test_chunk_markdown_packs_sections_and_respects_max() -> None:
    # Given
    body = "# 제목\n" + ("가" * 900) + "\n\n## 절\n" + ("나" * 900)

    # When
    chunks = chunk_markdown(body, max_chars=1000)

    # Then
    assert len(chunks) == 2
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert chunks == chunk_markdown(body, max_chars=1000)  # deterministic


def test_chunk_markdown_hard_splits_oversized_section() -> None:
    chunks = chunk_markdown("x" * 3200, max_chars=1500)
    assert len(chunks) == 3
    assert "".join(chunks) == "x" * 3200


def test_chunk_markdown_empty_body_yields_no_chunks() -> None:
    assert chunk_markdown("   \n  ") == []


def test_build_metadata_attaches_my_perspective_to_team_knowledge() -> None:
    # When
    metadata = build_metadata(PERSPECTIVE, "peer-report", {"task_id": "W1-6"})

    # Then — my-perspective keys always present on team knowledge vectors
    assert metadata["agent_id"] == "agent"
    assert metadata["role"] == "personal-research-agent"
    assert metadata["project"] == "autophagy"
    assert metadata["interest_tags"] == "autophagy,rag"
    assert metadata["source_type"] == "peer-report"
    assert metadata["task_id"] == "W1-6"


def test_build_metadata_extra_never_overrides_perspective() -> None:
    metadata = build_metadata(PERSPECTIVE, "wiki", {"agent_id": "intruder"})
    assert metadata["agent_id"] == "agent"


def test_build_document_adds_chunk_provenance() -> None:
    # When
    document = build_document("wiki:a.md", ["one", "two"], {"source_type": "wiki"})

    # Then
    assert document.chunks[0].source == "wiki:a.md#c0000"
    assert document.chunks[1].source == "wiki:a.md#c0001"
    assert document.chunks[0].metadata["chunk_index"] == "0"
    assert document.chunks[0].metadata["chunk_total"] == "2"
    assert document.chunks[0].metadata["content_sha256"] == content_sha256("one")
    assert len(document.point_ids) == 2


def test_parse_peer_report_accepts_interop_v0_format() -> None:
    # Given — byte-identical to automation.interop.report.format_report output
    from datetime import datetime, timezone

    from automation.interop.report import ReportStatus, TaskReport, format_report

    report = TaskReport(
        agent_id="peer",
        task_id="t_123",
        status=ReportStatus.DONE,
        summary="done summary",
        links=("https://example.internal/x",),
        timestamp=datetime(2026, 7, 15, 8, 0, 0, tzinfo=timezone.utc),
    )

    # When
    parsed = parse_peer_report(format_report(report))

    # Then
    assert parsed is not None
    assert parsed["agent_id"] == "peer"
    assert parsed["task_id"] == "t_123"
    assert parsed["status"] == "done"


def test_parse_peer_report_rejects_non_reports() -> None:
    assert parse_peer_report("일반 대화 메시지") is None
    assert parse_peer_report('```json\n{"version": "v0"}\n```') is None
    assert parse_peer_report('```json\n{not json}\n```') is None


def test_wiki_scan_reads_vault(tmp_path: Path) -> None:
    # Given
    from automation.rag_ingest.sources.files import scan_directory

    note = tmp_path / "노트.md"
    _ = note.write_text(
        "\n".join(
            [
                "---",
                'title: "테스트"',
                "tags: [a]",
                "created: 2026-07-15T00:00:00Z",
                "updated: 2026-07-15T00:00:00Z",
                "links: []",
                "---",
                "위키 본문",
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    # When
    documents, present = scan_directory(tmp_path, "wiki", "wiki", PERSPECTIVE, 1500)

    # Then
    assert present == {"wiki:노트.md"}
    assert len(documents) == 1
    assert documents[0].source_key == "wiki:노트.md"
    assert documents[0].chunks[0].metadata["title"] == "테스트"
    assert documents[0].chunks[0].metadata["source_type"] == "wiki"


def test_wiki_scan_includes_twin_frontmatter_metadata(tmp_path: Path) -> None:
    # Given
    from automation.rag_ingest.sources.files import scan_directory

    note = tmp_path / "decision.md"
    _ = note.write_text(
        "\n".join(
            [
                "---",
                'title: "결정 노트"',
                "kind: decision",
                "authority: default",
                "provenance: stated",
                "status: active",
                "review_after: 2026-12-01",
                "supersedes: old-slug",
                "---",
                "위키 본문",
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    # When
    documents, _ = scan_directory(tmp_path, "wiki", "wiki", PERSPECTIVE, 1500)

    # Then
    metadata = documents[0].chunks[0].metadata
    assert metadata["kind"] == "decision"
    assert metadata["authority"] == "default"
    assert metadata["provenance"] == "stated"
    assert metadata["status"] == "active"
    assert metadata["review_after"] == "2026-12-01"
    assert metadata["supersedes"] == "old-slug"


def test_wiki_scan_leaves_legacy_notes_without_twin_metadata(tmp_path: Path) -> None:
    # Given
    from automation.rag_ingest.sources.files import scan_directory

    note = tmp_path / "legacy.md"
    _ = note.write_text(
        "\n".join(
            [
                "---",
                'title: "레거시 노트"',
                "tags: [a]",
                "created: 2026-07-15T00:00:00Z",
                "updated: 2026-07-15T00:00:00Z",
                "links: []",
                "---",
                "위키 본문",
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    # When
    documents, _ = scan_directory(tmp_path, "wiki", "wiki", PERSPECTIVE, 1500)

    # Then
    metadata = documents[0].chunks[0].metadata
    assert "kind" not in metadata
    assert "authority" not in metadata
    assert "provenance" not in metadata
    assert "status" not in metadata
    assert "review_after" not in metadata
    assert "supersedes" not in metadata


def test_wiki_scan_includes_only_present_twin_metadata(tmp_path: Path) -> None:
    # Given
    from automation.rag_ingest.sources.files import scan_directory

    note = tmp_path / "partial.md"
    _ = note.write_text(
        "\n".join(
            [
                "---",
                'title: "부분 노트"',
                "kind: decision",
                "status: active",
                "---",
                "위키 본문",
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    # When
    documents, _ = scan_directory(tmp_path, "wiki", "wiki", PERSPECTIVE, 1500)

    # Then
    metadata = documents[0].chunks[0].metadata
    assert metadata["kind"] == "decision"
    assert metadata["status"] == "active"
    assert "authority" not in metadata
    assert "provenance" not in metadata
    assert "review_after" not in metadata
    assert "supersedes" not in metadata
