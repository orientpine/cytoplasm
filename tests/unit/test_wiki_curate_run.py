"""큐레이션 실행 경로 — 증류는 주입, 저장은 게이트, 상한은 상태가 센다.

증류에는 `automation/twin_distill/llm.py` 의 `LlmClient` Protocol 을 그대로 쓴다.
새 LLM 경로·새 예산 경로를 만들지 않기 위해서다. patent-sensitive 원천은 후보 선정에서
이미 걸러지므로 프롬프트에 도달하지 않는다(구성상 보장, 아래에서 확인한다).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from automation.wiki_curate.candidates import SourceNote, select_candidates
from automation.wiki_curate.distill import DistillRefused, distilled_body, render_prompt
from automation.wiki_curate.run import CurationPlan, run_curation

_CLOCK = datetime(2026, 8, 21, tzinfo=timezone.utc)


class _FakeClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def _source(ref: str = "projects/kimm.md", *, sensitivity: str | None = None) -> SourceNote:
    return SourceNote(
        ref=ref, title="KIMM 협업 조건", body="2026-05-02 회의에서 조건을 합의했다.",
        tags=("연구",), sensitivity=sensitivity, event_date="2026-05-02", entities=("김박사",),
    )


def _candidate(ref: str = "projects/kimm.md"):
    return select_candidates((_source(ref),), existing_digests=frozenset(), limit=1, clock=lambda: _CLOCK)[0]


def test_prompt_carries_the_source_text_and_its_origin() -> None:
    prompt = render_prompt(_candidate())
    assert "2026-05-02 회의에서 조건을 합의했다." in prompt
    assert "projects/kimm.md" in prompt


def test_distilled_body_keeps_the_origin_line() -> None:
    client = _FakeClient("## 요약\n조건에 합의했다.")
    body = distilled_body(_candidate(), client=client)
    assert "조건에 합의했다." in body
    assert "projects/kimm.md" in body
    assert len(client.prompts) == 1


def test_empty_distillation_is_refused_rather_than_drafted() -> None:
    with pytest.raises(DistillRefused):
        distilled_body(_candidate(), client=_FakeClient("   "))


def test_dry_run_plans_without_touching_the_gate_or_the_quota(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    plan = run_curation(
        sources=(_source(),),
        existing_digests=frozenset(),
        state_path=tmp_path / "state.json",
        cli_path=Path("/live/wiki/scripts/wiki_cli.py"),
        workspace=tmp_path / "work",
        client=_FakeClient("## 요약\n합의."),
        runner=lambda argv: calls.append(argv) or 0,
        clock=lambda: _CLOCK,
        cap=5,
        emit=False,
    )
    assert isinstance(plan, CurationPlan)
    assert [candidate.source_ref for candidate in plan.candidates] == ["projects/kimm.md"]
    assert plan.emitted == 0
    assert calls == []
    assert not (tmp_path / "state.json").exists()


def test_emit_sends_each_candidate_through_the_draft_gate_and_spends_quota(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    plan = run_curation(
        sources=(_source("a.md"), _source("b.md")),
        existing_digests=frozenset(),
        state_path=tmp_path / "state.json",
        cli_path=Path("/live/wiki/scripts/wiki_cli.py"),
        workspace=tmp_path / "work",
        client=_FakeClient("## 요약\n합의."),
        runner=lambda argv: calls.append(argv) or 0,
        clock=lambda: _CLOCK,
        cap=5,
        emit=True,
    )
    assert plan.emitted == 2
    assert [argv[1] for argv in calls] == ["draft", "draft"]
    assert all("--review-after" in argv for argv in calls)
    from automation.wiki_curate.state import remaining_quota
    assert remaining_quota(tmp_path / "state.json", cap=5, clock=lambda: _CLOCK) == 3


def test_weekly_quota_bounds_the_batch(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    sources = tuple(_source(f"n{index}.md") for index in range(4))
    plan = run_curation(
        sources=sources, existing_digests=frozenset(), state_path=tmp_path / "state.json",
        cli_path=Path("/live/wiki/scripts/wiki_cli.py"), workspace=tmp_path / "work",
        client=_FakeClient("## 요약\n합의."), runner=lambda argv: calls.append(argv) or 0,
        clock=lambda: _CLOCK, cap=2, emit=True,
    )
    assert plan.emitted == 2
    assert len(calls) == 2


def test_a_failing_gate_call_does_not_spend_quota_for_that_candidate(tmp_path: Path) -> None:
    plan = run_curation(
        sources=(_source("a.md"),), existing_digests=frozenset(), state_path=tmp_path / "state.json",
        cli_path=Path("/live/wiki/scripts/wiki_cli.py"), workspace=tmp_path / "work",
        client=_FakeClient("## 요약\n합의."), runner=lambda argv: 1,
        clock=lambda: _CLOCK, cap=5, emit=True,
    )
    assert plan.emitted == 0
    assert plan.failures == ("a.md",)
