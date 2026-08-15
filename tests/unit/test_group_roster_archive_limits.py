"""Regression: the roster archive read is bounded BEFORE any authentication.

F4 (security audit, 2026-08-15). ``refresh_roster`` called ``_extract_roster``
— which read a ``git archive`` stream with ``stream.read()`` and no cap — and
only *afterwards* verified the detached signature. Anyone who controls
``refs/heads/roster`` on the managed feed therefore had a pre-authentication
memory-exhaustion primitive that fired on every subscriber's tick.

``automation/managed_skills/submission_archive.py`` already capped archive size
and member count for the sibling artifact; this pins the same discipline here.

Every assertion below also checks that the signature runner was never invoked:
the point is not merely that an oversized archive is rejected, but that it is
rejected before the expensive, trust-establishing step runs.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from automation.group_roster.fetch import (
    RosterFetchConfig,
    RosterFetchError,
    refresh_roster,
)

ROSTER_PATH = "roster/roster.yaml"
SIGNATURE_PATH = "roster/roster.yaml.sig"


class _StubArchiveRunner:
    """Stands in for git, delivering a prepared tar to the requested --output."""

    def __init__(self, archive: Path) -> None:
        self._archive: Path = archive
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        argv: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool = False,
        text: bool = False,
        timeout: float = 0.0,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(argv))
        output = Path(argv[argv.index("--output") + 1])
        _ = shutil.copyfile(self._archive, output)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


class _RecordingSignatureRunner:
    """Records whether authentication was reached; always returns a failed verdict."""

    def __init__(self) -> None:
        self.calls: int = 0

    def __call__(
        self,
        args: tuple[str, ...],
        /,
        *,
        env: dict[str, str],
        input: bytes,  # noqa: A002 - mirrors subprocess.run's parameter name
        pass_fds: tuple[int, ...],
        capture_output: bool,
        check: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls += 1
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")


def _write_tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, mode="w") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def _config(tmp_path: Path) -> RosterFetchConfig:
    return RosterFetchConfig(
        mirror_dir=tmp_path / "mirror",
        roster_path=tmp_path / "roster.yaml",
        allowed_signers=tmp_path / "allowed_signers",
        expected_principal="admin@example.invalid",
    )


def _refresh(tmp_path: Path, archive: Path) -> tuple[RosterFetchError, int]:
    signature_runner = _RecordingSignatureRunner()
    with pytest.raises(RosterFetchError) as caught:
        _ = refresh_roster(
            _config(tmp_path),
            git_runner=_StubArchiveRunner(archive),
            signature_runner=signature_runner,
        )
    return caught.value, signature_runner.calls


def test_an_oversized_member_is_refused_before_signature_verification(
    tmp_path: Path,
) -> None:
    # Given: a feed host serves a roster member far beyond any legitimate size.
    archive = _write_tar(
        tmp_path / "big-member.tar",
        {ROSTER_PATH: b"A" * (2 * 1024 * 1024), SIGNATURE_PATH: b"sig"},
    )

    error, signature_calls = _refresh(tmp_path, archive)

    # Then: rejected as an archive fault, and authentication never ran.
    assert error.reason == "ROSTER-ARCHIVE"
    assert "size limit" in error.detail
    assert signature_calls == 0


def test_an_oversized_archive_is_refused_before_it_is_opened(tmp_path: Path) -> None:
    # Given: a tar whose total size exceeds the ceiling (many bounded members).
    archive = _write_tar(
        tmp_path / "big-archive.tar",
        {f"roster/pad-{index:03d}": b"A" * (512 * 1024) for index in range(16)},
    )
    assert archive.stat().st_size > 4 * 1024 * 1024

    error, signature_calls = _refresh(tmp_path, archive)

    assert error.reason == "ROSTER-ARCHIVE"
    assert "size limit" in error.detail
    assert signature_calls == 0


def test_too_many_members_are_refused_before_signature_verification(
    tmp_path: Path,
) -> None:
    # Given: a hostile branch where the roster path is a tree, not a file.
    members = {f"roster/roster.yaml/part-{index:04d}": b"x" for index in range(200)}
    archive = _write_tar(tmp_path / "many-members.tar", members)

    error, signature_calls = _refresh(tmp_path, archive)

    assert error.reason == "ROSTER-ARCHIVE"
    assert "too many members" in error.detail
    assert signature_calls == 0


def test_a_normal_sized_archive_still_reaches_signature_verification(
    tmp_path: Path,
) -> None:
    # Given: an ordinary small roster — the caps must not break the happy path.
    archive = _write_tar(
        tmp_path / "normal.tar",
        {ROSTER_PATH: b"version: 1\nrevision: 2\n", SIGNATURE_PATH: b"signature"},
    )
    signature_runner = _RecordingSignatureRunner()
    _ = (tmp_path / "allowed_signers").write_text("admin\n", encoding="utf-8")

    # When: the refresh runs; verification is reached and (with this stub) fails.
    with pytest.raises(RosterFetchError) as caught:
        _ = refresh_roster(
            _config(tmp_path),
            git_runner=_StubArchiveRunner(archive),
            signature_runner=signature_runner,
        )

    # Then: the failure is a signature verdict, not a size rejection.
    assert caught.value.reason == "ROSTER-SIGNATURE"
    assert signature_runner.calls >= 1
