"""Content words do not change destination access; source boundaries still apply."""

from __future__ import annotations

import pytest

from skills.proposal.scripts.proposal_route_guard import (
    Destination,
    RouteRefused,
    assert_route_allowed,
)


@pytest.mark.parametrize("destination", ["render", "drive", "refine-host", "image-api"])
@pytest.mark.parametrize("words", ["clean", "특허 patent 기밀", "NDA 기술이전 비공개"])
def test_keywords_do_not_change_destination_access(words: str, destination: Destination) -> None:
    # Given / When: the same source is offered with ordinary or technical words.
    decision = assert_route_allowed(words, destination)
    # Then: content never changes the destination decision.
    assert decision == assert_route_allowed("clean", destination)


def test_private_note_render_remains_refused() -> None:
    # Given: private-note provenance.
    payload = "Local note content"
    # When / Then: content-independent source access stays closed.
    with pytest.raises(RouteRefused):
        _ = assert_route_allowed(payload, "render", source_keys=("obsidian:private",))
