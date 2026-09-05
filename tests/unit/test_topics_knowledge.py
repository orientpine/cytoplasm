from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "topics" / "scripts"
os.environ.setdefault("TOPICS_SCRIPTS", str(SCRIPTS))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "automation" / "research_trends"))

from automation.knowledge.pack import EvidenceItem, EvidencePack, KnowledgeQuery, Verdict  # noqa: E402
from automation.research_trends import research_trends  # noqa: E402
from skills.topics.scripts import topics_cli, topics_knowledge  # noqa: E402


def _item(
    item_id: str = "E1", *, ref: str = "robotics/field-note.md",
    content: str = "현장 로봇 실증 노트", sensitivity: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        item_id, "rag", "note", ref, "관련 노트", "2026-08-18", "path", 0.8,
        True, None, None, sensitivity, content, "a" * 64,
    )


def _pack(
    verdict: Verdict = "hit", *, items: tuple[EvidenceItem, ...] | None = None,
) -> EvidencePack:
    query = KnowledgeQuery("robotics SLAM", "synthesize", caller="topics")
    selected = ((_item(),) if items is None else items) if verdict == "hit" else ()
    state = "hit" if verdict == "hit" else "no_memory" if verdict == "no_evidence" else "unavailable"
    return EvidencePack(
        "knowledge-v1", query, verdict, selected,
        {"rag": state, "wiki": "none" if verdict != "unavailable" else state,
         "twin": "none" if verdict != "unavailable" else state},
    )


def test_research_trends_evidence_excludes_its_own_reingested_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _pack(items=(
        _item("E1", ref="research-trends/research-trends-20260818.md", content="old report"),
        _item("E2", ref="robotics/field-note.md"),
    ))

    class Facade:
        KnowledgeQuery = KnowledgeQuery

        @staticmethod
        def collect_evidence(query: KnowledgeQuery) -> EvidencePack:
            return source

    monkeypatch.setattr(topics_knowledge, "module", lambda name: Facade)
    filtered = topics_knowledge.collect(("robotics", "SLAM"))
    assert [item.ref for item in filtered.items] == ["robotics/field-note.md"]
    assert [item.id for item in filtered.items] == ["E1"]


