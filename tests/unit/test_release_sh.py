"""VA-1 release.sh: preconditions, bounded owner polling, and the signed-tag cut.

The approval producer and the local-CI gate are injected as stubs so the whole
owner-decision flow is driven without Discord or a real receipt store, while the
tag cut runs the REAL ``release_tag_lib.sh`` against a throwaway origin — the tag
is the artifact production consumes, so it must be observed, not stubbed.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_COMMAND: Final = _REPO / "automation" / "release.sh"
_LIB: Final = _REPO / "automation" / "release_tag_lib.sh"

_APPROVAL_STUB: Final = """#!/usr/bin/env bash
set -uo pipefail
cmd="$1"; shift
printf '%s\\n' "$cmd" >> "$CALLS"
case "$cmd" in
  plan) printf '{"version":"stub"}\\n' ;;
  retire) exit 0 ;;
  request)
    if [[ -f "$STALE_MARKER" ]]; then
      printf 'REFUSED: approval request not posted outcome=deferred reason=binding-mismatch\\n' >&2
      printf 'RELEASE-REQUEST-STALE: version=%s head=%s message_id=%s probe=%s\\n' \\
        "$STALE_VERSION" "$STALE_HEAD" "$STALE_MESSAGE_ID" "$STALE_PROBE" >&2
      exit 6
    fi
    printf '{"message_id":"123"}\\n'
    ;;
  abandon)
    printf 'abandon %s\\n' "$*" >> "$CALLS"
    (( ABANDON_UNBLOCKS == 1 )) && rm -f "$STALE_MARKER"
    exit "$ABANDON_RC"
    ;;
  decision)
    n="$(cat "$COUNTER" 2>/dev/null || printf 0)"
    n=$((n + 1))
    printf '%s' "$n" > "$COUNTER"
    # shellcheck disable=SC2086
    set -- $DECISIONS
    (( n > $# )) && n=$#
    exit "${!n}"
    ;;
  *) exit 97 ;;
esac
"""

#: 2026-08-31 실측 사건의 모양 — origin/main 이 지나가 버린 pending 요청의 신원.
_STALE_VERSION: Final = "v1.0.141"
_STALE_MESSAGE_ID: Final = "1408000000000000141"

_DEPLOY_ALL_STUB: Final = """#!/usr/bin/env bash
set -uo pipefail
printf 'deploy-all' >> "$CALLS"
printf ' %s' "$@" >> "$CALLS"
printf '\\n' >> "$CALLS"
exit "$DEPLOY_ALL_RC"
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *args),
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    ).stdout.strip()


def _origin_with_commits(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    _ = subprocess.run(("git", "init", "--bare", "-b", "main", str(origin)),
                       check=True, capture_output=True)
    work = tmp_path / "work"
    _ = subprocess.run(("git", "clone", str(origin), str(work)),
                       check=True, capture_output=True)
    _git(work, "config", "user.name", "release-sh-test")
    _git(work, "config", "user.email", "release-sh-test@example.invalid")
    _git(work, "config", "commit.gpgsign", "false")
    for index in range(2):
        _ = (work / f"f{index}").write_text(f"{index}\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", f"c{index}")
    _git(work, "push", "-u", "origin", "main")
    return origin, work


def _signing_key(tmp_path: Path) -> Path:
    key = tmp_path / "signer"
    if not key.exists():  # ssh-keygen refuses to overwrite; two _run calls share one key
        _ = subprocess.run(
            ("ssh-keygen", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)),
            check=True, capture_output=True,
        )
    return key


def _run(
    tmp_path: Path,
    work: Path,
    *,
    decisions: str,
    deadline: str = "5",
    local_ci_rc: str = "0",
    deploy_all_rc: str = "0",
    arguments: tuple[str, ...] = (),
    stale_head: str = "",
    stale_probe: str = "bound_pending",
    abandon_unblocks: str = "1",
    abandon_rc: str = "0",
) -> subprocess.CompletedProcess[str]:
    # 낡은 pending 요청이 있는 세계는 stale_head 를 준 테스트에서만 존재한다 —
    # marker 가 없으면 stub 의 request 는 예전 그대로 성공한다.
    stale_marker = tmp_path / "stale-marker"
    if stale_head:
        _ = stale_marker.write_text("stale\n", encoding="utf-8")
    approval = tmp_path / "approval-stub"
    _ = approval.write_text(_APPROVAL_STUB, encoding="utf-8")
    approval.chmod(0o755)
    local_ci = tmp_path / "local-ci-stub"
    _ = local_ci.write_text(
        "#!/usr/bin/env bash\n"
        'printf "verify %s\\n" "$2" >> "$CALLS"\n'
        f"exit {local_ci_rc}\n",
        encoding="utf-8",
    )
    local_ci.chmod(0o755)
    deploy_all = tmp_path / "deploy-all-stub"
    _ = deploy_all.write_text(_DEPLOY_ALL_STUB, encoding="utf-8")
    deploy_all.chmod(0o755)
    env = {
        **os.environ,
        "RELEASE_REPO_ROOT": str(work),
        "RELEASE_APPROVAL_CMD": f"bash {approval}",
        "RELEASE_LOCAL_CI": str(local_ci),
        "RELEASE_DEPLOY_ALL": str(deploy_all),
        "RELEASE_POLL_SECONDS": "0",
        "RELEASE_DEADLINE_SECONDS": deadline,
        "UPDATE_TRUST_SIGNING_KEY": str(_signing_key(tmp_path)),
        "CALLS": str(tmp_path / "calls.log"),
        "COUNTER": str(tmp_path / "counter"),
        "DECISIONS": decisions,
        "DEPLOY_ALL_RC": deploy_all_rc,
        "STALE_MARKER": str(stale_marker),
        "STALE_VERSION": _STALE_VERSION,
        "STALE_HEAD": stale_head,
        "STALE_MESSAGE_ID": _STALE_MESSAGE_ID,
        "STALE_PROBE": stale_probe,
        "ABANDON_UNBLOCKS": abandon_unblocks,
        "ABANDON_RC": abandon_rc,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ("bash", str(_COMMAND), *arguments),
        capture_output=True, text=True, check=False, env=env,
    )


