"""Delegation receipt through the real gateway hook and notice facade."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

import pytest

from automation import owner_notice
from automation.interop import hermes_plugin as plugin
from automation.interop.delegation import InteropEnvelope, format_envelope, result_message
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
from tests.unit.test_interop_channel_routing import _ROSTER, _PEER_BOT_ID

LEGACY: Final = "Interop delegation result: corr-1"
RENDERED: Final = (
    "대상: 에이전트 가용 시간 조회 (corr-1)\n"
    "사실: peer-test 응답 수신 · response_availability (실행 완료)\n"
    "위치: 링크 없음 (공간 미상); 검색: Discord 검색 / corr-1\n"
    "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n"
    "되돌리기: 해당 없음"
)
EXPECTED: Final = OwnerMessage(
    subject_key="corr-1", subject="에이전트 가용 시간 조회",
    fact="peer-test 응답 수신 · response_availability",
    location=Ref(scope="channel", space="unknown", channel_id="222", search=("Discord 검색", "corr-1")),
    owner=Action(verb="none"), agent_next="추가 실행 없음",
    recovery="not_applicable", detail=Result(outcome="executed"),
)


@dataclass(frozen=True, slots=True)
class Source:
    chat_id: str = "111"
    thread_id: str | None = "222"
    is_bot: bool = True
    user_id: str = _PEER_BOT_ID


@dataclass(frozen=True, slots=True)
class Event:
    text: str
    source: Source = Source()


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Event:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(plugin, "_config", lambda: {
        "agent_id": "agent-test", "owner_id": "333", "agents_log_channel_id": "444",
    })
    monkeypatch.setattr(plugin.group_roster, "load_roster", lambda path: _ROSTER)
    return Event(format_envelope(InteropEnvelope(
        "corr-1", "peer-test", "agent-test", "response_availability", {"slots": []},
    )))


@pytest.fixture
def deliveries(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "444")
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: sent.append(body))
    return sent


def test_legacy_bytes_when_facade_has_no_capability(
    gateway: Event, deliveries: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an older facade.
    monkeypatch.delattr(owner_notice, "ACCEPTS_OWNER_MESSAGE")
    # When: the gateway receives a delegation result.
    result = plugin.pre_gateway_dispatch(gateway, None, None)
    # Then: the gateway consumes it and sends exactly the legacy bytes.
    assert result == {"action": "skip", "reason": "interop_delegation_delivered"}
    assert deliveries == [LEGACY]


def test_legacy_bytes_when_contract_import_is_unavailable(
    gateway: Event, deliveries: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the optional contract cannot be imported.
    import sys
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    # When: the gateway receives a delegation result.
    result = plugin.pre_gateway_dispatch(gateway, None, None)
    # Then: the legacy bytes and hook return survive.
    assert result == {"action": "skip", "reason": "interop_delegation_delivered"}
    assert deliveries == [LEGACY]


def test_delivery_error_propagates_when_transport_fails(
    gateway: Event, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the facade reports delivery failure.
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content, **kwargs: False)
    # When / Then: the hook must not silently consume the failed result.
    with pytest.raises(RuntimeError, match="^delegation result notice delivery failed$"):
        plugin.pre_gateway_dispatch(gateway, None, None)


def test_envelope_fields_when_gateway_receives_result(
    gateway: Event, deliveries: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real facade with only its wire transport replaced.
    messages: list[OwnerMessage | None] = []
    original = owner_notice.notify_owner

    def capture(content: str, *, message: OwnerMessage | None = None) -> bool:
        messages.append(message)
        return original(content, message=message)

    monkeypatch.setattr(owner_notice, "notify_owner", capture)
    # When: a response arrives in a known thread but unknown Discord space.
    result = plugin.pre_gateway_dispatch(gateway, None, None)
    # Then: complete independently specified fields, not an id-only success claim.
    assert result == {"action": "skip", "reason": "interop_delegation_delivered"}
    assert messages == [EXPECTED]
    assert deliveries == [RENDERED]


@pytest.mark.parametrize(("intent", "subject", "fact"), [
    ("response_confirm_slot", "에이전트 일정 확인", "peer-test 응답 수신 · response_confirm_slot"),
    ("response_custom", "에이전트 위임 응답", "peer-test 응답 수신 · response_custom"),
])
def test_fields_when_peer_declines(intent: str, subject: str, fact: str) -> None:
    # Given: declined responses are receipts, not evidence of calendar execution.
    envelope = InteropEnvelope("corr-1", "peer-test", "agent-test", intent, {"result": "declined"})
    # When: a receipt is built without Discord coordinates.
    message = result_message("corr-1", envelope, None)
    # Then: all fields retain the receipt semantics and the known search key.
    assert message == OwnerMessage(
        subject_key="corr-1", subject=subject, fact=fact,
        location=Ref(scope="channel", search=("Discord 검색", "corr-1")),
        owner=Action(verb="none"), agent_next="추가 실행 없음",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )


def test_location_when_gateway_has_no_thread(
    gateway: Event, deliveries: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a channel response rather than a thread response.
    messages: list[OwnerMessage | None] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content, *, message=None: messages.append(message) or True)
    event = replace(gateway, source=Source(thread_id=None))
    # When: the hook dispatches the result.
    plugin.pre_gateway_dispatch(event, None, None)
    # Then: the actual source channel is retained; no link is guessed.
    assert messages == [replace(EXPECTED, location=Ref(
        scope="channel", channel_id="111", search=("Discord 검색", "corr-1"),
    ))]


def test_fields_when_only_correlation_is_available() -> None:
    # Given / When: a legacy internal caller has only the protocol correlation.
    message = result_message("corr-1", None, None)
    # Then: no coordinates or completion details are invented.
    assert message == OwnerMessage(
        subject_key="corr-1", subject="에이전트 위임 응답", fact="위임 응답 수신",
        location=Ref(scope="channel", search=("Discord 검색", "corr-1")),
        owner=Action(verb="none"), agent_next="추가 실행 없음",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )


def test_coordination_hook_when_response_is_not_direct(
    gateway: Event, deliveries: list[str],
) -> None:
    # Given: a coordination response owned by the coordination consumer.
    event = Event(gateway.text.replace("corr-1", "coord-1"))
    # When: the same gateway hook dispatches it.
    result = plugin.pre_gateway_dispatch(event, None, None)
    # Then: no owner delivery and the existing skip contract.
    assert result == {"action": "skip", "reason": "interop_coordination_response"}
    assert deliveries == []
