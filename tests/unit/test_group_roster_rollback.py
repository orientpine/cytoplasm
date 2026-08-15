"""Rollback/replay protection for the signed group roster (security audit 2026-08-15).

The roster is the group's ONLY revocation mechanism: removing a member is
expressed as roster state, not as a key or a server-side ACL. W-F2-C signs the
roster so a feed host that cannot sign cannot forge one — but a signature proves
authorship, never freshness. Replaying a genuinely-signed EARLIER roster
therefore undoes a revocation while every existing check still passes.

The transport makes the replay cheap rather than exotic: the shared mirror
fetches the roster branch with the force refspec
``+refs/heads/roster:refs/heads/roster`` (automation/managed_sync/fetch.py), so a
rewound remote branch is adopted with no fast-forward objection at all.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation.group_roster import (
    MemberStatus,
    RosterError,
    RosterFetchConfig,
    RosterFetchError,
    parse_roster,
    refresh_roster,
)
from automation.managed_sync.fetch import sync_remote, sync_roster_ref
from tests.unit.roster_fetch_fixtures import (
    ROSTER_PRINCIPAL,
    RosterRepository,
    create_roster_repository,
    publish_signed_roster,
)


def _refresh_config(repository: RosterRepository) -> RosterFetchConfig:
    return RosterFetchConfig(
        mirror_dir=repository.feed_config.mirror_dir,
        roster_path=repository.destination,
        allowed_signers=repository.allowed_signers,
        expected_principal=ROSTER_PRINCIPAL,
    )


def _fetch_tick(repository: RosterRepository) -> None:
    _ = sync_remote(repository.feed_config)
    sync_roster_ref(repository.feed_config)


def _roster(
    repository: RosterRepository,
    *,
    revision: int | None,
    members: tuple[tuple[str, str], ...],
) -> bytes:
    """Render one roster, optionally carrying the monotonic ``revision`` field."""
    rendered = "".join(
        (
            "  - name: Test Member\n"
            f'    discord_user_id: "{member_id}"\n'
            f"    node_label: node-{member_id}\n"
            f"    status: {status}\n"
        )
        for member_id, status in members
    )
    revision_line = "" if revision is None else f"revision: {revision}\n"
    return (
        "schema: 1\n"
        f"{revision_line}"
        "group_id: testlab\n"
        "admin:\n"
        "  name: Test Admin\n"
        '  discord_user_id: "2001"\n'
        f"  publisher_principal: {ROSTER_PRINCIPAL}\n"
        f"  signing_public_key: {repository.public_key}\n"
        f"members:\n{rendered}"
    ).encode()


def _branch_tip(repository: RosterRepository) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository.publisher), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _rewind_feed_to(repository: RosterRepository, commit: str) -> None:
    """A feed host with write access rewinds the branch; it forges nothing."""
    _ = subprocess.run(
        (
            "git",
            "-C",
            str(repository.publisher),
            "push",
            "--force",
            "origin",
            f"{commit}:refs/heads/roster",
        ),
        check=True,
        capture_output=True,
    )


def _member_status(payload: bytes, discord_user_id: str) -> MemberStatus:
    roster = parse_roster(payload.decode("utf-8"), source="test")
    member = next(
        candidate
        for candidate in roster.members
        if candidate.discord_user_id == discord_user_id
    )
    return member.status


def _publish_revocation_history(
    repository: RosterRepository,
) -> tuple[bytes, bytes, str]:
    """Publish revision 1 (member active) then revision 2 (member revoked)."""
    revision_one = _roster(
        repository, revision=1, members=(("2002", "active"), ("2003", "active"))
    )
    publish_signed_roster(repository, revision_one)
    replayable_tip = _branch_tip(repository)
    revision_two = _roster(
        repository, revision=2, members=(("2002", "active"), ("2003", "removed"))
    )
    publish_signed_roster(repository, revision_two)
    return revision_one, revision_two, replayable_tip


def test_refresh_roster_when_feed_replays_an_older_signed_roster_then_revocation_stands(
    tmp_path: Path,
) -> None:
    # Given: the subscriber has converged on revision 2, which revoked member 2003.
    repository = create_roster_repository(tmp_path)
    revision_one, revision_two, replayable_tip = _publish_revocation_history(repository)
    _fetch_tick(repository)
    assert refresh_roster(_refresh_config(repository)).updated
    assert repository.destination.read_bytes() == revision_two
    assert _member_status(revision_one, "2003") is MemberStatus.ACTIVE
    assert _member_status(revision_two, "2003") is MemberStatus.REMOVED

    # When: a feed host that cannot sign rewinds the branch to the older SIGNED roster.
    _rewind_feed_to(repository, replayable_tip)
    _fetch_tick(repository)

    # Then: freshness is checked, not just authorship — the revocation survives.
    with pytest.raises(RosterFetchError, match="ROSTER-ROLLBACK"):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == revision_two
    assert _member_status(repository.destination.read_bytes(), "2003") is MemberStatus.REMOVED


def test_refresh_roster_when_replay_strips_the_revision_field_then_it_is_still_refused(
    tmp_path: Path,
) -> None:
    # Given: the subscriber holds a revisioned roster (revision 2).
    repository = create_roster_repository(tmp_path)
    _, revision_two, _ = _publish_revocation_history(repository)
    _fetch_tick(repository)
    assert refresh_roster(_refresh_config(repository)).updated

    # When: an older, validly signed PRE-revision roster is served instead.
    unversioned = _roster(repository, revision=None, members=(("2002", "active"),))
    publish_signed_roster(repository, unversioned)
    _fetch_tick(repository)

    # Then: dropping the ordering field is a downgrade, not an exemption from it.
    with pytest.raises(RosterFetchError, match="ROSTER-ROLLBACK"):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == revision_two


def test_refresh_roster_when_revision_advances_then_the_update_installs(
    tmp_path: Path,
) -> None:
    # Given: the subscriber has converged on revision 2.
    repository = create_roster_repository(tmp_path)
    _, _, _ = _publish_revocation_history(repository)
    _fetch_tick(repository)
    assert refresh_roster(_refresh_config(repository)).updated

    # When: the administrator publishes revision 3 in the ordinary way.
    revision_three = _roster(
        repository, revision=3, members=(("2002", "active"), ("2004", "active"))
    )
    publish_signed_roster(repository, revision_three)
    _fetch_tick(repository)

    # Then: forward movement is unaffected by the rollback guard.
    assert refresh_roster(_refresh_config(repository)).updated
    assert repository.destination.read_bytes() == revision_three


def test_refresh_roster_when_neither_side_is_revisioned_then_behaviour_is_unchanged(
    tmp_path: Path,
) -> None:
    # Given: a pre-revision installation, exactly as shipped before this guard.
    repository = create_roster_repository(tmp_path)
    installed = _roster(repository, revision=None, members=(("2002", "active"),))
    _ = repository.destination.write_bytes(installed)
    replacement = _roster(
        repository, revision=None, members=(("2002", "active"), ("2003", "active"))
    )
    publish_signed_roster(repository, replacement)

    # When: the ordinary tick runs against an equally unversioned roster.
    _fetch_tick(repository)

    # Then: rosters that declare no order are ordered by nothing — legacy behaviour stands.
    assert refresh_roster(_refresh_config(repository)).updated
    assert repository.destination.read_bytes() == replacement


def test_refresh_roster_when_replay_repeats_the_installed_revision_then_it_is_refused(
    tmp_path: Path,
) -> None:
    # Given: a converged subscriber at revision 2.
    repository = create_roster_repository(tmp_path)
    _, revision_two, _ = _publish_revocation_history(repository)
    _fetch_tick(repository)
    assert refresh_roster(_refresh_config(repository)).updated

    # When: a DIFFERENT roster is signed under the SAME revision number.
    collision = _roster(
        repository, revision=2, members=(("2002", "active"), ("2003", "active"))
    )
    publish_signed_roster(repository, collision)
    _fetch_tick(repository)

    # Then: equal is not newer — matching managed_sync.state.record_verified.
    with pytest.raises(RosterFetchError, match="ROSTER-ROLLBACK"):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == revision_two


# --- ordering-field validation (fail-closed parsing of the new field) --------------


@pytest.mark.parametrize(
    "rendered",
    [
        "revision: true\n",  # bool is an int subclass; `true` must not read as 1
        "revision: 0\n",
        "revision: -1\n",
        'revision: "2"\n',
        "revision: 1.5\n",
        "revision: null\n",
    ],
)
def test_validate_roster_when_revision_is_not_a_positive_integer_then_it_is_refused(
    tmp_path: Path, rendered: str
) -> None:
    # Given: a roster whose ordering field cannot be compared as a counter.
    repository = create_roster_repository(tmp_path)
    payload = _roster(repository, revision=1, members=(("2002", "active"),))
    malformed = payload.replace(b"revision: 1\n", rendered.encode())

    # When / Then: an uncomparable order is refused rather than coerced.
    with pytest.raises(RosterError, match="revision must be a positive integer"):
        _ = parse_roster(malformed.decode("utf-8"), source="test")


def test_validate_roster_when_revision_is_present_then_it_is_exposed_for_comparison(
    tmp_path: Path,
) -> None:
    # Given: a well-formed revisioned roster.
    repository = create_roster_repository(tmp_path)
    payload = _roster(repository, revision=7, members=(("2002", "active"),))

    # When / Then: the counter reaches the rollback guard, and absence stays None.
    assert parse_roster(payload.decode("utf-8"), source="test").revision == 7
    unversioned = _roster(repository, revision=None, members=(("2002", "active"),))
    assert parse_roster(unversioned.decode("utf-8"), source="test").revision is None
