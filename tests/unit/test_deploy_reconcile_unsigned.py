"""Unsigned origin/main observation and first-tick notification wiring."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import automation.deploy_reconcile_cli as reconcile_cli
from automation.deploy_reconcile import FAILURE_NOTICE_THRESHOLD
from automation.deploy_reconcile_unsigned import raw_remote_main_sha
from automation.git_tag_signature import GitRunner
from automation.update_trust import UpdateTrustError

_A = "a" * 40
_B = "b" * 40


def _runner(
    stdout: str,
    observed: list[tuple[list[str], float]] | None = None,
) -> GitRunner:
    def run(
        args: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text
        if observed is not None:
            observed.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return run


def test_raw_remote_main_sha_uses_only_read_only_ls_remote(tmp_path: Path) -> None:
    # Given: the remote reports one exact origin/main object id.
    observed: list[tuple[list[str], float]] = []

    # When: the advisory observer reads the public head.
    result = raw_remote_main_sha(
        tmp_path,
        "git@example.invalid:group/repo.git",
        _runner(f"{_B}\trefs/heads/main\n", observed),
    )

    # Then: the only command is a read-only exact-ref query.
    assert result == _B
    assert observed == [
        (
            [
                "git",
                "-C",
                str(tmp_path),
                "ls-remote",
                "git@example.invalid:group/repo.git",
                "refs/heads/main",
            ],
            30.0,
        )
    ]


@pytest.mark.parametrize(
    "stdout",
    (
        "",
        f"{_B}\trefs/heads/not-main\n",
        "not-an-object-id\trefs/heads/main\n",
        f"{_B}\trefs/heads/main\n{_A}\trefs/heads/main\n",
    ),
)
def test_raw_remote_main_sha_rejects_malformed_results(
    tmp_path: Path,
    stdout: str,
) -> None:
    # Given: the advisory read is absent, wrong-ref, malformed, or ambiguous.
    # When/Then: no SHA is exposed to the notice lifecycle.
    assert raw_remote_main_sha(tmp_path, runner=_runner(stdout)) == ""


def test_main_unsigned_head_records_backlog_without_paging(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Given: the public branch head has no trusted signed release tag — under VA-3
    # (머지=축적) this is the NORMAL state between releases, not an incident.
    calls: list[str] = []
    notices: list[str] = []

    def blocked_target() -> str:
        raise UpdateTrustError("UNSIGNED-HEAD", "origin/main lacks a signed release tag")

    def unexpected_release(_target: str, _prior: str) -> int:
        calls.append("release")
        return 0

    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", blocked_target)
    monkeypatch.setattr(
        reconcile_cli,
        "raw_remote_main_sha",
        lambda _mirror, _channel: _B,
    )
    monkeypatch.setattr(
        reconcile_cli, "unreleased_commit_count", lambda _mirror, _current, _head: 4
    )
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "run_release_update", unexpected_release)
    monkeypatch.setattr(reconcile_cli, "current_release_sha", lambda: _A)
    monkeypatch.setattr(reconcile_cli, "notify_owner", lambda notice: not notices.append(notice))

    # When: several ticks observe the same young backlog.
    results = [reconcile_cli.main() for _ in range(FAILURE_NOTICE_THRESHOLD)]

    # Then: no notice and no root helper — the backlog is counted only; aging past the
    # digest threshold is pinned by tests/unit/test_release_backlog_digest.py.
    assert results == [0] * FAILURE_NOTICE_THRESHOLD
    assert calls == []
    assert notices == []
    state = reconcile_cli.load_state(tmp_path / "state.json")
    assert state.skip_reason == "release-backlog"
    assert state.notified_target is None
    assert state.consecutive_failures == FAILURE_NOTICE_THRESHOLD
    assert "UPDATE-TRUST-BLOCK UNSIGNED-HEAD" in capsys.readouterr().err


def test_unsigned_head_with_unresolved_sha_keeps_threshold_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: signature resolution identifies UNSIGNED-HEAD but advisory re-read fails.
    notices: list[str] = []

    def blocked_target() -> str:
        raise UpdateTrustError("UNSIGNED-HEAD", "origin/main lacks a signed release tag")

    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", blocked_target)
    monkeypatch.setattr(
        reconcile_cli,
        "raw_remote_main_sha",
        lambda _mirror, _channel: "",
    )
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "notify_owner", lambda notice: not notices.append(notice))

    # When: the generic threshold elapses.
    results = [reconcile_cli.main() for _ in range(FAILURE_NOTICE_THRESHOLD)]

    # Then: there is no immediate unresolved message; the legacy fallback fires once.
    assert results == [0] * FAILURE_NOTICE_THRESHOLD
    assert len(notices) == 1
    assert "unresolved" in notices[0]
    assert reconcile_cli.load_state(tmp_path / "state.json").notified_target == (
        "skip:update-trust-block"
    )


def test_non_unsigned_trust_error_keeps_threshold_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the trust boundary failed for a reason other than a missing release tag.
    notices: list[str] = []
    advisory_calls: list[tuple[Path, str | None]] = []

    def blocked_target() -> str:
        raise UpdateTrustError("TAG-FETCH", "remote unavailable")

    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", blocked_target)
    monkeypatch.setattr(
        reconcile_cli,
        "raw_remote_main_sha",
        lambda mirror, channel: advisory_calls.append((mirror, channel)) or _B,
    )
    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: None)
    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "notify_owner", lambda notice: not notices.append(notice))

    # When: three identical trust failures occur.
    results = [reconcile_cli.main() for _ in range(FAILURE_NOTICE_THRESHOLD)]

    # Then: raw unsigned-head observation is not attempted and legacy threshold remains.
    assert results == [0] * FAILURE_NOTICE_THRESHOLD
    assert advisory_calls == []
    assert len(notices) == 1
    assert reconcile_cli.load_state(tmp_path / "state.json").notified_target == (
        "skip:update-trust-block"
    )
