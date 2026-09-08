"""화자 수 질의의 실행 경계 — 옵트인·민감도 게이트·자르기."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_speaker_ask  # noqa: E402

_ON = {"SPEECHTOTEXT_SPEAKER_COUNT_LLM": "1"}


def test_the_question_is_opt_in() -> None:
    """설정하지 않은 노드에서는 묻지 않는다 — 그때 전사는 예전과 바이트 그대로다."""
    assert stt_speaker_ask.resolve({}, repo_root=REPO, complete=lambda _p: "3") is None


def test_an_unreadable_prompt_or_gate_asks_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """준비물이 없으면 묻지 않는다(fail-closed) — 게이트 없이 모델을 부르지 않는다."""
    assert stt_speaker_ask.resolve(_ON, repo_root=tmp_path, complete=lambda _p: "3") is None
    assert "RECOUNT-FAIL" in capsys.readouterr().err


def test_a_clean_draft_is_asked_through_the_shipped_prompt() -> None:
    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return '{"speaker_count": 3}'

    ask = stt_speaker_ask.resolve(_ON, repo_root=REPO, complete=complete)
    assert ask is not None

    assert ask("[00:00:00] 화자1\n안녕하세요.") == '{"speaker_count": 3}'
    assert "안녕하세요." in seen[0]
    assert "{{TRANSCRIPT}}" not in seen[0]


def test_a_patent_sensitive_draft_never_reaches_the_model(
    capsys: pytest.CaptureFixture[str]
) -> None:
    """화자 수를 세자고 특허 내용을 내보낼 수는 없다 — 초안 단계에서 걸러 낸다."""

    def complete(_prompt: str) -> str:
        raise AssertionError("특허 민감 전사본이 모델로 갔다")

    ask = stt_speaker_ask.resolve(_ON, repo_root=REPO, complete=complete)
    assert ask is not None

    assert ask("[00:00:00] 화자1\n이 특허 출원 건은 다음 주에 냅니다.") == ""
    assert "RECOUNT-SKIP patent-sensitive" in capsys.readouterr().err


def test_a_long_draft_is_clipped_before_it_is_sent() -> None:
    """상한을 넘긴 초안은 잘라 보낸다 — 두 초안의 프롬프트 길이가 같아야 한다."""
    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return "2"

    ask = stt_speaker_ask.resolve(_ON, repo_root=REPO, complete=complete)
    assert ask is not None

    _ = ask("x" * stt_speaker_ask.MAX_DRAFT_CHARS)
    _ = ask("x" * (stt_speaker_ask.MAX_DRAFT_CHARS + 500))

    assert len(seen[0]) == len(seen[1])