def _calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls.log"
    return path.read_text(encoding="utf-8").split() if path.exists() else []


def _origin_tags(work: Path) -> str:
    return _git(work, "ls-remote", "--tags", "origin")


def _call_lines(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_an_approved_release_cuts_the_signed_tag(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run(tmp_path, work, decisions="2 7 0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)
    approval_calls = [c for c in _calls(tmp_path) if c in {"decision", "plan", "request"}]
    assert approval_calls[:3] == ["decision", "plan", "request"]
    # The tag cut no longer ends with a hint to run deploy_all by hand — release.sh
    # runs it itself and reports that completion.
    assert "receipt written by deploy_all" in result.stderr


def test_default_release_runs_the_full_deployment_once(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="0")

    assert result.returncode == 0, result.stdout + result.stderr
    deploy_calls = [line for line in _call_lines(tmp_path) if line.startswith("deploy-all")]
    assert deploy_calls == ["deploy-all --apply --wait-converge"]
    assert "fully deployed" in result.stderr


def test_no_deploy_cuts_the_tag_without_running_deployment(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run(tmp_path, work, decisions="0", arguments=("--no-deploy",))

    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(line.startswith("deploy-all") for line in _call_lines(tmp_path))
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)


def test_failed_deployment_keeps_the_signed_tag_and_exits_ten(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run(tmp_path, work, decisions="0", deploy_all_rc="1")

    assert result.returncode == 10
    assert "deploy_all rc=1" in result.stderr
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)


def test_a_denied_release_exits_nine_and_cuts_no_tag(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2 9")

    assert result.returncode == 9
    assert "refs/tags/v" not in _origin_tags(work)


def test_no_decision_times_out_without_a_tag_and_names_the_resume(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2 7", deadline="0")

    assert result.returncode == 8
    assert "refs/tags/v" not in _origin_tags(work)
    assert "재실행" in result.stderr


def test_transient_decision_failures_are_retried_until_a_real_answer(
    tmp_path: Path,
) -> None:
    """rc=255(SSH 불통) 한 번이 무기한 대기를 죽이면 안 된다 — 2026-08-31 실측."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run(tmp_path, work, decisions="2 7 255 7 0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)


def test_persistent_decision_failures_eventually_die(tmp_path: Path) -> None:
    result_work = _origin_with_commits(tmp_path)[1]

    result = _run(tmp_path, result_work, decisions="2 " + "255 " * 11)

    assert result.returncode == 1
    assert "refs/tags/v" not in _origin_tags(result_work)


def test_default_owner_wait_has_no_deadline() -> None:
    source = _COMMAND.read_text(encoding="utf-8")

    assert 'deadline_seconds="${RELEASE_DEADLINE_SECONDS:-}"' in source
    assert "${RELEASE_DEADLINE_SECONDS:-1800}" not in source


def test_an_already_approved_request_resumes_straight_to_the_tag(tmp_path: Path) -> None:
    """세션이 죽어도 재실행이 곧 재개다 — 요청을 다시 게시하지 않는다."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run(tmp_path, work, decisions="0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)
    assert "request" not in _calls(tmp_path)


def _abandons(tmp_path: Path) -> list[str]:
    """stub 이 실제 인자와 함께 기록한 abandon 호출 — 이름만 찍힌 줄은 세지 않는다."""
    return [line for line in _call_lines(tmp_path) if line.startswith("abandon --")]


def test_a_stale_pending_request_is_abandoned_once_and_the_request_retried(
    tmp_path: Path,
) -> None:
    """origin/main 이 지나간 pending 요청은 사람 손 없이 감사와 함께 놓여난다 — 2026-08-31 v1.0.141 실측."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")
    stale_head = _git(work, "rev-parse", "HEAD~1")  # 사건의 모양: 조상이지만 더는 tip 이 아니다

    result = _run(tmp_path, work, decisions="2 7 0", stale_head=stale_head)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)
    assert "reason=binding-mismatch" in result.stderr  # 거절 stderr 는 계속 터미널에 닿는다
    lines = _call_lines(tmp_path)
    abandons = _abandons(tmp_path)
    assert len(abandons) == 1
    assert f"--version {_STALE_VERSION}" in abandons[0]
    assert f"--head {stale_head}" in abandons[0]
    assert f"--message-id {_STALE_MESSAGE_ID}" in abandons[0]
    assert "--reason stale pending release superseded by origin/main advance" in abandons[0]
    requested = [index for index, line in enumerate(lines) if line == "request"]
    assert len(requested) == 2
    assert requested[0] < lines.index(abandons[0]) < requested[1]


def test_a_decided_pending_record_is_never_abandoned_by_the_recovery(tmp_path: Path) -> None:
    """소유자가 결정한 요청은 자동 복구가 건드리지 않는다 — abandon 은 운영자의 것이다."""
    _origin, work = _origin_with_commits(tmp_path)
    stale_head = _git(work, "rev-parse", "HEAD~1")

    result = _run(
        tmp_path, work, decisions="2", stale_head=stale_head, stale_probe="cancelled"
    )

    assert result.returncode != 0
    assert _abandons(tmp_path) == []
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_pending_record_bound_to_the_current_tip_is_not_stale(tmp_path: Path) -> None:
    """지금 tip 에 묶인 요청은 아직 릴리스될 수 있다 — 낡지 않았으므로 파괴하지 않는다."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run(tmp_path, work, decisions="2", stale_head=head)

    assert result.returncode != 0
    assert _abandons(tmp_path) == []
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_failed_abandon_keeps_the_original_refusal(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    stale_head = _git(work, "rev-parse", "HEAD~1")

    result = _run(tmp_path, work, decisions="2", stale_head=stale_head, abandon_rc="1")

    assert result.returncode != 0
    assert len(_abandons(tmp_path)) == 1
    assert [line for line in _call_lines(tmp_path) if line == "request"] == ["request"]
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_still_refused_retry_dies_after_exactly_one_extra_request(tmp_path: Path) -> None:
    """복구는 한 번만이다 — 다시 거절당해도 요청·폐기를 반복하지 않는다."""
    _origin, work = _origin_with_commits(tmp_path)
    stale_head = _git(work, "rev-parse", "HEAD~1")

    result = _run(
        tmp_path, work, decisions="2", stale_head=stale_head, abandon_unblocks="0"
    )

    assert result.returncode != 0
    assert len(_abandons(tmp_path)) == 1
    assert [line for line in _call_lines(tmp_path) if line == "request"] == [
        "request",
        "request",
    ]
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_dirty_tree_is_refused_before_any_approval_traffic(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    _ = (work / "f0").write_text("changed\n", encoding="utf-8")

    result = _run(tmp_path, work, decisions="0")

    assert result.returncode == 4
    assert _calls(tmp_path) == []


def test_a_head_behind_origin_is_refused(tmp_path: Path) -> None:
    origin, work = _origin_with_commits(tmp_path)
    mover = tmp_path / "mover"
    _ = subprocess.run(("git", "clone", str(origin), str(mover)),
                       check=True, capture_output=True)
    _git(mover, "config", "user.name", "mover")
    _git(mover, "config", "user.email", "mover@example.invalid")
    _git(mover, "config", "commit.gpgsign", "false")
    _git(mover, "commit", "--allow-empty", "-m", "origin moved")
    _git(mover, "push", "origin", "main")

    result = _run(tmp_path, work, decisions="0")

    assert result.returncode == 4
    assert _calls(tmp_path) == []


def test_a_missing_local_ci_receipt_is_refused(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="0", local_ci_rc="1")

    assert result.returncode == 4
    assert "decision" not in _calls(tmp_path)
    assert "request" not in _calls(tmp_path)


def test_help_succeeds(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    helped = _run(tmp_path, work, decisions="0", arguments=("--help",))

    assert helped.returncode == 0
    assert "usage: release.sh [--no-deploy]" in helped.stdout


def test_unknown_argument_is_a_usage_error(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="0", arguments=("--bad",))

    assert result.returncode == 2


def test_the_command_ships_executable() -> None:
    assert os.access(_COMMAND, os.X_OK)


def test_latest_release_base_peels_the_newest_tag(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    first = _git(work, "rev-parse", "HEAD~1")
    _git(work, "tag", "-a", "v1.0.0", "-m", "release: v1.0.0", first)
    _git(work, "push", "origin", "v1.0.0")

    result = subprocess.run(
        ("bash", "-c", f'source "{_LIB}"; latest_release_base "{work}"'),
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == first
