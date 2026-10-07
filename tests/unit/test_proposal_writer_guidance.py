from __future__ import annotations

import pytest

from skills.proposal.engine.agents import hermes_client, llm
from skills.proposal.engine.agents.writer_guidance import SectionGuidedLLM
from skills.proposal.engine.hwpx.seed_fill import FORM_SUBHEADINGS


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, role: str, prompt: str) -> str:
        self.calls.append((role, prompt))
        return "본문이다."


def test_a_live_writer_is_told_the_form_outline_and_the_banned_markup() -> None:
    inner = _Recorder()

    _ = SectionGuidedLLM(inner).complete("writer_sec3", "Write section 3 for proposal:")

    role, prompt = inner.calls[0]
    assert role == "writer_sec3"
    assert prompt.endswith("Write section 3 for proposal:")
    for _, section, title in FORM_SUBHEADINGS:
        assert (f"### {title}" in prompt) is (section == "3")
    assert "마크다운 표" in prompt


def test_non_writer_roles_reach_the_backend_unchanged() -> None:
    inner = _Recorder()

    _ = SectionGuidedLLM(inner).complete("planner", "plan this")

    assert inner.calls == [("planner", "plan this")]


def test_live_llm_routes_writers_through_the_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    inner = _Recorder()
    monkeypatch.setattr(hermes_client, "hermes_client_from_env", lambda: inner)

    client = llm.get_llm({"mode": "live", "provider": "hermes"})
    _ = client.complete("writer_sec1", "Write section 1 for proposal:")

    assert "### 1-1. 기술적 배경 및 국내외 동향" in inner.calls[0][1]
