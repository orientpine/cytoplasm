r"""봇끼리는 프로토콜로만 말한다 — 자유 산문은 에이전트 턴을 열지 않는다.

2026-08-20 실측: `#approvals` 에서 agent 봇과 peer 봇이 서로의 인사말("검증 완료 상태입니다",
"네, 양측 검증 완료 상태로 일치합니다", "대기 모드 유지 중입니다" …)에 번갈아 답하며 **12번**을
오갔고, 그 사이 정작 소유자가 ✅ 를 눌러야 할 승인 요청 메시지가 화면 밖으로 밀려났다.

`LoopGuard` 는 이 패턴을 막지 못했다 — 분당 5회를 허용하는데 2분에 걸쳐 각 6회였고,
저정보 판정도 한국어 인사말을 저정보로 보지 않았다. 임계값을 느슨하게 조이는 대신
**첫 홉에서** 끊는다: 봉투도 보고도 아닌 봇 발화는 어떤 흐름도 요구하지 않는다
(peer 증명은 `peer_attest.py` 가 직접 게시하고, 보고는 report_hub 가 수집하며,
승인은 소유자 리액션을 cron 워처가 폴링한다).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from automation.interop import hermes_plugin


@dataclass(frozen=True, slots=True)
class _Source:
    is_bot: bool
    user_id: str = "999"
    chat_id: str = "1528936606856122421"
    thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class _Event:
    text: str
    source: _Source = field(default_factory=lambda: _Source(is_bot=True))


class _NeverPaused:
    def is_paused(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def _isolated_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """디스크의 interop config·킬스위치에 의존하지 않게 경계만 고정한다."""
    monkeypatch.setattr(hermes_plugin, "_pause_store", _NeverPaused)
    monkeypatch.setattr(
        hermes_plugin,
        "_config",
        lambda: {
            "agent_id": "agent-under-test",
            "agents_log_channel_id": "1",
            "owner_id": "2",
            "interop_channel_id": "3",
        },
    )


def _dispatch(text: str, *, is_bot: bool) -> dict[str, str] | None:
    event = _Event(text=text, source=_Source(is_bot=is_bot))
    return hermes_plugin.pre_gateway_dispatch(event, None, None)


def test_bot_prose_never_opens_an_agent_turn() -> None:
    result = _dispatch(
        "네, 양측 검증 완료 상태로 일치합니다. 차 소유자의 ✅ 리액션만 남았습니다.", is_bot=True
    )

    assert result is not None
    assert result["action"] == "skip"
    assert result["reason"] == "interop_bot_prose"


def test_the_first_reply_is_suppressed_not_the_sixth() -> None:
    """루프는 임계값에 닿기 전에 이미 채널을 덮는다 — 첫 홉에서 끊어야 한다."""
    for _ in range(3):
        result = _dispatch("대기 모드 유지 중입니다. 상태 변경이 감지되면 응답하겠습니다.", is_bot=True)

        assert result is not None
        assert result["reason"] == "interop_bot_prose", "rate_limit 까지 가면 이미 늦다"


def test_a_human_message_is_untouched() -> None:
    """사람의 발화는 이 규칙의 대상이 아니다."""
    result = _dispatch("wiki 배포 상태 알려줘", is_bot=False)

    assert result is None or result.get("action") in {"allow", "rewrite"}
