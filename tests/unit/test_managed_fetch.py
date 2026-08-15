from __future__ import annotations

import logging
import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import override

import pytest

from automation.managed_sync.fetch import (
    FetchResult,
    ManagedFetchError,
    ReleaseTag,
    list_release_tags,
    sync_remote,
    sync_roster_ref,
)

_GitOutcome = tuple[int, str, str]  # (returncode, stdout, stderr)


def _success_outcome(_index: int) -> _GitOutcome:
    return (0, "", "")


@dataclass(frozen=True, slots=True)
class CapturedCall:
    argv: tuple[str, ...]
    env: dict[str, str]
    timeout: float | None


class FakeRunner:
    def __init__(self, outcome_for_call: Callable[[int], _GitOutcome] | None = None) -> None:
        self.calls: list[CapturedCall] = []
        self._outcome_for_call: Callable[[int], _GitOutcome] = (
            outcome_for_call or _success_outcome
        )

    def __call__(
        self,
        argv: list[str],
        /,
        *,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert env is not None, "explicit env= is mandatory (watcher-cron 규약 b-2)"
        assert capture_output is True
        assert text is True
        self.calls.append(CapturedCall(tuple(argv), dict(env), timeout))
        returncode, stdout, stderr = self._outcome_for_call(len(self.calls))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


class RaisingRunner:
    def __call__(
        self,
        argv: list[str],
        /,
        *,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise OSError("git executable unavailable")


@dataclass(frozen=True, slots=True)
class _Config:
    remote_url: str
    mirror_dir: Path
    ssh_key_path: Path


def _config(tmp_path: Path) -> _Config:
    return _Config(
        remote_url="ssh://example.invalid/managed-skills.git",
        mirror_dir=tmp_path / "managed-sync" / "mirror",
        ssh_key_path=tmp_path / "key",
    )


def _disable_push_argv(mirror_dir: Path) -> tuple[str, ...]:
    return ("git", "-C", str(mirror_dir), "remote", "set-url", "--push", "origin", "DISABLED")


def _tag_fetch_argv(mirror_dir: Path) -> tuple[str, ...]:
    return (
        "git",
        "-C",
        str(mirror_dir),
        "fetch",
        "origin",
        "+refs/tags/*:refs/tags/*",
    )


def _roster_fetch_argv(mirror_dir: Path) -> tuple[str, ...]:
    return (
        "git",
        "-C",
        str(mirror_dir),
        "fetch",
        "origin",
        "+refs/heads/roster:refs/heads/roster",
    )


def _git_verb(argv: tuple[str, ...]) -> str:
    return argv[3] if argv[1] == "-C" else argv[1]


def test_sync_remote_clones_absent_mirror_fetches_tags_and_disables_push(tmp_path: Path) -> None:
    # Given — no mirror on disk yet
    config = _config(tmp_path)
    runner = FakeRunner()

    # When
    result = sync_remote(config, runner=runner)

    # Then — disable writes before the explicit tag and roster fetch.
    assert result == FetchResult(mirror_dir=config.mirror_dir, fetched=False, cloned=True)
    assert [call.argv for call in runner.calls] == [
        ("git", "clone", "--", config.remote_url, str(config.mirror_dir)),
        _disable_push_argv(config.mirror_dir),
        _tag_fetch_argv(config.mirror_dir),
    ]
    assert stat.S_IMODE(config.mirror_dir.parent.stat().st_mode) == 0o700
    for call in runner.calls:
        assert "GIT_SSH_COMMAND" in call.env


def test_sync_remote_fetches_existing_mirror_tags_without_prune(tmp_path: Path) -> None:
    # Given — an existing mirror
    config = _config(tmp_path)
    (config.mirror_dir / ".git").mkdir(parents=True)
    runner = FakeRunner()

    # When
    result = sync_remote(config, runner=runner)

    # Then — tag + roster fetch (never --prune: decision 16) + push-URL re-hardening
    assert result == FetchResult(mirror_dir=config.mirror_dir, fetched=True, cloned=False)
    assert [call.argv for call in runner.calls] == [
        _tag_fetch_argv(config.mirror_dir),
        _disable_push_argv(config.mirror_dir),
    ]
    assert all("--prune" not in call.argv for call in runner.calls)


def test_sync_roster_ref_fetches_fixed_branch_in_the_same_mirror(tmp_path: Path) -> None:
    # Given: skill sync already created the shared read-only mirror.
    config = _config(tmp_path)
    runner = FakeRunner()

    # When: the independent roster side path refreshes its transport ref.
    sync_roster_ref(config, runner=runner)

    # Then: it fetches only the fixed roster branch and never prunes skill state.
    assert [call.argv for call in runner.calls] == [_roster_fetch_argv(config.mirror_dir)]
    assert "--prune" not in runner.calls[0].argv


def test_sync_remote_passes_explicit_env_with_git_ssh_command_on_every_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given — both branches exercised with one recording runner
    config = _config(tmp_path)
    monkeypatch.setenv("PATH", "/bin/git-fixture")
    runner = FakeRunner()

    # When
    _ = sync_remote(config, runner=runner)  # clone branch
    (config.mirror_dir / ".git").mkdir(parents=True)
    _ = sync_remote(config, runner=runner)  # fetch branch

    # Then — every call carries an explicit env built from the key path, merged onto os.environ
    assert runner.calls
    expected_ssh = f"ssh -i {config.ssh_key_path} -o IdentitiesOnly=yes"
    assert all(call.env["GIT_SSH_COMMAND"] == expected_ssh for call in runner.calls)
    assert all(call.env["PATH"] == os.environ["PATH"] for call in runner.calls)


class _RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)



def test_list_release_tags_returns_numeric_sorted_tags_and_skips_malformed(
    tmp_path: Path,
) -> None:
    # Given — mixed valid and malformed tag names from git
    listing = "managed-x/v2\nmanaged-x/v10\ngarbage\nmanaged-x/vX\nmanaged-x/v0\nmanaged-x/v1\n"
    runner = FakeRunner(outcome_for_call=lambda _index: (0, listing, ""))
    mirror = tmp_path / "mirror"
    handler = _RecordingHandler()
    logger = logging.getLogger("automation.managed_sync.fetch")
    logger.addHandler(handler)

    # When
    try:
        tags = list_release_tags(mirror, "managed-x", runner=runner)
    finally:
        logger.removeHandler(handler)

    # Then — only valid tags, sequence-sorted numerically (1, 2, 10 — not lexical)
    assert tags == (
        ReleaseTag(skill="managed-x", sequence=1, tag_name="managed-x/v1"),
        ReleaseTag(skill="managed-x", sequence=2, tag_name="managed-x/v2"),
        ReleaseTag(skill="managed-x", sequence=10, tag_name="managed-x/v10"),
    )
    assert runner.calls[0].argv == ("git", "-C", str(mirror), "tag", "--list", "managed-x/v*")
    # Malformed tags are reported once (count is a structured log arg), never fatal
    assert [record.levelno for record in handler.records] == [logging.WARNING]
    assert handler.records[0].args == (3, "managed-x")


def test_no_recorded_git_invocation_ever_uses_the_push_verb(tmp_path: Path) -> None:
    # Given — every code path recorded through one runner
    config = _config(tmp_path)
    runner = FakeRunner(outcome_for_call=lambda _index: (0, "managed-x/v1\n", ""))

    # When
    _ = sync_remote(config, runner=runner)  # clone branch
    (config.mirror_dir / ".git").mkdir(parents=True)
    _ = sync_remote(config, runner=runner)  # fetch branch
    _ = list_release_tags(config.mirror_dir, "managed-x", runner=runner)

    # Then — no git subcommand is ever `push` (set-url --push ... DISABLED is config, not a push)
    assert runner.calls
    assert all(_git_verb(call.argv) != "push" for call in runner.calls)


def test_sync_remote_raises_managed_fetch_error_with_git_stderr(tmp_path: Path) -> None:
    # Given
    config = _config(tmp_path)
    runner = FakeRunner(
        outcome_for_call=lambda _index: (128, "", "fatal: could not read from remote")
    )

    # When / Then
    with pytest.raises(ManagedFetchError, match="could not read from remote"):
        _ = sync_remote(config, runner=runner)


def test_list_release_tags_raises_managed_fetch_error_on_nonzero(tmp_path: Path) -> None:
    # Given
    runner = FakeRunner(outcome_for_call=lambda _index: (128, "", "fatal: not a git repository"))

    # When / Then
    with pytest.raises(ManagedFetchError, match="not a git repository"):
        _ = list_release_tags(tmp_path / "mirror", "managed-x", runner=runner)


def test_sync_remote_raises_managed_fetch_error_when_runner_raises(tmp_path: Path) -> None:
    # Given / When / Then — OSError from the runner is fail-closed, not propagated raw
    with pytest.raises(ManagedFetchError):
        _ = sync_remote(_config(tmp_path), runner=RaisingRunner())
