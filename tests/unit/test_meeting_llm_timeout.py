"""추출 호출의 시간 예산 계약.

별도 파일인 이유는 이 계약이 프롬프트·스키마가 아니라 **운영 한도**이기 때문이다.
2026-08-28 실측(노드): 91,894바이트 전사본이 `call_litellm` 의 180초 기본값에 걸려
`TimeoutError` 로 죽었고, 야간 배치는 매일 밤 같은 자리에서 같은 이유로 실패했다 — 재시도가
있어도 한도가 그대로면 영원히 실패한다. 값 자체가 곧 동작이라 값을 고정한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_llm  # noqa: E402


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"choices":[{"message":{"content":"{}"}}]}'


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    def fake_urlopen(request, timeout=None):
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(meeting_llm.urllib.request, "urlopen", fake_urlopen)
    return seen


def test_a_long_transcript_gets_more_than_three_minutes(monkeypatch) -> None:
    """180초는 91KB 전사본을 넘기기에 부족하다 — 실측으로 죽은 값이다."""
    seen = _capture(monkeypatch)

    meeting_llm.call_litellm("x", sensitive=False, base_url="http://x/v1", api_key="k")

    assert seen["timeout"] == meeting_llm.LLM_TIMEOUT
    assert meeting_llm.LLM_TIMEOUT >= 600, "codex 경로와 같은 예산 아래로 내려가지 않는다"


def test_the_budget_can_be_raised_without_a_release(monkeypatch) -> None:
    """더 긴 회의가 오면 릴리스를 기다리지 않고 노드에서 늘릴 수 있어야 한다."""
    monkeypatch.setenv("MEETING_LLM_TIMEOUT", "1234")
    seen = _capture(monkeypatch)

    meeting_llm.call_litellm("x", sensitive=False, base_url="http://x/v1", api_key="k")

    assert seen["timeout"] == 1234.0


def test_a_malformed_override_falls_back_instead_of_crashing(monkeypatch) -> None:
    monkeypatch.setenv("MEETING_LLM_TIMEOUT", "곧")
    seen = _capture(monkeypatch)

    meeting_llm.call_litellm("x", sensitive=False, base_url="http://x/v1", api_key="k")

    assert seen["timeout"] == meeting_llm.LLM_TIMEOUT
