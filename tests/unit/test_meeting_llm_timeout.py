"""추출 호출의 시간 예산 계약과 경로 고정.

별도 파일인 이유는 이 계약이 프롬프트·스키마가 아니라 **운영 한도**이기 때문이다.
2026-08-28 실측(노드): 91,894바이트 전사본이 추출 호출의 180초 기본값에 걸려
`TimeoutError` 로 죽었고, 야간 배치는 매일 밤 같은 자리에서 같은 이유로 실패했다 — 재시도가
있어도 한도가 그대로면 영원히 실패한다. 값 자체가 곧 동작이라 값을 고정한다.

같은 자리에서 argv 도 고정한다. `--ignore-user-config` 가 빠지면 Hermes 가 사용자 설정의
fallback provider 로 내려가므로, 예산과 마찬가지로 값 자체가 곧 fail-closed 동작이다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(REPO))

import meeting_llm  # noqa: E402

from automation import codex_llm  # noqa: E402


def _capture(monkeypatch, tmp_path: Path) -> dict:
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(argv, 0, '{"decisions": []}', "")

    monkeypatch.delenv(meeting_llm.TIMEOUT_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(tmp_path / "hermes"))
    monkeypatch.setattr(codex_llm.subprocess, "run", fake_run)
    return seen


def test_a_long_transcript_gets_more_than_three_minutes(monkeypatch, tmp_path) -> None:
    """180초는 91KB 전사본을 넘기기에 부족하다 — 실측으로 죽은 값이다."""
    seen = _capture(monkeypatch, tmp_path)

    meeting_llm.call_codex("x", sensitive=False)

    assert seen["timeout"] == meeting_llm.LLM_TIMEOUT
    assert meeting_llm.LLM_TIMEOUT >= 600, "공용 클라이언트 기본값(180초) 아래로 내려가지 않는다"


def test_the_budget_can_be_raised_without_a_release(monkeypatch, tmp_path) -> None:
    """더 긴 회의가 오면 릴리스를 기다리지 않고 노드에서 늘릴 수 있어야 한다."""
    seen = _capture(monkeypatch, tmp_path)
    monkeypatch.setenv(meeting_llm.TIMEOUT_ENV, "1234")

    meeting_llm.call_codex("x", sensitive=False)

    assert seen["timeout"] == 1234.0


def test_a_malformed_override_falls_back_instead_of_crashing(monkeypatch, tmp_path) -> None:
    seen = _capture(monkeypatch, tmp_path)
    monkeypatch.setenv(meeting_llm.TIMEOUT_ENV, "곧")

    meeting_llm.call_codex("x", sensitive=False)

    assert seen["timeout"] == meeting_llm.LLM_TIMEOUT


def test_every_extraction_call_pins_the_codex_oauth_route(monkeypatch, tmp_path) -> None:
    """사용자 설정의 fallback provider 로 내려갈 수 있는 argv 는 회귀다."""
    seen = _capture(monkeypatch, tmp_path)

    meeting_llm.call_codex("x", sensitive=True)

    assert "--ignore-user-config" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--provider") + 1] == meeting_llm.CODEX_PROVIDER
