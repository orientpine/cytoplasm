"""The owner's decision is re-read immediately before MOUNT, and that must stay true.

FA1-S5. The plan assumed this needed building. It did not: `deploy-skill.sh` already
re-runs the approval check at stage 4, after the review verdict is re-confirmed and
before the privileged install. What was missing was that the check itself ignored ⛔ —
fixed in FA-1 — so the re-check was faithfully re-asking a question that could only be
answered "approved" or "not yet".

With both halves in place the TOCTOU is closed: a ⛔ that lands between the first check
and the install is seen, because `gate check` is a fresh subprocess every time and
cannot cache the earlier answer.

So this file pins the STRUCTURE rather than adding behaviour. The re-check is one
`if` away from being "simplified" out by someone who notices the check already ran a
few lines earlier, and its absence would be invisible: every happy-path deploy would
still work, and only a cancelled one would mount anyway.
"""
from __future__ import annotations

from pathlib import Path

_DEPLOY = Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh"
_SOURCE = _DEPLOY.read_text(encoding="utf-8")


def _index(needle: str) -> int:
    position = _SOURCE.find(needle)
    assert position != -1, f"missing from deploy-skill.sh: {needle!r}"
    return position


def test_the_approval_is_rechecked_a_second_time_before_mounting() -> None:
    """Two distinct checks: one to decide, one immediately before acting."""
    assert _SOURCE.count("check_with_attestation_refresh ") >= 2


def test_the_recheck_sits_between_the_review_verdict_and_the_install() -> None:
    review_recheck = _index('review check --skill "$SKILL" --hash "$CURRENT_DIGEST"')
    mount_recheck = _index('check_with_attestation_refresh "$CURRENT_DIGEST"')
    install = min(_index("install_managed_skill "), _index("install_reviewed_skill "))
    assert review_recheck < mount_recheck < install


def test_a_failed_recheck_blocks_the_mount_rather_than_warning() -> None:
    """The failure path must be fatal — a warning here would mount a cancelled skill."""
    mount_recheck = _index('check_with_attestation_refresh "$CURRENT_DIGEST"')
    install = min(_index("install_managed_skill "), _index("install_reviewed_skill "))
    between = _SOURCE[mount_recheck:install]
    assert "MOUNT-BLOCK" in between
    assert "die " in between


def test_the_recheck_uses_the_current_digest_not_the_reviewed_one() -> None:
    """Re-asking about a stale digest would approve an artifact nobody looked at."""
    assert 'check_with_attestation_refresh "$CURRENT_DIGEST"' in _SOURCE
