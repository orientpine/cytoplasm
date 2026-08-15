from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.rag_ingest.config import ObsidianSourceConfig
from automation.rag_ingest.sources.obsidian import (
    ObsidianSyncError,
    mirror_is_healthy,
    sync_mirror,
)


@dataclass(frozen=True, slots=True)
class CapturedCall:
    argv: tuple[str, ...]
    cwd: Path | None
    env: dict[str, str]
    timeout: float | None


class FakeRunner:
    def __init__(self, result_for_call: Callable[[int], int] | None = None) -> None:
        self.calls: list[CapturedCall] = []
        self._result_for_call = result_for_call or (lambda _index: 0)

    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[list[str]]:
        assert env is not None
        self.calls.append(CapturedCall(tuple(argv), cwd, dict(env), timeout))
        return subprocess.CompletedProcess(argv, self._result_for_call(len(self.calls)))


class RaisingRunner:
    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[list[str]]:
        raise OSError("git executable unavailable")


def _config(tmp_path: Path) -> ObsidianSourceConfig:
    return ObsidianSourceConfig(
        enabled=True,
        repo_url="git@example.test:cha/obsidian.git",
        mirror_dir=tmp_path / "mirror",
        ssh_key_path=tmp_path / "id_ed25519",
        sensitivity_rules_path=tmp_path / "sensitivity.yaml",
        branch="decision-twin",
    )


def _assert_git_ssh_env(call: CapturedCall, key_path: Path) -> None:
    assert call.env["GIT_SSH_COMMAND"] == f"ssh -i {key_path} -o IdentitiesOnly=yes"
    assert call.env["GIT_SSH_COMMAND"].endswith("-o IdentitiesOnly=yes")


def _assert_no_git_push_verb(calls: list[CapturedCall]) -> None:
    assert all(call.argv[:2] != ("git", "push") for call in calls)


def test_sync_mirror_clones_absent_mirror_and_disables_push_url(tmp_path: Path) -> None:
    # Given
    config = _config(tmp_path)
    runner = FakeRunner()

    # When
    result = sync_mirror(config, runner=runner)

    # Then
    assert result.action == "cloned"
    assert result.mirror_dir == config.mirror_dir
    assert [call.argv for call in runner.calls] == [
        ("git", "clone", "--branch", config.branch, "--", config.repo_url, str(config.mirror_dir)),
        ("git", "remote", "set-url", "--push", "origin", "DISABLED"),
    ]
    assert [call.cwd for call in runner.calls] == [None, config.mirror_dir]
    assert stat.S_IMODE(config.mirror_dir.stat().st_mode) == 0o700
    for call in runner.calls:
        _assert_git_ssh_env(call, config.ssh_key_path)
        assert call.timeout is not None
    _assert_no_git_push_verb(runner.calls)


def test_sync_mirror_fetches_and_hard_resets_existing_git_mirror(tmp_path: Path) -> None:
    # Given — a healthy mirror (HEAD resolvable)
    config = _config(tmp_path)
    (config.mirror_dir / ".git").mkdir(parents=True)
    runner = FakeRunner()

    # When
    result = sync_mirror(config, runner=runner)

    # Then — health probe, fetch, hard reset, then push-URL hardening (SI-5)
    assert result.action == "fetched"
    assert result.mirror_dir == config.mirror_dir
    assert [call.argv for call in runner.calls] == [
        ("git", "-C", str(config.mirror_dir), "rev-parse", "--verify", "HEAD"),
        ("git", "fetch"),
        ("git", "reset", "--hard", f"origin/{config.branch}"),
        ("git", "remote", "set-url", "--push", "origin", "DISABLED"),
    ]
    assert [call.cwd for call in runner.calls] == [
        None,
        config.mirror_dir,
        config.mirror_dir,
        config.mirror_dir,
    ]
    for call in runner.calls[1:]:
        _assert_git_ssh_env(call, config.ssh_key_path)
    for call in runner.calls:
        assert call.timeout is not None
    _assert_no_git_push_verb(runner.calls)


