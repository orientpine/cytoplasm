"""Local render exception must not expand the existing network policy."""

from __future__ import annotations

import pytest

from skills.proposal.scripts.proposal_route_guard import (
    Destination,
    RouteRefused,
    assert_route_allowed,
    classify,
)


@pytest.mark.parametrize(
    "route",
    [
        ("render", None, True),
        ("drive", None, True),
        ("refine-host", "codex-oauth", True),
        ("refine-host", "codex", True),
        ("refine-host", "openai-codex", True),
        ("refine-host", "hermes-codex", True),
        ("image-api", None, False),
        ("refine-host", None, False),
        ("refine-host", "public-anthropic-api", False),
        ("refine-host", "attacker.example", False),
    ],
)
def test_technical_transfer_follows_destination_policy(
    monkeypatch: pytest.MonkeyPatch,
    route: tuple[Destination, str | None, bool],
) -> None:
    destination, host, allowed = route
    # Given: the real keyword classifier and default owner-controlled hosts.
    monkeypatch.delenv("PROPOSAL_REFINE_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("PROPOSAL_RULES_PATH", raising=False)
    payload = "기술이전 계획은 검증된 공개 성과의 활용 절차를 설명한다."
    assert classify(payload) == "patent-sensitive"

    # When: the same sensitive body is offered to a destination.
    # Then: only local rendering and the pre-existing explicit exceptions pass.
    if allowed:
        assert assert_route_allowed(payload, destination, host=host).allowed
    else:
        with pytest.raises(RouteRefused):
            _ = assert_route_allowed(payload, destination, host=host)


def test_private_note_render_remains_refused() -> None:
    # Given: private-note provenance without a patent keyword.
    payload = "Local note content"
    # When / Then: allowing patent-sensitive render does not open private notes.
    with pytest.raises(RouteRefused):
        _ = assert_route_allowed(payload, "render", source_keys=("obsidian:private",))
