from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from automation.knowledge.pack import EvidenceItem, EvidencePack, KnowledgeQuery, Verdict  # noqa: E402
from skills.report.scripts import report_cli  # noqa: E402

RULES = ROOT / "configs" / "sensitivity-rules.yaml"


def _item(*, content: str = "건설 로보틱스 실증 성과", sensitivity: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        "E1", "rag", "note", "robotics/result.md", "실증 결과", "2026-08-18", "path",
        0.8, True, None, None, sensitivity, content, "a" * 64,
    )


def _pack(verdict: Verdict = "hit", *, item: EvidenceItem | None = None) -> EvidencePack:
    query = KnowledgeQuery("주간 보고 건설 로보틱스", "synthesize", caller="report")
    items = (_item() if item is None else item,) if verdict == "hit" else ()
    layers = {
        "rag": "hit" if verdict == "hit" else "no_memory" if verdict == "no_evidence" else "unavailable",
        "wiki": "none" if verdict != "unavailable" else "unavailable",
        "twin": "none" if verdict != "unavailable" else "unavailable",
    }
    return EvidencePack("knowledge-v1", query, verdict, items, layers)


def _args(tmp_path: Path) -> argparse.Namespace:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "weekly.md").write_text("# 주간 기록\n\n현장 작업 요약\n", encoding="utf-8")
    return argparse.Namespace(
        notes_root=str(notes), outputs_root=str(tmp_path / "outputs"), query="현장", limit=12,
        title="주간 보고", response_file="", with_evidence=True, period_date=None,
    )


def test_hit_pack_adds_prompt_evidence_validates_citations_and_writes_private_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path)
    prompts: list[str] = []

    def generate(prompt: str, route: Any) -> str:
        prompts.append(prompt)
        return "실증 성과를 반영한다 [E1]. 허위 인용 [E9]."

    monkeypatch.setattr(report_cli.report_llm, "generate", generate)

    assert report_cli._report(args, evidence_pack=_pack()) == 0

    output = next((tmp_path / "outputs").glob("report-*.md"))
    document = output.read_text(encoding="utf-8")
    assert "[E1]" in document and "[E9]" not in document
    assert "## 근거 노트" in document and "## 근거" in document
    assert "[E1] RAG/note: robotics/result.md (2026-08-18, path)" in document
    assert "store=rag" in prompts[0] and "건설 로보틱스 실증 성과" in prompts[0]
    assert "Use only MATERIAL/EVIDENCE, cite [En], do not invent" in prompts[0]
    sidecar = output.with_suffix(".evidence.json")
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
    args = _args(tmp_path)
    calls = 0

    def generate(prompt: str, route: Any) -> str:
        nonlocal calls
        calls += 1
        assert message in prompt
        return "생성된 보고서"

    monkeypatch.setattr(report_cli.report_llm, "generate", generate)

    assert report_cli._report(args, evidence_pack=_pack(verdict)) == 0

    output = next((tmp_path / "outputs").glob("report-*.md"))
    assert calls == 1
    assert message in output.read_text(encoding="utf-8")


def test_patent_sensitive_evidence_routes_report_to_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _args(tmp_path)
    monkeypatch.setenv("REPORT_RULES_PATH", str(RULES))
    routes: list[tuple[str, str, bool]] = []
    sensitive = _item(
        content="[[PATENT-SENSITIVE-RECALL]] patent filing planning",
        sensitivity="patent-sensitive",
    )

    def generate(prompt: str, route: Any) -> str:
        routes.append((route.provider, route.model, route.sensitive))
        return "민감 보고 [E1]"

    monkeypatch.setattr(report_cli.report_llm, "generate", generate)

    assert report_cli._report(args, evidence_pack=_pack(item=sensitive)) == 0
    assert routes == [("openai-codex", "gpt-5.6-sol", True)]


def test_evidence_json_preview_exposes_only_count_and_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(tmp_path)
    args.json = True
    monkeypatch.setattr(report_cli.report_knowledge, "collect", lambda *args: _pack())

    assert report_cli._evidence(args) == 0
    assert json.loads(capsys.readouterr().out) == {"evidence_count": 1, "layers": _pack().layers}