def test_sync_mirror_env_starts_from_parent_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    config = _config(tmp_path)
    monkeypatch.setenv("PATH", "/bin/git-fixture")
    runner = FakeRunner()

    # When
    sync_mirror(config, runner=runner)

    # Then
    assert runner.calls
    assert all(call.env["PATH"] == os.environ["PATH"] for call in runner.calls)
    assert all("GIT_SSH_COMMAND" in call.env for call in runner.calls)


def test_sync_mirror_raises_on_nonzero_git_result(tmp_path: Path) -> None:
    # Given
    config = _config(tmp_path)
    runner = FakeRunner(result_for_call=lambda index: 1 if index == 1 else 0)

    # When / Then
    with pytest.raises(ObsidianSyncError):
        sync_mirror(config, runner=runner)


def test_sync_mirror_raises_when_runner_raises(tmp_path: Path) -> None:
    # Given
    config = _config(tmp_path)

    # When / Then
    with pytest.raises(ObsidianSyncError):
        sync_mirror(config, runner=RaisingRunner())



def test_sync_mirror_clone_uses_long_timeout_and_hardening_stays_short(tmp_path: Path) -> None:
    # Given — no mirror yet (initial clone of a large vault)
    config = _config(tmp_path)
    runner = FakeRunner()

    # When
    sync_mirror(config, runner=runner)

    # Then — only the clone gets the long budget; set-url keeps the short one
    clone_call, set_url_call = runner.calls
    assert clone_call.argv[:2] == ("git", "clone")
    assert clone_call.timeout == 3600.0
    assert set_url_call.timeout == 120.0


def test_sync_mirror_fetch_path_keeps_short_timeouts(tmp_path: Path) -> None:
    # Given — a healthy mirror
    config = _config(tmp_path)
    (config.mirror_dir / ".git").mkdir(parents=True)
    runner = FakeRunner()

    # When
    sync_mirror(config, runner=runner)

    # Then — fetch/reset/set-url (and the probe) all stay at the short budget
    assert runner.calls
    assert all(call.timeout == 120.0 for call in runner.calls)


def test_sync_mirror_self_heals_partial_clone_by_recloning(tmp_path: Path) -> None:
    # Given — .git exists but HEAD is unresolvable (interrupted clone) + leftovers
    config = _config(tmp_path)
    (config.mirror_dir / ".git").mkdir(parents=True)
    leftover = config.mirror_dir / "partial-marker"
    _ = leftover.write_text("partial", encoding="utf-8")
    runner = FakeRunner(result_for_call=lambda index: 1 if index == 1 else 0)

    # When
    result = sync_mirror(config, runner=runner)

    # Then — partial mirror removed, fresh clone issued, push URL disabled
    assert result.action == "cloned"
    assert not leftover.exists()
    assert [call.argv for call in runner.calls] == [
        ("git", "-C", str(config.mirror_dir), "rev-parse", "--verify", "HEAD"),
        ("git", "clone", "--branch", config.branch, "--", config.repo_url, str(config.mirror_dir)),
        ("git", "remote", "set-url", "--push", "origin", "DISABLED"),
    ]
    _assert_no_git_push_verb(runner.calls)


def test_mirror_is_healthy_false_without_git_dir(tmp_path: Path) -> None:
    # Given / When
    runner = FakeRunner()
    healthy = mirror_is_healthy(tmp_path / "mirror", runner=runner)

    # Then — short-circuits before ever invoking git
    assert healthy is False
    assert runner.calls == []


def test_mirror_is_healthy_true_when_head_resolves(tmp_path: Path) -> None:
    # Given
    mirror = tmp_path / "mirror"
    (mirror / ".git").mkdir(parents=True)

    # When / Then
    assert mirror_is_healthy(mirror, runner=FakeRunner()) is True


def test_mirror_is_healthy_false_when_head_unresolvable(tmp_path: Path) -> None:
    # Given
    mirror = tmp_path / "mirror"
    (mirror / ".git").mkdir(parents=True)
    runner = FakeRunner(result_for_call=lambda _index: 1)

    # When / Then
    assert mirror_is_healthy(mirror, runner=runner) is False


def test_mirror_is_healthy_false_when_runner_raises(tmp_path: Path) -> None:
    # Given
    mirror = tmp_path / "mirror"
    (mirror / ".git").mkdir(parents=True)

    # When / Then — never raises, reports unhealthy
    assert mirror_is_healthy(mirror, runner=RaisingRunner()) is False