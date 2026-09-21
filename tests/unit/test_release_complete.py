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

#: 적용 완료 통지 스윕의 노드 프로브 — 테스트는 ssh 로 나가지 않는다.
_PROBE_STUB: Final = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$PROBE_CALLS"
printf '%s' "${PROBE_OUT:-}"
"""

#: 완결 리컨실의 전량 반영 — 어느 세대에서 불렸는지(PWD·HEAD)까지 기록한다.
_DEPLOY_STUB: Final = """#!/usr/bin/env bash
set -uo pipefail
printf '%s|%s|%s\\n' "$PWD" "$(git rev-parse HEAD)" "$*" >> "$DEPLOY_CALLS"
exit "${DEPLOY_RC:-0}"
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
    deploy_rc: int = 0,
    arguments: tuple[str, ...] = (),
    extra_env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    approval = _stub(tmp_path / "approval-stub", _APPROVAL_STUB)
    release = _stub(tmp_path / "release-stub", _RELEASE_STUB)
    probe = _stub(tmp_path / "probe-stub", _PROBE_STUB)
    deploy = _stub(tmp_path / "deploy-stub", _DEPLOY_STUB)
    return subprocess.run(
        ("bash", str(_COMMAND), *arguments),
        capture_output=True,
        text=True,
        check=False,
        cwd=None if cwd is None else str(cwd),
        env={
            **os.environ,
            "RELEASE_COMPLETE_STATE": str(state),
            "RELEASE_COMPLETE_SOURCE_REPO": str(source),
            "RELEASE_APPROVAL_CMD": f"bash {approval}",
            "RELEASE_COMPLETE_RELEASE_CMD": f"bash {release}",
            "RELEASE_APPLIED_PROBE_CMD": f"bash {probe}",
            "RELEASE_COMPLETE_DEPLOY_CMD": f"bash {deploy}",
            "DECISION_RC": str(decision_rc),
            "RELEASE_RC": str(release_rc),
            "DEPLOY_RC": str(deploy_rc),
            "CALLS": str(tmp_path / "calls.log"),
            "RELEASE_CALLS": str(tmp_path / "release-calls.log"),
            "PROBE_CALLS": str(tmp_path / "probe-calls.log"),
            "DEPLOY_CALLS": str(tmp_path / "deploy-calls.log"),
            **(extra_env or {}),
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
            "RELEASE_APPLIED_PROBE_CMD": f"bash {tmp_path / 'probe-stub'}",
            "RELEASE_COMPLETE_MAX_ATTEMPTS": "4",
            "DECISION_RC": "0",
            "RELEASE_RC": "0",
            "CALLS": str(tmp_path / "calls.log"),
            "RELEASE_CALLS": str(tmp_path / "release-calls.log"),
            "PROBE_CALLS": str(tmp_path / "probe-calls.log"),
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


def test_applied_notice_sweep_runs_before_the_completed_short_circuit(
    tmp_path: Path,
) -> None:
    """완결 마커가 있는 sha 야말로 통지 대상이다 — 단축회로 뒤에 두면 영영 못 본다.

    Given: 한 틱이 릴리스를 완결해 `completed/<head>` 를 남긴 상태
    When: 다음 틱이 돈다(같은 head, 이미 완결)
    Then: 스윕은 돌고 판정 로그를 남기되, 결정·릴리스 호출은 오늘과 똑같이 생략된다.
    """
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    head = _git(source, "rev-parse", "HEAD")

    first = _run(tmp_path, source, state, decision_rc=0)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _lines(tmp_path / "probe-calls.log") == []  # 완결 전에는 통지 대상이 없다
    decision_calls = _lines(tmp_path / "calls.log")
    release_calls = _lines(tmp_path / "release-calls.log")

    second = _run(tmp_path, source, state, decision_rc=0)

    assert second.returncode == 0, second.stdout + second.stderr
    assert len(_lines(tmp_path / "probe-calls.log")) == 1
    assert f"RELEASE-APPLIED-RETRY NO-TAG {head[:12]}" in second.stdout
    assert _lines(tmp_path / "calls.log") == decision_calls
    assert _lines(tmp_path / "release-calls.log") == release_calls
    _assert_decision_only(tmp_path)


def test_applied_notice_sweep_runs_on_a_pending_tick_without_releasing(
    tmp_path: Path,
) -> None:
    """Given: 승인 대기 틱, 그러나 옛 릴리스의 완결 마커가 남아 있다.

    When: 틱이 돈다
    Then: 통지 스윕은 승인 상태와 무관하게 돌고, 릴리스는 여전히 실행되지 않는다.
    """
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    older = "0123456789abcdef0123456789abcdef01234567"
    (state / "completed").mkdir(parents=True)
    _ = (state / "completed" / older).write_text("2026-09-05T00:00:00Z\n", encoding="utf-8")

    result = _run(tmp_path, source, state, decision_rc=7)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pending" in result.stdout
    assert f"RELEASE-APPLIED-RETRY NO-TAG {older[:12]}" in result.stdout
    assert len(_lines(tmp_path / "probe-calls.log")) == 1
    assert _lines(tmp_path / "release-calls.log") == []
    _assert_decision_only(tmp_path)


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



#: origin/main 에 착지한 "새" release_complete.sh — exec 됐음과 상속받은 잠금을 보고한다.
_FRESH_SCRIPT_STUB: Final = """#!/usr/bin/env bash
set -o pipefail
printf 'FRESH-RAN reexec=%s\\n' "$RELEASE_COMPLETE_REEXEC"
if ( exec 8>"$RELEASE_COMPLETE_STATE/lock"; flock -n 8 ); then
  printf 'LOCK free\\n'
else
  printf 'LOCK held\\n'
fi
exit 0
"""


def _commit_script(source: Path, content: str) -> None:
    """Land automation/release_complete.sh on the test origin's main."""
    script = source / "automation" / "release_complete.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    _ = script.write_text(content, encoding="utf-8")
    script.chmod(0o755)
    _ = _git(source, "add", "automation/release_complete.sh")
    _ = _git(source, "commit", "-m", "release_complete.sh on origin/main")
    _ = _git(source, "push", "origin", "main")


def test_self_update_when_origin_main_changed_the_script_then_execs_the_worktree_copy(
    tmp_path: Path,
) -> None:
    """Given: origin/main carries a newer release_complete.sh than the installed copy.

    When: a tick runs from the installed copy.
    Then: right after the worktree sync it execs the worktree copy once, still holding
    the lock, and the stale copy never reaches the decision.
    """
    _origin, source = _origin_and_source(tmp_path)
    _commit_script(source, _FRESH_SCRIPT_STUB)
    state = tmp_path / "state"

    result = _run(tmp_path, source, state, decision_rc=7)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF-UPDATE" in result.stdout
    assert "FRESH-RAN reexec=1" in result.stdout
    assert "LOCK held" in result.stdout
    assert _lines(tmp_path / "calls.log") == []


def test_self_update_when_reexec_guard_is_set_then_runs_in_place(tmp_path: Path) -> None:
    """The loop guard: an already re-exec'd tick never execs again, even if copies differ."""
    _origin, source = _origin_and_source(tmp_path)
    _commit_script(source, _FRESH_SCRIPT_STUB)
    state = tmp_path / "state"

    result = _run(
        tmp_path, source, state, decision_rc=7, extra_env={"RELEASE_COMPLETE_REEXEC": "1"}
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF-UPDATE" not in result.stdout
    assert "FRESH-RAN" not in result.stdout
    assert "pending" in result.stdout
    _assert_decision_only(tmp_path)
    assert len(_lines(tmp_path / "calls.log")) == 1


def test_self_update_when_copies_are_identical_then_runs_in_place(tmp_path: Path) -> None:
    """Byte-identical copies mean the installed script IS origin/main — no exec, no noise."""
    _origin, source = _origin_and_source(tmp_path)
    _commit_script(source, _COMMAND.read_text(encoding="utf-8"))
    state = tmp_path / "state"

    result = _run(tmp_path, source, state, decision_rc=7)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELF-UPDATE" not in result.stdout
    assert "pending" in result.stdout
    _assert_decision_only(tmp_path)
    assert len(_lines(tmp_path / "calls.log")) == 1


def _tag_and_advance(source: Path, tag: str, *, commits: int = 2) -> str:
    """릴리스 태그를 자른 뒤 origin/main 을 그 앞으로 민다 — 2026-09-08 v1.6.3 사고의 모양.

    Returns: 태그가 가리키는 커밋 sha.
    """
    tagged = _git(source, "rev-parse", "HEAD")
    _ = _git(source, "tag", "-a", tag, "-m", tag)
    _ = _git(source, "push", "origin", tag)
    for index in range(commits):
        _ = (source / f"after-{index}").write_text("later\n", encoding="utf-8")
        _ = _git(source, "add", f"after-{index}")
        _ = _git(source, "commit", "-m", f"after {index}")
    _ = _git(source, "push", "origin", "main")
    return tagged


def test_reconcile_completes_the_tagged_release_after_origin_main_moved_on(
    tmp_path: Path,
) -> None:
    """Given: ✅ 를 받아 서명 태그까지 잘린 릴리스 위로 origin/main 이 2커밋 전진했다.

    When: 완결기가 돈다 — 살아 있는 승인 요청은 다른 HEAD 에 묶여 결정이 rc 2 다.
    Then: 그 **태그된 sha 로 워크트리를 옮겨** 전량 반영을 돌리고 완결 마커를 남긴 뒤
    워크트리를 origin/main 으로 되돌린다. 태그는 자르지 않는다(release.sh 미호출) —
    옛 ✅ 가 새 tip 을 인가하는 문은 그대로 잠겨 있다.
    """
    _origin, source = _origin_and_source(tmp_path)
    tagged = _tag_and_advance(source, "v9.9.9")
    state = tmp_path / "state"

    result = _run(tmp_path, source, state, decision_rc=2)

    assert result.returncode == 0, result.stdout + result.stderr
    worktree = state / "worktree"
    assert _lines(tmp_path / "deploy-calls.log") == [f"{worktree}|{tagged}|--apply"]
    assert (state / "completed" / tagged).is_file()
    assert _git(worktree, "rev-parse", "HEAD") == _git(source, "rev-parse", "origin/main")
    assert _lines(tmp_path / "release-calls.log") == []
    assert "reconcil" in result.stdout


def test_reconcile_defers_without_counting_when_the_node_has_not_converged(
    tmp_path: Path,
) -> None:
    """노드가 아직 그 릴리스가 아니면(deploy_all rc 4) 전이 상태다 — 시도로 세지 않는다.

    세면 2분 틱 × 3 = 6분 만에 영구 포기가 되어 2026-09-08 사고가 그대로 재현된다
    (그날 노드 수렴은 52분 걸렸다).
    """
    _origin, source = _origin_and_source(tmp_path)
    tagged = _tag_and_advance(source, "v9.9.9")
    state = tmp_path / "state"

    for _ in range(3):
        deferred = _run(tmp_path, source, state, decision_rc=2, deploy_rc=4)
        assert deferred.returncode == 0, deferred.stdout + deferred.stderr
        assert "RECONCILE-DEFER" in deferred.stdout
    assert not (state / "attempts" / f"reconcile-{tagged}").exists()
    assert not (state / "completed" / tagged).exists()
    assert len(_lines(tmp_path / "deploy-calls.log")) == 3

    converged = _run(tmp_path, source, state, decision_rc=2)

    assert converged.returncode == 0, converged.stdout + converged.stderr
    assert len(_lines(tmp_path / "deploy-calls.log")) == 4
    assert (state / "completed" / tagged).is_file()


def test_reconcile_failures_stop_at_the_per_sha_attempt_cap(tmp_path: Path) -> None:
    """전이가 아닌 실패는 상한을 쓴다 — 지속 결함을 매 틱 전량 재배포로 되풀이하지 않는다."""
    _origin, source = _origin_and_source(tmp_path)
    tagged = _tag_and_advance(source, "v9.9.9")
    state = tmp_path / "state"

    for _ in range(3):
        failed = _run(tmp_path, source, state, decision_rc=2, deploy_rc=3)
        assert failed.returncode == 0, failed.stdout + failed.stderr
        assert "RECONCILE-FAIL rc=3" in failed.stdout
    assert (state / "attempts" / f"reconcile-{tagged}").read_text(
        encoding="utf-8"
    ).strip() == "3"

    gave_up = _run(tmp_path, source, state, decision_rc=2, deploy_rc=3)

    assert gave_up.returncode == 0, gave_up.stdout + gave_up.stderr
    assert "RECONCILE-GIVEUP" in gave_up.stdout
    assert len(_lines(tmp_path / "deploy-calls.log")) == 3
    assert not (state / "completed" / tagged).exists()


def test_reconcile_stays_out_of_the_way_when_the_tip_is_the_release(
    tmp_path: Path,
) -> None:
    """정상 경로: 팁이 곧 릴리스면 완결은 기존 승인 경로가 소유한다 — 리컨실은 무동작."""
    _origin, source = _origin_and_source(tmp_path)
    _ = _tag_and_advance(source, "v9.9.9", commits=0)
    state = tmp_path / "state"
    head = _git(source, "rev-parse", "HEAD")

    result = _run(tmp_path, source, state, decision_rc=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _lines(tmp_path / "deploy-calls.log") == []
    assert len(_lines(tmp_path / "release-calls.log")) == 1
    assert (state / "completed" / head).is_file()


def test_reconcile_stays_out_of_the_way_without_a_release_tag(tmp_path: Path) -> None:
    """태그가 없으면 인가된 릴리스도 없다 — 리컨실 대상이 아니다(fail-closed)."""
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"
    warm_up = _run(tmp_path, source, state, decision_rc=2)
    assert warm_up.returncode == 0, warm_up.stdout + warm_up.stderr

    result = _run(tmp_path, source, state, decision_rc=2)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert _lines(tmp_path / "deploy-calls.log") == []


def test_reconcile_does_not_repeat_a_completed_release(tmp_path: Path) -> None:
    """완결 마커가 곧 멱등 열쇠다 — 같은 릴리스를 매 틱 다시 반영하지 않는다."""
    _origin, source = _origin_and_source(tmp_path)
    tagged = _tag_and_advance(source, "v9.9.9")
    state = tmp_path / "state"
    first = _run(tmp_path, source, state, decision_rc=2)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (state / "completed" / tagged).is_file()

    second = _run(tmp_path, source, state, decision_rc=2)

    assert second.returncode == 0, second.stdout + second.stderr
    assert len(_lines(tmp_path / "deploy-calls.log")) == 1


def test_the_decision_carries_the_cut_release_so_it_is_not_called_stale(
    tmp_path: Path,
) -> None:
    """Given: 릴리스 태그가 잘린 뒤 origin/main 이 그 앞으로 전진했다.

    When: 완결기가 소유자 결정을 묻는다.
    Then: 그 결정에 **잘린 릴리스 sha** 를 함께 실어 보낸다.

    이 사실이 없으면 결정 경로는 이미 실행된 릴리스를 낡은 승인으로 오인해 소유자에게
    거짓 ⛔("…가 origin/main …와 달라 자동 완결할 수 없습니다")를 보낸다 — 2026-09-10
    v1.6.7 실측(완결·적용 통지가 이미 나간 릴리스였다). 판정은 `release_approval` 이
    단독으로 하고 완결기는 사실만 나른다(사본 0).
    """
    _origin, source = _origin_and_source(tmp_path)
    tagged = _tag_and_advance(source, "v9.9.9")
    state = tmp_path / "state"

    result = _run(tmp_path, source, state, decision_rc=2)

    assert result.returncode == 0, result.stdout + result.stderr
    tip = _git(source, "rev-parse", "origin/main")
    assert _lines(tmp_path / "calls.log") == [
        f"decision --head {tip} --notify-stale --completion-candidate --tagged {tagged}"
    ]


def test_the_decision_omits_the_cut_release_when_no_release_tag_exists(
    tmp_path: Path,
) -> None:
    """태그가 없는 이력에는 실을 사실이 없다 — argv 는 예전과 바이트 그대로다."""
    _origin, source = _origin_and_source(tmp_path)
    state = tmp_path / "state"

    result = _run(tmp_path, source, state, decision_rc=7)

    assert result.returncode == 0, result.stdout + result.stderr
    head = _git(source, "rev-parse", "origin/main")
    assert _lines(tmp_path / "calls.log") == [f"decision --head {head} --notify-stale --completion-candidate"]


def test_a_stale_checkout_as_cwd_does_not_shadow_the_completer_runtime(
    tmp_path: Path,
) -> None:
    """Given: 유닛의 WorkingDirectory 는 메인 체크아웃인데 self-update 는 워크트리 사본을 exec 한다.

    When: 그 낡은 체크아웃을 cwd 로 둔 채 틱이 돈다.
    Then: 리컨실도 적용 완료 스윕도 워크트리 세대의 모듈을 쓴다.

    `python3 -m` 은 sys.path[0] 에 cwd 를 넣으므로 낡은 체크아웃의 automation 패키지가
    워크트리의 새 모듈을 가린다 — 2026-09-09 실측: 완결기가 매 틱
    `No module named automation.release_completion_target` 만 남기고 리컨실이 조용히 죽었다.
    """
    _origin, source = _origin_and_source(tmp_path)
    tagged = _tag_and_advance(source, "v9.9.9")
    state = tmp_path / "state"
    stale = tmp_path / "stale-checkout"
    (stale / "automation").mkdir(parents=True)
    _ = (stale / "automation" / "__init__.py").write_text("", encoding="utf-8")

    result = _run(tmp_path, source, state, decision_rc=2, cwd=stale)

    assert result.returncode == 0, result.stdout + result.stderr
    worktree = state / "worktree"
    assert _lines(tmp_path / "deploy-calls.log") == [f"{worktree}|{tagged}|--apply"]
    assert (state / "completed" / tagged).is_file()

    second = _run(tmp_path, source, state, decision_rc=2, cwd=stale)

    assert second.returncode == 0, second.stdout + second.stderr
    assert len(_lines(tmp_path / "probe-calls.log")) == 1
