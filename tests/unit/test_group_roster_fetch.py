from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from automation.group_roster import (
    RosterFetchConfig,
    RosterFetchError,
    refresh_roster,
)
from automation.managed_sync.fetch import sync_remote, sync_roster_ref
from tests.unit.roster_fetch_fixtures import (
    ROSTER_NAMESPACE,
    ROSTER_PRINCIPAL,
    RosterRepository,
    create_roster_repository,
    publish_signed_roster,
    publish_tampered_roster,
    publish_unsigned_roster,
    roster_bytes,
)

_REPO = Path(__file__).resolve().parents[2]


def test_group_roster_imports_when_runtime_typing_has_no_override() -> None:
    # Given: the no-agent interpreter exposes the Python 3.11 typing surface.
    script = """
import typing

if hasattr(typing, "override"):
    del typing.override

import automation.group_roster
"""

    # When: the deployed group-roster package is imported through its public surface.
    result = subprocess.run(
        (sys.executable, "-c", script),
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: typing-only decorators cannot prevent the runtime from starting.
    assert result.returncode == 0, result.stderr


class RecordingGitRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        argv: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(argv))
        return subprocess.run(
            argv,
            env=env,
            capture_output=capture_output,
            text=text,
            timeout=timeout,
        )


def _refresh_config(repository: RosterRepository) -> RosterFetchConfig:
    return RosterFetchConfig(
        mirror_dir=repository.feed_config.mirror_dir,
        roster_path=repository.destination,
        allowed_signers=repository.allowed_signers,
        expected_principal=ROSTER_PRINCIPAL,
    )


def _seed_last_known_good(repository: RosterRepository) -> bytes:
    old_roster = roster_bytes(repository, ("2002",))
    _ = repository.destination.write_bytes(old_roster)
    return old_roster


def _fetch_tick(repository: RosterRepository) -> None:
    _ = sync_remote(repository.feed_config)
    sync_roster_ref(repository.feed_config)


def test_refresh_roster_when_admin_adds_member_then_next_tick_installs_exact_bytes(
    tmp_path: Path,
) -> None:
    # Given: a subscriber has the signed one-member roster and the admin publishes two members.
    repository = create_roster_repository(tmp_path)
    old_roster = _seed_last_known_good(repository)
    publish_signed_roster(repository, old_roster)
    _fetch_tick(repository)
    assert not refresh_roster(_refresh_config(repository)).updated
    updated_roster = roster_bytes(repository, ("2002", "2003"))
    publish_signed_roster(repository, updated_roster)

    # When: the subscriber runs the next ordinary shared-mirror fetch tick.
    _fetch_tick(repository)
    runner = RecordingGitRunner()
    result = refresh_roster(_refresh_config(repository), git_runner=runner)

    # Then: the local roster advances to the administrator's exact signed bytes.
    assert result.updated
    assert repository.destination.read_bytes() == updated_roster
    archive_call = runner.calls[0]
    assert archive_call[:6] == (
        "git",
        "-C",
        str(repository.feed_config.mirror_dir),
        "archive",
        "--format=tar",
        "--output",
    )
    assert Path(archive_call[6]).name == "roster.tar"
    assert archive_call[7:] == (
        "refs/heads/roster",
        "roster/roster.yaml",
        "roster/roster.yaml.sig",
    )


def test_refresh_roster_when_payload_is_tampered_then_old_bytes_survive(
    tmp_path: Path,
) -> None:
    # Given: a branch tip changes the roster after its valid signature was created.
    repository = create_roster_repository(tmp_path)
    old_roster = _seed_last_known_good(repository)
    publish_signed_roster(repository, old_roster)
    tampered = roster_bytes(repository, ("2002", "2999"))
    publish_tampered_roster(repository, tampered)

    # When / Then: verification rejects it and leaves the last-known-good file byte-identical.
    _fetch_tick(repository)
    with pytest.raises(RosterFetchError, match="signature"):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == old_roster


def test_refresh_roster_when_signature_is_missing_then_old_bytes_survive(
    tmp_path: Path,
) -> None:
    # Given: the roster branch contains YAML but no detached signature artifact.
    repository = create_roster_repository(tmp_path)
    old_roster = _seed_last_known_good(repository)
    publish_signed_roster(repository, old_roster)
    publish_unsigned_roster(repository, roster_bytes(repository, ("2002", "2003")))

    # When / Then: archive verification rejects it without touching the local roster.
    _fetch_tick(repository)
    with pytest.raises(RosterFetchError):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == old_roster


def test_refresh_roster_when_signed_yaml_is_malformed_then_old_bytes_survive(
    tmp_path: Path,
) -> None:
    # Given: malformed YAML is authentically signed by the administrator.
    repository = create_roster_repository(tmp_path)
    old_roster = _seed_last_known_good(repository)
    publish_signed_roster(repository, b"schema: 1\nadmin: [\n")

    # When / Then: parsing fails closed after signature verification and preserves old bytes.
    _fetch_tick(repository)
    with pytest.raises(RosterFetchError, match="parse"):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == old_roster


def test_refresh_roster_when_signed_schema_is_invalid_then_old_bytes_survive(
    tmp_path: Path,
) -> None:
    # Given: syntactically valid YAML omits fields required by the roster validator.
    repository = create_roster_repository(tmp_path)
    old_roster = _seed_last_known_good(repository)
    invalid_schema = b"schema: 1\ngroup_id: testlab\nadmin: {}\nmembers: []\n"
    publish_signed_roster(repository, invalid_schema)

    # When / Then: W-F2-A validation rejects it and preserves the old bytes exactly.
    _fetch_tick(repository)
    with pytest.raises(RosterFetchError, match="parse"):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == old_roster


def test_refresh_roster_when_signed_payload_is_empty_then_old_bytes_survive(
    tmp_path: Path,
) -> None:
    # Given: an empty roster file is committed with a detached signature.
    repository = create_roster_repository(tmp_path)
    old_roster = _seed_last_known_good(repository)
    publish_signed_roster(repository, b"")

    # When / Then: empty input is rejected before installation and old bytes remain exact.
    _fetch_tick(repository)
    with pytest.raises(RosterFetchError, match="empty"):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == old_roster


def test_refresh_roster_when_signature_uses_git_namespace_then_old_bytes_survive(
    tmp_path: Path,
) -> None:
    # Given: valid roster bytes are signed under Git's tag namespace instead of roster scope.
    repository = create_roster_repository(tmp_path)
    old_roster = _seed_last_known_good(repository)
    publish_signed_roster(repository, roster_bytes(repository, ("2002", "2003")), "git")

    # When / Then: only autophagy-roster authorizes replacement of the local roster.
    _fetch_tick(repository)
    with pytest.raises(RosterFetchError, match="signature"):
        _ = refresh_roster(_refresh_config(repository))
    assert repository.destination.read_bytes() == old_roster
    assert ROSTER_NAMESPACE == "autophagy-roster"
