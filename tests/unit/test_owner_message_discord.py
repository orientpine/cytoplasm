"""Discord presentation contracts; separate from frozen FS3 and v1 captures."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Literal

import pytest

from automation.interop.approval_card import CardRenderError, prepare
from automation.interop.owner_message import (
    Action, Approval, OwnerMessage, Periodic, Ref, Result, render,
)

HERE = Ref("self")
WHEN = datetime(2030, 1, 2, 9, 0, tzinfo=timezone(timedelta(hours=9)))
MESSAGE = OwnerMessage(
    "0123456789abcdef", "S42", "F42  \n  F43\t\n\nF44", HERE,
    Action("react", HERE, "✅ 실행 / ⛔ 취소"), "N42", "not_applicable",
    Approval(WHEN, "C42"), render_version="owner-ko-v2",
)


def test_fact_line_boundaries_when_rendering_discord() -> None:
    # Given a fact with intentional indentation and blank lines.
    message = MESSAGE
    # When
    body = render(message, destination=HERE)
    # Then quote markdown preserves all content except trailing whitespace.
    assert [line.removeprefix("> ") for line in body.splitlines() if line.startswith("> ")] == [
        "F42", "  F43", "", "F44",
    ]
    assert body.splitlines()[0] == "**🔔 S42**"
    assert message.subject_key not in body
    assert body.splitlines()[-1].endswith("`01234567`")


@pytest.mark.parametrize("scope", ["self", "none", "channel", "message"])
def test_location_omitted_when_reference_is_local(
    scope: Literal["self", "none", "channel", "message"],
) -> None:
    # Given a local reference with a search fallback that must not leak.
    reference = Ref(scope, channel_id="222", search=("L42", "K42"))
    message = replace(MESSAGE, location=reference)
    # When
    body = render(message, destination=Ref("channel", channel_id="222"))
    # Then
    assert "L42" not in body and "K42" not in body
    assert not any(line.startswith("위치:") for line in body.splitlines())


@pytest.mark.parametrize("reference", [
    Ref("resource", url="https://example.test/document"),
    Ref("message", "guild", "111", "222", "333"),
    Ref("message", search=("L42", "K42")),
])
def test_remote_location_kept_when_destination_differs(reference: Ref) -> None:
    # Given
    message = replace(MESSAGE, location=reference)
    # When
    body = render(message, destination=Ref("channel", channel_id="999"))
    # Then
    location = next(line for line in body.splitlines() if line.startswith("위치:"))
    if reference.url:
        assert reference.url in location
    elif reference.channel_id:
        assert "/111/222/333" in location
    else:
        assert "L42" in location and "K42" in location


@pytest.mark.parametrize("offset", [9, -4])
def test_expiry_keeps_own_timezone_when_rendering(offset: int) -> None:
    # Given
    expiry = WHEN.replace(tzinfo=timezone(timedelta(hours=offset)))
    message = replace(MESSAGE, detail=Approval(expiry, "C42"))
    # When
    body = render(message, destination=HERE)
    # Then presentation changes separators, never the instant's wall-clock zone.
    decision = next(line for line in body.splitlines() if line.startswith("**결정:**"))
    assert f"2030-01-02 09:00 ({offset:+03}:00)" in decision
    assert decision.count("✅") == decision.count("⛔") == 1


@pytest.mark.parametrize(("detail", "icon"), [
    (Approval(None, "C42"), "🔔"), (Result("executed"), "✅"),
    (Result("cancelled"), "⛔"), (Result("expired"), "⌛"),
    (Periodic(WHEN, WHEN + timedelta(hours=1)), "📊"),
])
def test_header_kind_when_detail_changes(detail: Approval | Result | Periodic, icon: str) -> None:
    # Given
    message = replace(MESSAGE, detail=detail)
    # When
    body = render(message, destination=HERE)
    # Then
    assert body.splitlines()[0] == f"**{icon} S42**"


@pytest.mark.parametrize("recovery", ["not_applicable", "irreversible", Action("reply", HERE, "R42")])
def test_recovery_presentation_when_action_is_available(
    recovery: Literal["not_applicable", "irreversible"] | Action,
) -> None:
    # Given
    message = replace(MESSAGE, recovery=recovery)
    # When
    body = render(message, destination=HERE)
    # Then
    assert any(line.startswith("되돌리기:") for line in body.splitlines()) is (recovery != "not_applicable")
    assert "C42" in body and "N42" in body


def test_postable_limit_when_v2_card_is_too_long() -> None:
    # Given the existing final-card boundary (envelope rendering does not truncate).
    message = replace(MESSAGE, fact="F" * 2000)
    # When / Then
    with pytest.raises(CardRenderError):
        _ = prepare(lambda version: render(message, destination=HERE), "3")


def test_new_card_version_when_no_stored_version_exists() -> None:
    # Given
    versions: list[str] = []

    def renderer(version: str) -> str:
        versions.append(version)
        return "wire"

    # When
    card = prepare(renderer)
    # Then
    assert card.render_version == "3" and versions == ["3"]


@pytest.mark.parametrize("version", ["1", "2", "3"])
def test_stored_card_when_new_default_changes(version: str) -> None:
    # Given
    versions: list[str] = []

    def renderer(selected: str) -> str:
        versions.append(selected)
        return selected

    # When
    card = prepare(renderer, version)
    # Then
    assert card.content == version and versions == [version]


def test_local_action_when_location_line_is_hidden_retains_its_target() -> None:
    # Given a same-channel reaction and recovery, not a self-scoped card.
    from tests.unit.mail_approval_card_golden import OWNER_LOCAL_V2

    target = Ref("message", channel_id="222", message_id="333")
    message = replace(
        MESSAGE, location=target, fact="F42", owner=Action("react", target, "A42"),
        recovery=Action("reply", target, "R42"), detail=Approval(None, "C42"),
    )
    # When
    content = render(message, destination=Ref("channel", channel_id="222"))
    # Then exact display bytes refer to the local target, not an omitted location row.
    assert content == OWNER_LOCAL_V2
