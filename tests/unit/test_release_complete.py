"""Workstation release completer: decision-only polling and resumable execution."""
from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_COMMAND: Final = _REPO / "automation" / "release_complete.sh"

_APPROVAL_STUB: Final = """#!/usr/bin/env bash
set -uo pipefail
printf '%s\\n' "$*" >> "$CALLS"
[[ "${1:-}" == decision ]] || exit 97
exit "${DECISION_RC:-7}"
"""

_RELEASE_STUB: Final = """#!/usr/bin/env bash
set -uo pipefail
printf '%s|%s\\n' "$PWD" "$RELEASE_REPO_ROOT" >> "$RELEASE_CALLS"
exit "${RELEASE_RC:-0}"
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *args),
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    ).stdout.strip()


def _origin_and_source(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    _ = subprocess.run(
        ("git", "init", "--bare", "-b", "main", str(origin)),
        check=True,
        capture_output=True,
    )
    source = tmp_path / "source"
    _ = subprocess.run(
        ("git", "clone", str(origin), str(source)),
        check=True,
        capture_output=True,
    )
    _ = _git(source, "config", "user.name", "release-complete-test")
    _ = _git(source, "config", "user.email", "release-complete@example.invalid")
    _ = _git(source, "config", "commit.gpgsign", "false")
    _ = (source / "tracked").write_text("clean\n", encoding="utf-8")
    _ = _git(source, "add", "tracked")
    _ = _git(source, "commit", "-m", "initial")
    _ = _git(source, "push", "-u", "origin", "main")
    return origin, source


def _stub(path: Path, content: str) -> Path:
    _ = path.write_text(content, encoding="utf-8")
    _ = path.chmod(0o755)
    return path


def _run(
    tmp_path: Path,
    source: Path,
    state: Path,
    *,
    decision_rc: int,
    release_rc: int = 0,
    arguments: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    approval = _stub(tmp_path / "approval-stub", _APPROVAL_STUB)
    release = _stub(tmp_path / "release-stub", _RELEASE_STUB)
    return subprocess.run(
        ("bash", str(_COMMAND), *arguments),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "RELEASE_COMPLETE_STATE": str(state),
            "RELEASE_COMPLETE_SOURCE_REPO": str(source),
            "RELEASE_APPROVAL_CMD": f"bash {approval}",
            "RELEASE_COMPLETE_RELEASE_CMD": f"bash {release}",
            "DECISION_RC": str(decision_rc),
            "RELEASE_RC": str(release_rc),
            "CALLS": str(tmp_path / "calls.log"),
            "RELEASE_CALLS": str(tmp_path / "release-calls.log"),
        },
    )


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _assert_decision_only(tmp_path: Path) -> None:
    calls = _lines(tmp_path / "calls.log")
    assert all(line.startswith("decision --head ") for line in calls)


def test_pending_creates_a_detached_main_worktree_without_releasing(
    tmp_path: Path,
) -> None:
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"

    result = _run(tmp_path, source, state, decision_rc=7)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _lines(tmp_path / "release-calls.log") == []
    assert _git(state / "worktree", "rev-parse", "HEAD") == _git(
        source, "rev-parse", "origin/main"
    )
    symbolic = subprocess.run(
        ("git", "-C", str(state / "worktree"), "symbolic-ref", "-q", "HEAD"),
        capture_output=True,
        check=False,
    )
    assert symbolic.returncode == 1
    _assert_decision_only(tmp_path)


def test_approved_runs_release_once_and_records_completion(tmp_path: Path) -> None:
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    head = _git(source, "rev-parse", "HEAD")

    result = _run(tmp_path, source, state, decision_rc=0)

    assert result.returncode == 0, result.stdout + result.stderr
    expected_worktree = str(state / "worktree")
    assert _lines(tmp_path / "release-calls.log") == [
        f"{expected_worktree}|{expected_worktree}"
    ]
    assert (state / "completed" / head).is_file()
    _assert_decision_only(tmp_path)


def test_completed_head_short_circuits_before_decision_or_release(tmp_path: Path) -> None:
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    first = _run(tmp_path, source, state, decision_rc=0)
    assert first.returncode == 0, first.stdout + first.stderr
    decision_calls = _lines(tmp_path / "calls.log")
    release_calls = _lines(tmp_path / "release-calls.log")

    second = _run(tmp_path, source, state, decision_rc=0)

    assert second.returncode == 0, second.stdout + second.stderr
    assert _lines(tmp_path / "calls.log") == decision_calls
    assert _lines(tmp_path / "release-calls.log") == release_calls
    _assert_decision_only(tmp_path)


def test_held_lock_exits_silently_without_calls(tmp_path: Path) -> None:
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    lock_file = (state / "lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run(tmp_path, source, state, decision_rc=0)
    finally:
        lock_file.close()

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert _lines(tmp_path / "calls.log") == []
    assert _lines(tmp_path / "release-calls.log") == []


def test_release_failure_is_returned_without_a_marker(tmp_path: Path) -> None:
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    head = _git(source, "rev-parse", "HEAD")

    result = _run(tmp_path, source, state, decision_rc=0, release_rc=3)

    assert result.returncode == 3
    assert len(_lines(tmp_path / "release-calls.log")) == 1
    assert not (state / "completed" / head).exists()
    assert "COMPLETE-FAIL rc=3" in result.stdout
    assert (state / "attempts" / head).read_text(encoding="utf-8").strip() == "1"
    _assert_decision_only(tmp_path)


def test_repeated_failures_stop_at_the_per_sha_attempt_cap(tmp_path: Path) -> None:
    """A persistent defect must not turn into a full redeploy every tick.

    Three failing ticks consume the default cap; the fourth tick gives up without
    calling release.sh, and a later success (same sha, e.g. a hand-run fixed the
    node) clears the counter. A new sha starts from zero because the counter is
    keyed by head.
    """
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    head = _git(source, "rev-parse", "HEAD")

    for _ in range(3):
        assert _run(tmp_path, source, state, decision_rc=0, release_rc=3).returncode == 3
    assert (state / "attempts" / head).read_text(encoding="utf-8").strip() == "3"

    gave_up = _run(tmp_path, source, state, decision_rc=0, release_rc=3)

    assert gave_up.returncode == 0, gave_up.stdout + gave_up.stderr
    assert "COMPLETE-GIVEUP" in gave_up.stdout
    assert len(_lines(tmp_path / "release-calls.log")) == 3
    assert not (state / "completed" / head).exists()

    raised_cap = subprocess.run(
        ("bash", str(_COMMAND)),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "RELEASE_COMPLETE_STATE": str(state),
            "RELEASE_COMPLETE_SOURCE_REPO": str(source),
            "RELEASE_APPROVAL_CMD": f"bash {tmp_path / 'approval-stub'}",
            "RELEASE_COMPLETE_RELEASE_CMD": f"bash {tmp_path / 'release-stub'}",
            "RELEASE_COMPLETE_MAX_ATTEMPTS": "4",
            "DECISION_RC": "0",
            "RELEASE_RC": "0",
            "CALLS": str(tmp_path / "calls.log"),
            "RELEASE_CALLS": str(tmp_path / "release-calls.log"),
        },
    )

    assert raised_cap.returncode == 0, raised_cap.stdout + raised_cap.stderr
    assert len(_lines(tmp_path / "release-calls.log")) == 4
    assert (state / "completed" / head).exists()
    assert not (state / "attempts" / head).exists()
    _assert_decision_only(tmp_path)


def test_dirty_completer_worktree_is_refused_before_decision(tmp_path: Path) -> None:
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    initial = _run(tmp_path, source, state, decision_rc=7)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    _ = (tmp_path / "calls.log").unlink()
    _ = (state / "worktree" / "tracked").write_text("dirty\n", encoding="utf-8")

    result = _run(tmp_path, source, state, decision_rc=0)

    assert result.returncode == 5
    assert "COMPLETER-DIRTY" in result.stdout
    assert _lines(tmp_path / "calls.log") == []
    assert _lines(tmp_path / "release-calls.log") == []


def test_no_live_request_is_a_silent_success(tmp_path: Path) -> None:
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    initial = _run(tmp_path, source, state, decision_rc=7)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    _ = (tmp_path / "calls.log").unlink()

    result = _run(tmp_path, source, state, decision_rc=2)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert _lines(tmp_path / "release-calls.log") == []
    _assert_decision_only(tmp_path)


def test_cancelled_and_transient_decisions_do_not_release(tmp_path: Path) -> None:
    for decision_rc, message in ((9, "cancelled"), (255, "decision unavailable")):
        case = tmp_path / str(decision_rc)
        case.mkdir()
        _origin, source = _origin_and_source(case)
        result = _run(case, source, case / "state", decision_rc=decision_rc)

        assert result.returncode == 0
        assert message in result.stdout
        assert _lines(case / "release-calls.log") == []
        _assert_decision_only(case)


def test_unknown_argument_is_usage_error(tmp_path: Path) -> None:
    result = subprocess.run(
        ("bash", str(_COMMAND), "--bogus"),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(tmp_path)},
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_command_ships_executable() -> None:
    assert os.access(_COMMAND, os.X_OK)
