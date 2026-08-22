from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.knowledge.pack import EvidenceItem, EvidencePack, KnowledgeQuery, Verdict  # noqa: E402
from skills.proposal.scripts import proposal_assembly, proposal_cli, proposal_core  # noqa: E402
from skills.proposal.scripts.proposal_storage import ProposalPaths  # noqa: E402

RULES = ROOT / "configs" / "sensitivity-rules.yaml"


def _paths(tmp_path: Path) -> ProposalPaths:
    return ProposalPaths(tmp_path / "proposals", tmp_path / "status", RULES)


def _item(*, content: str = "건설 로보틱스 실증 성과", sensitivity: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        "E1", "rag", "note", "robotics/result.md", "실증 결과", "2026-08-18", "path",
        0.8, True, None, None, sensitivity, content, "a" * 64,
    )


def _pack(verdict: Verdict = "hit", *, item: EvidenceItem | None = None) -> EvidencePack:
    query = KnowledgeQuery("추진전략 건설 로보틱스 과제", "synthesize", caller="proposal")
    items = (_item() if item is None else item,) if verdict == "hit" else ()
    layers = {
        "rag": "hit" if verdict == "hit" else "no_memory" if verdict == "no_evidence" else "unavailable",
        "wiki": "none" if verdict != "unavailable" else "unavailable",
        "twin": "none" if verdict != "unavailable" else "unavailable",
    }
    return EvidencePack("knowledge-v1", query, verdict, items, layers)


def _args(brief: Path) -> argparse.Namespace:
    return argparse.Namespace(
        slug="robotics", section="approach", file=None, text=None,
        brief_file=str(brief), with_evidence=True,
    )


def _proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ProposalPaths, Path]:
    paths = _paths(tmp_path)
    proposal_core.create_proposal(paths, "robotics", "건설 로보틱스 과제", (("approach", "추진전략"),))
    brief = tmp_path / "brief.md"
    brief.write_text("현장 실증 성과를 바탕으로 계획을 작성한다.", encoding="utf-8")
    monkeypatch.setattr(proposal_cli, "_paths", lambda: paths)
    monkeypatch.setattr(proposal_cli, "_kanban", lambda slug: None)
    return paths, brief


def test_hit_pack_adds_prompt_evidence_validates_citations_and_writes_private_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, brief = _proposal(tmp_path, monkeypatch)
    prompts: list[str] = []

    def draft(prompt: str, provider: str, model: str, sensitive: bool) -> str:
        prompts.append(prompt)
        return "실증 성과를 활용한다 [E1]. 팩 밖 주장은 제외한다 [E9]."

    monkeypatch.setattr(proposal_cli.proposal_llm, "run_section_draft", draft)

    assert proposal_cli._draft(_args(brief), evidence_pack=_pack()) == 0

    body = proposal_core.read_section(paths, "robotics", "approach").body
    assert "[E1]" in body and "[E9]" not in body
    assert "### 근거" in body
    assert "[E1] RAG/note: robotics/result.md (2026-08-18, path)" in body
    assert "store=rag" in prompts[0] and "ref=robotics/result.md" in prompts[0]
    assert "date=2026-08-18" in prompts[0] and "건설 로보틱스 실증 성과" in prompts[0]
    assert "Use only MATERIAL/EVIDENCE, cite [En], do not invent" in prompts[0]
    sidecar = paths.workspace_root / "robotics" / "sections" / "01-approach.evidence.json"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["verdict"] == "hit"
    assert sidecar.stat().st_mode & 0o777 == 0o600
    assert "CITATIONS-STRIPPED count=1" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("verdict", "message"),
    (("no_evidence", "근거 없음"), ("unavailable", "근거 수집 불가")),
)
def test_non_hit_pack_adds_deterministic_verdict_and_continues_generation(
    verdict: Verdict, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, brief = _proposal(tmp_path, monkeypatch)
    calls = 0

    def draft(prompt: str, provider: str, model: str, sensitive: bool) -> str:
        nonlocal calls
        calls += 1
        assert message in prompt
        return "생성된 초안"

    monkeypatch.setattr(proposal_cli.proposal_llm, "run_section_draft", draft)

    assert proposal_cli._draft(_args(brief), evidence_pack=_pack(verdict)) == 0

    body = proposal_core.read_section(paths, "robotics", "approach").body
    assert calls == 1
    assert body.startswith(message)


def test_patent_sensitive_evidence_routes_draft_to_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, brief = _proposal(tmp_path, monkeypatch)
    routes: list[tuple[str, str, bool]] = []
    sensitive = _item(content="[[PATENT-SENSITIVE-RECALL]] 비공개 실증", sensitivity="patent-sensitive")

    def draft(prompt: str, provider: str, model: str, marked: bool) -> str:
        routes.append((provider, model, marked))
        return "민감 초안 [E1]"

    monkeypatch.setattr(proposal_cli.proposal_llm, "run_section_draft", draft)

    assert proposal_cli._draft(_args(brief), evidence_pack=_pack(item=sensitive)) == 0
    assert routes == [("openai-codex", "gpt-5.4", True)]


def test_assemble_appends_one_deduplicated_sources_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, brief = _proposal(tmp_path, monkeypatch)
    monkeypatch.setattr(
        proposal_cli.proposal_llm, "run_section_draft",
        lambda prompt, provider, model, sensitive: "근거 기반 초안 [E1]",
    )
    proposal_cli._draft(_args(brief), evidence_pack=_pack())
    proposal_core.add_section(paths, "robotics", "impact", "기대효과")
    proposal_core.write_draft(paths, "robotics", "impact", "같은 근거", evidence_pack=_pack())

    assembled = proposal_assembly.assemble(paths, "robotics")

    assert "## 근거 목록" in assembled.document
    assert assembled.document.count("RAG/note: robotics/result.md") == 1


def test_evidence_json_preview_exposes_only_count_and_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, brief = _proposal(tmp_path, monkeypatch)
    monkeypatch.setattr(proposal_cli.proposal_knowledge, "collect", lambda *args: _pack())
    args = argparse.Namespace(slug="robotics", section="approach", brief_file=str(brief), json=True)

    assert proposal_cli._evidence(args) == 0

    assert json.loads(capsys.readouterr().out) == {"evidence_count": 1, "layers": _pack().layers}
