"""Oversized submission cards must refuse through the real lease, not as TypeError."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from automation.managed_skills.submission_approval import request_submission_approval
from automation.managed_skills.submission_errors import SubmissionArtifactError
from tests.unit.test_approval_lease import _peer_can_lock
from tests.unit.test_personal_submission_approval import _Transport, _config


def test_submit_when_card_is_oversized_then_original_exception_propagates_and_releases(
    tmp_path: Path,
) -> None:
    # Given: a validated artifact whose submission identity cannot fit on a card.
    transport = _Transport()
    config = replace(_config(tmp_path, transport), group_id="x" * 1900)

    # When / Then: the lifecycle posting path raises the original refusal.
    with pytest.raises(SubmissionArtifactError, match="1900"):
        _ = request_submission_approval(config)

    # And: Discord is untouched and the key flock is free for the next producer.
    assert transport.posts == []
    assert transport.messages == {}
    leases = tuple((config.state_root / "approval-leases").glob("*.lease"))
    assert len(leases) == 1
    assert _peer_can_lock(leases[0]) is True
