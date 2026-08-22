"""The readiness report must not read as "every approval surface is ready".

`REQUIRED_CHANNELS` covers the three *guild* channels the runtime reads, and it
is complete for what it claims. But the owner-DM surface — the one that carries
mail, calendar, coordination, todo and repair approvals — is not in it, and a
reader who sees `--- READY: 9건 중 실패 0` reasonably concludes the approval path
was verified. It was not.

Discovering it automatically is deliberately NOT done: an installer that opens
or looks up a DM channel has stopped being read-only, and the owner id is not
even known at install time. So the gap is *declared* instead — one WARN naming
the surface as unverified, which cannot change the exit code and cannot be
mistaken for a passing check.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from automation.install.checks import Status, exit_code, render
from automation.install.discord_check import (
    OWNER_DM_SURFACE,
    REQUIRED_CHANNELS,
    ApiResponse,
    owner_dm_coverage,
    run_checks,
)

_PERMISSION_BITS: Final[Mapping[str, int]] = {
    "ADD_REACTIONS": 1 << 6,
    "VIEW_CHANNEL": 1 << 10,
    "SEND_MESSAGES": 1 << 11,
    "ATTACH_FILES": 1 << 15,
    "READ_MESSAGE_HISTORY": 1 << 16,
    "CREATE_PUBLIC_THREADS": 1 << 35,
    "SEND_MESSAGES_IN_THREADS": 1 << 38,
}


def _fetch(path: str) -> ApiResponse:
    if path == "/users/@me":
        return ApiResponse(200, {"id": "1", "username": "Example-Agent", "bot": True})
    if path == "/applications/@me":
        return ApiResponse(200, {"flags": 1 << 18})
    if path == "/users/@me/guilds":
        return ApiResponse(200, [{"id": "9", "permissions": str(sum(_PERMISSION_BITS.values()))}])
    if path.endswith("/messages?limit=1"):
        return ApiResponse(200, [])
    return ApiResponse(200, {"id": "5", "type": 0, "name": _NAMES[path]})


_NAMES: Final = {
    f"/channels/{index}": requirement.expected_name
    for index, requirement in enumerate(REQUIRED_CHANNELS)
}
_IDS: Final = {
    requirement.role: str(index) for index, requirement in enumerate(REQUIRED_CHANNELS)
}


def test_owner_dm_is_reported_as_an_unverified_surface() -> None:
    # When
    result = owner_dm_coverage()

    # Then
    assert result.status is Status.WARN
    assert "UNVERIFIED-SURFACE" in result.detail
    assert OWNER_DM_SURFACE in result.name


def test_the_notice_says_what_it_does_not_cover_and_why() -> None:
    detail = owner_dm_coverage().detail

    assert "DM" in detail
    assert "읽기 전용" in detail
    assert "설치 후" in detail


def test_a_fully_passing_run_still_carries_the_unverified_surface() -> None:
    # Given every guild channel is fine
    results = run_checks(_fetch, _IDS)

    # Then the report cannot be read as "all approval surfaces verified"
    rendered = render(results)
    assert any(result.status is Status.WARN for result in results)
    assert "UNVERIFIED-SURFACE" in rendered
    assert exit_code(results) == 0


def test_the_notice_never_turns_a_green_run_red() -> None:
    # Given — a declaration of coverage is not a failing check
    results = run_checks(_fetch, _IDS)

    assert [r for r in results if r.status is Status.FAIL] == []
    assert exit_code(results) == 0


def test_a_dead_token_still_short_circuits_before_the_notice() -> None:
    # Given — the early return exists so one dead token is not buried in noise
    def dead(path: str) -> ApiResponse:
        del path
        return ApiResponse(401)

    results = run_checks(dead, _IDS)

    assert len(results) == 1
    assert results[0].status is Status.FAIL


def test_no_dm_endpoint_is_ever_requested() -> None:
    # Given — opening or looking up a DM would end the read-only contract
    requested: list[str] = []

    def recording(path: str) -> ApiResponse:
        requested.append(path)
        return _fetch(path)

    _ = run_checks(recording, _IDS)

    assert not any("/users/@me/channels" in path for path in requested)
    assert not any(path.startswith("/users/") and path != "/users/@me" for path in requested if
                   path != "/users/@me/guilds")