def test_topics_list_without_facade_renders_unavailable_notice(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    staged_skill = sandbox / "skills" / "topics"
    shutil.copytree(ROOT / "skills" / "topics", staged_skill)
    state = tmp_path / "research-topics.yaml"
    state.write_text('version: 1\ntopics:\n  - "robotics"\n', encoding="utf-8")
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()

    result = subprocess.run(
        [sys.executable, "-I", str(staged_skill / "scripts" / "topics_cli.py"),
         "list", "--with-evidence"],
        cwd=tmp_path,
        env={
            **os.environ,
            "AUTOPHAGY_REPO_ROOT": str(empty_repo),
            "TOPICS_STATE_FILE": str(state),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "근거 수집 불가" in result.stdout


def test_topics_scenario_passes_from_an_isolated_skill_copy(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    staged_skill = sandbox / "skills" / "topics"
    shutil.copytree(ROOT / "skills" / "topics", staged_skill)

    result = subprocess.run(
        ["bash", str(staged_skill / "scripts" / "scenario.sh")],
        cwd=tmp_path,
        env={**os.environ, "AUTOPHAGY_DEMO_SECRET": "DUMMY-unit"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "SCENARIO-PASS" in result.stdout


def test_topics_scenario_passes_when_the_caller_cwd_is_a_checkout(tmp_path: Path) -> None:
    """The node sandbox inherits the release root as cwd; the facade probe must not see it."""
    sandbox = tmp_path / "sandbox"
    staged_skill = sandbox / "skills" / "topics"
    shutil.copytree(ROOT / "skills" / "topics", staged_skill)

    result = subprocess.run(
        ["bash", str(staged_skill / "scripts" / "scenario.sh")],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "AUTOPHAGY_REPO_ROOT": str(ROOT),
            "AUTOPHAGY_DEMO_SECRET": "DUMMY-unit",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "SCENARIO-PASS" in result.stdout


def test_topics_list_hit_pack_renders_related_notes_and_private_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "research-topics.yaml"
    state.write_text('version: 1\ntopics:\n  - "robotics"\n', encoding="utf-8")
    monkeypatch.setenv("TOPICS_STATE_FILE", str(state))

    assert topics_cli.main(["list", "--with-evidence"], evidence_pack=_pack()) == 0

    output = capsys.readouterr().out
    assert "## 내 관련 노트" in output
    assert "[E1] RAG/note: robotics/field-note.md (2026-08-18, path)" in output
    sidecar = state.with_suffix(".evidence.json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["verdict"] == "hit"
    assert sidecar.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(("verdict", "message"), (("no_evidence", "근거 없음"), ("unavailable", "근거 수집 불가")))
def test_topics_list_non_hit_pack_marks_result_and_continues(
    verdict: Verdict, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = tmp_path / "research-topics.yaml"
    state.write_text('version: 1\ntopics:\n  - "robotics"\n', encoding="utf-8")
    monkeypatch.setenv("TOPICS_STATE_FILE", str(state))
    assert topics_cli.main(["list", "--with-evidence"], evidence_pack=_pack(verdict)) == 0
    output = capsys.readouterr().out
    assert "- robotics" in output and message in output


def _stub_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reports: list[str] = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESEARCH_TRENDS_DRY_RUN", "1")
    monkeypatch.setattr(research_trends, "_safe_topics", lambda: ("robotics",))
    monkeypatch.setattr(research_trends, "weekly_quality_section", lambda *_: "")
    monkeypatch.setattr(research_trends, "_write_report", lambda report, day: reports.append(report))
    return reports


def test_research_trends_unavailable_evidence_does_not_block_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = _stub_research(tmp_path, monkeypatch)
    calls = 0

    def run_topics(*args: Any) -> tuple[object, ...]:
        nonlocal calls
        calls += 1
        return ()

    monkeypatch.setattr(research_trends.core, "run_topics", run_topics)
    monkeypatch.setattr(research_trends.topics_knowledge, "collect", lambda topics: _pack("unavailable"))
    assert research_trends.run() == 0
    assert calls == 1 and "## 내 관련 노트" in reports[0]
    assert "근거 수집 불가" in reports[0]


def test_patent_sensitive_related_notes_skip_the_draft_stage_and_use_codex_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2026-09-04 공급자 이관 전에는 초안 단계가 은퇴한 2차 티어로 나갔고 민감 근거는 그 티어를
    # 건너뛰었다. 티어가 하나뿐인 지금도 민감 근거는 초안 단계를 아예 부르지 않고, 정리 단계의
    # 공유 Codex 클라이언트 한 번만 쓴다 — 티어 수가 줄었다고 민감 경로가 넓어지지 않는다.
    _stub_research(tmp_path, monkeypatch)
    calls: list[str] = []
    drafts: list[str] = []
    sensitive = _pack(items=(_item(
        content="[[PATENT-SENSITIVE-RECALL]] patent filing",
        sensitivity="patent-sensitive",
    ),))
    monkeypatch.setattr(research_trends.topics_knowledge, "collect", lambda topics: sensitive)
    monkeypatch.setattr(
        research_trends, "_synthesis", lambda *args, **kwargs: calls.append("synthesis") or "draft"
    )
    monkeypatch.setattr(
        research_trends, "_korean", lambda *args, **kwargs: calls.append("codex") or "정리"
    )

    def run_topics(topics: tuple[str, ...], fetch: Any, summarize: Any, korean: Any) -> tuple[object, ...]:
        paper = research_trends.core.Paper("title", "abstract", "url", "2026-08-01")
        draft = summarize(topics[0], (paper,))
        drafts.append(draft)
        korean(topics[0], (paper,), draft)
        return ()

    monkeypatch.setattr(research_trends.core, "run_topics", run_topics)
    assert research_trends.run() == 0
    assert calls == ["codex"]
    assert drafts == [""]  # 초안 단계는 모델을 부르지 않고 빈 문자열을 낸다.
