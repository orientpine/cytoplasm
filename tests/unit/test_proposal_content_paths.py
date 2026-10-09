"""Document words do not change proposal processing or citation eligibility."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from skills.proposal.engine.agents.hermes_client import HermesLLMClient
from skills.proposal.engine.contracts import Claim, SectionDraft
from skills.proposal.engine.converter.corpus_lint import lint
from skills.proposal.engine.converter.ingest import ingest_dir
from skills.proposal.engine.converter.materialize import materialize
from skills.proposal.engine.converter.normalize import normalize
from skills.proposal.engine.converter.pms import ProposalMaterialStore
from skills.proposal.engine.converter.sanitize import sanitize_gate
from skills.proposal.engine.converter.traceability_graph import build_evidence_graph
from skills.proposal.scripts import proposal_cli, proposal_core, proposal_corpus, proposal_knowledge
from skills.proposal.scripts.proposal_storage import ProposalPaths


WORDS = ("clean", "특허 patent 기밀", "NDA 비공개 기술이전", "협력기관 계약금액 3억원")


@pytest.mark.parametrize("words", WORDS)
def test_corpus_text_reaches_store_and_graph_without_a_content_verdict(
    tmp_path: Path, words: str,
) -> None:
    # Given: a real file and the same source metadata for every text.
    corpus, candidate = tmp_path / "corpus", tmp_path / "candidate"
    corpus.mkdir()
    candidate.mkdir()
    fact = words + " 연구 목표를 검증한다"
    (candidate / "source.md").write_text(
        "---\nsource_url: https://example.org/source\n---\n" + fact + "\n",
        encoding="utf-8",
    )

    # When: ingestion, normalization, materialization and lint run on real bytes.
    units = materialize([normalize(raw) for raw in ingest_dir(str(candidate))])
    pms = ProposalMaterialStore(units)
    drafts = [SectionDraft("0", "개요", fact, [Claim(fact, [units[0].unit_id])])]
    graph = build_evidence_graph(pms, drafts)

    # Then: all evidence survives, the graph is connected, and no content tag is emitted.
    assert lint(corpus, candidate) == 0
    assert pms.public_evidence() == units
    assert units[0].fact == fact
    assert set(units[0].model_dump()) == {"unit_id", "fact", "provenances", "bucket", "conflict"}
    assert graph.coverage_score == 1.0
    assert graph.evidence_nodes[0].model_dump() == {"source_id": units[0].unit_id}
    assert sanitize_gate(fact, pms).ok


@pytest.mark.parametrize("words", WORDS)
def test_owner_summary_handoff_preserves_text_without_content_tags(
    tmp_path: Path, words: str,
) -> None:
    # Given: source access is already summarized by the knowledge facade.
    item = proposal_knowledge.EvidenceItem("wiki:demo", "wiki-twin", words, 0.9, None)
    pack = proposal_knowledge.EvidencePack("goal", (item,), (), ())

    # When: the brief is serialized, re-read, and converted into owner evidence.
    brief = proposal_corpus.proposal_research.write_research_brief(tmp_path, "goal", pack)
    restored = proposal_corpus._pack_from_brief(brief)
    files = proposal_corpus._write_owner_evidence(tmp_path, restored)

    # Then: the same summary and provenance survive without classification metadata.
    assert restored.items[0].summary == item.summary
    assert restored.items[0].source_key == item.source_key
    assert files[0].read_text(encoding="utf-8") == (
        "---\nsource_key: wiki:demo\n---\n" + words + "\n"
    )


@pytest.mark.parametrize("words", WORDS)
def test_draft_brief_reaches_shared_model_without_keyword_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, words: str,
) -> None:
    # Given: a workspace with the same shape and no rule configuration.
    paths = ProposalPaths(tmp_path / "workspace", tmp_path / "status")
    proposal_core.create_proposal(paths, "demo", "Demo", (("overview", "개요"),))
    brief = tmp_path / "brief.md"
    brief.write_text(words, encoding="utf-8")
    monkeypatch.setattr(proposal_cli, "_paths", lambda: paths)
    monkeypatch.setattr(proposal_cli, "_kanban", lambda _slug: None)
    monkeypatch.setenv("PROPOSAL_LLM_LOG_ROOT", str(tmp_path / "logs"))
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", "/bin/echo")
    args = argparse.Namespace(
        slug="demo", section="overview", file=None, text=None,
        brief_file=str(brief), with_evidence=False,
    )

    # When: the real draft boundary and shared model transport run.
    assert proposal_cli._draft(args) == 0

    # Then: echo's actual prompt becomes the stored draft and the log has no verdict.
    assert "SECTION BRIEF:\n" + words in proposal_core.read_section(paths, "demo", "overview").body
    record = json.loads((tmp_path / "logs" / "llm-calls.jsonl").read_text(encoding="utf-8"))
    assert set(record) == {"run_id", "stage", "provider", "model", "served_model", "served_provider"}


@pytest.mark.parametrize("words", WORDS)
def test_engine_backend_sends_keywords_without_provider_ban(words: str) -> None:
    # Given: an injectable Hermes runner; account config, not content, picks routing.
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="draft", stderr="")

    client = HermesLLMClient("litellm", "glm", runner=runner)
    # When: the backend processes the text.
    assert client.complete("writer", words) == "draft"
    # Then: exactly one unchanged payload is sent and no argv pins the account model.
    assert len(calls) == 1
    assert calls[0][2].endswith("\n\n" + words)
    assert "--provider" not in calls[0] and "-m" not in calls[0]


def test_failed_source_cross_reference_still_blocks_verbatim_leaks(tmp_path: Path) -> None:
    # Given: an otherwise ordinary source that failed explicit cross-reference verification.
    fact = "An unverified source contains this sufficiently long verbatim passage"
    (tmp_path / "source.md").write_text(fact, encoding="utf-8")
    units = materialize([normalize(raw) for raw in ingest_dir(str(tmp_path))])
    pms = ProposalMaterialStore(units, crossref_verify=lambda _ref: False)
    # When / Then: eligibility and the full-verbatim leak scan stay closed.
    assert pms.public_evidence() == []
    report = sanitize_gate(fact, pms)
    assert not report.ok
    assert report.violations[0].span == fact
