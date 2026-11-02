"""VA-1 release.sh: preconditions, bounded owner polling, and the signed-tag cut.

The approval producer and the local-CI gate are injected as stubs so the whole
owner-decision flow is driven without Discord or a real receipt store, while the
tag cut runs the REAL ``release_tag_lib.sh`` against a throwaway origin — the tag
is the artifact production consumes, so it must be observed, not stubbed.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
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
  plan) printf '%s\\n' "$*" > "$PLAN_ARGS"; printf '{"version":"stub"}\\n' ;;
  retire) exit 0 ;;
  command)
    cp "$2" "$COMMAND_PLAN"
    (( COMMAND_RC == 0 )) || { printf 'RELEASE-COMMAND-REFUSED: stub refusal\\n' >&2; exit "$COMMAND_RC"; }
    printf '{"message_id":"1500000000000000005"}\\n'
    ;;
  decision)
    n="$(cat "$COUNTER" 2>/dev/null || printf 0)"
    n=$((n + 1))
    printf '%s' "$n" > "$COUNTER"
    # shellcheck disable=SC2086
    set -- $DECISIONS
    (( n > $# )) && n=$#
    decision="${!n}"
    if [[ -n "$RECORD_VERSION" && "$decision" != 2 ]]; then
      printf 'RELEASE-DECISION: live version=%s\n' "$RECORD_VERSION" >&2
    fi
    exit "$decision"
    ;;
  *) exit 97 ;;
esac
"""

#: 형이 릴리스를 시킨 글 — 모든 새 릴리스는 이 링크를 근거로 시작한다(형 2026-11-02 Q447).
_OWNER_COMMAND: Final = "https://discord.com/channels/111/222/1500000000000000005"

_DEPLOY_ALL_STUB: Final = """#!/usr/bin/env bash
set -uo pipefail
printf 'deploy-all' >> "$CALLS"
printf ' %s' "$@" >> "$CALLS"
printf '\\n' >> "$CALLS"
exit "$DEPLOY_ALL_RC"
"""

#: 노드의 root 수렴 도우미 판정. 기본이 injected 인 것이 중요하다 — 주입하지 않으면
#: release.sh 는 진짜 노드로 ssh 하므로, 이 스텁이 없는 테스트는 실노드를 건드린다.
_HELPER_PROBE_STUB: Final = """#!/usr/bin/env bash
printf 'helper-probe\\n' >> "$CALLS"
case "$HELPER_PROBE_MODE" in
  drift)
    printf '[release-helper] HELPER-DRIFT: origin_snapshot.sh differs from the rendered release source\\n' >&2
    printf '[release-helper] run provision-deploy-converge.sh on the node as root\\n' >&2
    exit 1 ;;
  unknown)
    printf '[release-helper] HELPER-DRIFT-UNKNOWN: unreadable path=/usr/local/libexec/x\\n' >&2
    exit 1 ;;
  unreachable)
    printf 'ssh: connect to host node port 22: Network is unreachable\\n' >&2
    exit 255 ;;
  *)
    printf '[release-helper] HELPER-DRIFT-PASS: privileged helpers and units match release sources\\n' >&2
    exit 0 ;;
esac
"""


#: GitHub Release 표면의 가짜. 릴리스마다 $GH_STATE/<tag>.json 에 본문·자산(파일 이름과 내용)을
#: 남기고, release view 는 그 파일을 되읽는다 — 게시 뒤 read-back 이 실제로 도는지 보려면
#: 상태가 있어야 한다. GH_CREATE_RC 로 게시 실패를 만든다.
_GH_STUB: Final = """#!/usr/bin/env bash
set -uo pipefail
printf 'gh %s\\n' "$*" >> "$CALLS"
[[ "$1" == release ]] || exit 98
verb="$2"; tag="$3"; shift 3
state="$GH_STATE/$tag.json"
case "$verb" in
  view) [[ -f "$state" ]] || exit 1; cat "$state" ;;
  create)
    (( GH_CREATE_RC == 0 )) || exit "$GH_CREATE_RC"
    asset="$1"; notes=""; title=""; latest=""
    while (( $# )); do
      case "$1" in
        --notes-file) notes="$2"; shift ;;
        --title) title="$2"; shift ;;
        --latest=*) latest="${1#--latest=}" ;;
      esac
      shift
    done
    python3 -c '
import json, pathlib, sys
asset, notes, title, latest, out = sys.argv[1:]
pathlib.Path(out).write_text(json.dumps({"title": title, "latest": latest,
    "body": pathlib.Path(notes).read_text(encoding="utf-8"),
    "assets": [{"name": pathlib.Path(asset).name,
                "content": pathlib.Path(asset).read_text(encoding="utf-8")}]}), encoding="utf-8")
' "$asset" "$notes" "$title" "$latest" "$state"
    ;;
  *) exit 97 ;;
esac
"""

#: 모든 릴리스는 노트 초안을 갖는다 — 없으면 release.sh 가 승인 전에 멈춘다.
_NOTE: Final = "# 메일 다이제스트 형식 개편\n\n## 바뀐 것\n- 다이제스트가 메일 한 통에 메시지 하나를 쓴다.\n"


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
    local_ci_rc: str = "0",
    deploy_all_rc: str = "0",
    arguments: tuple[str, ...] = (),
    owner_command: str | None = _OWNER_COMMAND,
    command_rc: str = "0",
    inbox: Path | None = None,
    record_version: str = "",
    helper_probe: str = "pass",
    allow_helper_drift: str = "0",
    note: str | None = _NOTE,
    gh_create_rc: str = "0",
    wrapper_plan: str = "true",
    rag_plan: str = "true",
    rehearsal: str | None = "ok",
) -> subprocess.CompletedProcess[str]:
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
    helper_probe_stub = tmp_path / "helper-probe-stub"
    _ = helper_probe_stub.write_text(_HELPER_PROBE_STUB, encoding="utf-8")
    helper_probe_stub.chmod(0o755)
    gh = tmp_path / "gh-stub"
    _ = gh.write_text(_GH_STUB, encoding="utf-8")
    gh.chmod(0o755)
    gh_state = tmp_path / "gh-state"
    gh_state.mkdir(exist_ok=True)
    if rehearsal is not None:
        # The pre-card gate (lead rule 2026-10-19) needs a node rehearsal of this HEAD.
        sha = _git(work, "rev-parse", "HEAD").strip()
        stamp = int(time.time()) - (86400 if rehearsal == "stale" else 0)
        line = f"REHEARSAL-OK head={sha[:12]} actions=0 steps=plan" if rehearsal != "refused" \
            else "REHEARSAL-REFUSED step=litellm rc=4"
        record = tmp_path / ".hermes/deploy-all/rehearsal" / sha
        record.parent.mkdir(parents=True, exist_ok=True)
        _ = record.write_text(f"{stamp} {sha}\n{line}\n", encoding="utf-8")
    if note is not None:
        _ = (tmp_path / "note.md").write_text(note, encoding="utf-8")
        arguments = (*arguments, "--notes-file", str(tmp_path / "note.md"))
    if owner_command is not None:
        arguments = (*arguments, "--owner-command", owner_command)
    if inbox is not None:
        arguments = (*arguments, "--owner-inbox", str(inbox))
    env = {
        **os.environ,
        "RELEASE_REPO_ROOT": str(work),
        "RELEASE_APPROVAL_CMD": f"bash {approval}",
        "RELEASE_LOCAL_CI": str(local_ci),
        "RELEASE_DEPLOY_ALL": str(deploy_all),
        "RELEASE_HELPER_PROBE_CMD": f"bash {helper_probe_stub}",
        "RELEASE_WRAPPER_PLAN_CMD": wrapper_plan,
        "RELEASE_RAG_MCP_PLAN_CMD": rag_plan,
        "PLAN_ARGS": str(tmp_path / "plan-args"),
        "HELPER_PROBE_MODE": helper_probe,
        "RELEASE_ALLOW_HELPER_DRIFT": allow_helper_drift,
        "UPDATE_TRUST_SIGNING_KEY": str(_signing_key(tmp_path)),
        "CALLS": str(tmp_path / "calls.log"),
        "COUNTER": str(tmp_path / "counter"),
        "DECISIONS": decisions,
        "DEPLOY_ALL_RC": deploy_all_rc,
        "COMMAND_PLAN": str(tmp_path / "command-plan.json"),
        "COMMAND_RC": command_rc,
        "RECORD_VERSION": record_version,
        "RELEASE_GH": str(gh),
        "RELEASE_NOTE_REPO": "example/repo",
        "RELEASE_NOTES_DIR": str(tmp_path / "release-notes"),
        "GH_STATE": str(gh_state),
        "GH_CREATE_RC": gh_create_rc,
        "HOME": str(tmp_path),
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

    result = _run(tmp_path, work, decisions="2 0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)
    approval_calls = [c for c in _calls(tmp_path) if c in {"decision", "plan", "command", "request"}]
    assert approval_calls == ["decision", "plan", "command", "decision"]
    plan = json.loads((tmp_path / "command-plan.json").read_text(encoding="utf-8"))
    assert plan["owner_command_url"] == _OWNER_COMMAND
    assert "owner_command_transcript" not in plan
    # The tag cut no longer ends with a hint to run deploy_all by hand — release.sh
    # runs it itself and reports that completion.
    assert "receipt written by deploy_all" in result.stderr


def test_absent_dori_switch_does_not_add_a_plan_argument(tmp_path: Path) -> None:
    # Given
    _origin, work = _origin_with_commits(tmp_path)
    # When
    result = _run(tmp_path, work, decisions="2 0")
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--dori-agent-chat" not in (tmp_path / "plan-args").read_text()
    assert "--hermes-gateways" not in (tmp_path / "plan-args").read_text()


def test_release_host_stop_switch_is_bound_by_the_plan_boundary(tmp_path: Path) -> None:
    # Given
    _origin, work = _origin_with_commits(tmp_path)
    flag = tmp_path / ".hermes/release-flags/hermes-gateways-stopped"
    flag.parent.mkdir(parents=True)
    flag.write_text("stopped")
    # When
    result = _run(tmp_path, work, decisions="2 0")
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--hermes-gateways stopped" in (tmp_path / "plan-args").read_text()


def test_invalid_gateway_stop_switch_refuses_before_approval(tmp_path: Path) -> None:
    # Given
    _origin, work = _origin_with_commits(tmp_path)
    flag = tmp_path / ".hermes/release-flags/hermes-gateways-stopped"
    flag.parent.mkdir(parents=True)
    flag.write_text("stopped\n")
    # When
    result = _run(tmp_path, work, decisions="2 0")
    # Then
    assert result.returncode == 4
    assert "GATEWAY-STOP-REFUSED" in result.stderr
    assert not (tmp_path / "plan-args").exists()
    assert _origin_tags(work) == ""


def test_release_host_switch_is_passed_to_the_real_plan_boundary(tmp_path: Path) -> None:
    # Given
    _origin, work = _origin_with_commits(tmp_path)
    flag = tmp_path / ".hermes/release-flags/dori-agent-chat"
    flag.parent.mkdir(parents=True)
    flag.write_text("555")
    # When
    result = _run(tmp_path, work, decisions="2 0")
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--dori-agent-chat 555" in (tmp_path / "plan-args").read_text()


def test_invalid_release_host_switch_blocks_before_any_approval(tmp_path: Path) -> None:
    # Given
    _origin, work = _origin_with_commits(tmp_path)
    flag = tmp_path / ".hermes/release-flags/dori-agent-chat"
    flag.parent.mkdir(parents=True)
    flag.write_text("not-a-bot")
    # When
    result = _run(tmp_path, work, decisions="2 0")
    # Then
    assert result.returncode == 4
    assert "DORI-SWITCH-REFUSED" in result.stderr
    assert not (tmp_path / "plan-args").exists()
    assert _origin_tags(work) == ""


def test_an_approved_record_uses_its_version_instead_of_the_requested_bump(
    tmp_path: Path,
) -> None:
    """A live approval owns the tag name, so the completer cannot cut a patch sibling."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run(
        tmp_path,
        work,
        decisions="0",
        arguments=("--no-deploy", "--bump", "patch"),
        record_version="v9.9.0",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{head}\trefs/tags/v9.9.0^{{}}" in _origin_tags(work)


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


def test_a_new_release_without_an_owner_command_is_refused_before_any_plan(
    tmp_path: Path,
) -> None:
    """형 명령 없이는 릴리스하지 않는다 — 카드도 레코드도 태그도 없다."""
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2", owner_command=None)

    assert result.returncode == 4
    assert "RELEASE-NO-OWNER-COMMAND" in result.stderr
    assert [c for c in _calls(tmp_path) if c in {"plan", "command", "request"}] == []
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_refused_owner_command_cuts_no_tag(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2 0", command_rc="4")

    assert result.returncode == 4
    assert "RELEASE-COMMAND-REFUSED" in result.stderr
    assert "refs/tags/v" not in _origin_tags(work)


def test_an_unconfirmed_owner_command_record_cuts_no_tag(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2 7")

    assert result.returncode == 1
    assert "RELEASE-COMMAND-UNVERIFIED" in result.stderr
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_malformed_owner_command_link_is_refused_before_the_node(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2 0", owner_command="릴리스 내")

    assert result.returncode == 4
    assert "command" not in _calls(tmp_path)
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_voice_command_carries_the_receiver_transcript_to_the_node(tmp_path: Path) -> None:
    """형은 음성으로 시킨다 — 글 본문이 비어 있으니 수신기 전사를 계획에 실어 보낸다."""
    _origin, work = _origin_with_commits(tmp_path)
    inbox = tmp_path / "inbox.jsonl"
    _ = inbox.write_text(
        json.dumps({"id": "1", "transcript": "다른 글"}) + "\n"
        + json.dumps({"id": "1500000000000000005", "content": "",
                      "transcript": "릴리즈하고 배포까지 하는 걸로 하자."}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, work, decisions="2 0", inbox=inbox)

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads((tmp_path / "command-plan.json").read_text(encoding="utf-8"))
    assert plan["owner_command_transcript"] == "릴리즈하고 배포까지 하는 걸로 하자."


def test_an_already_approved_request_resumes_straight_to_the_tag(tmp_path: Path) -> None:
    """세션이 죽어도 재실행이 곧 재개다 — 요청을 다시 게시하지 않는다."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    result = _run(tmp_path, work, decisions="0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)
    assert "command" not in _calls(tmp_path)


def test_a_rerun_after_the_tag_was_cut_resumes_the_deployment_under_the_same_version(
    tmp_path: Path,
) -> None:
    """2026-09-03 완결기 attempt 2 의 모양 — 태그는 잘렸고 deploy_all 이 실패한 뒤의 재실행은
    HEAD 에 이미 붙은 태그를 그대로 쓴다. 다음 버전을 새로 계산하면 이름 불일치 검사가
    자기 릴리스(v1.1.1 vs 요청 v1.1.2)를 거부해 "재실행이 재개다" 가 깨진다."""
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")
    first = _run(tmp_path, work, decisions="0", deploy_all_rc="1")
    assert first.returncode == 10, first.stdout + first.stderr

    second = _run(tmp_path, work, decisions="0")

    assert second.returncode == 0, second.stdout + second.stderr
    tags = _origin_tags(work)
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in tags
    assert "refs/tags/v1.0.1" not in tags
    assert _calls(tmp_path).count("deploy-all") == 2


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
    assert "command" not in _calls(tmp_path)


def test_help_succeeds(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    helped = _run(tmp_path, work, decisions="0", arguments=("--help",))

    assert helped.returncode == 0
    assert "usage: release.sh --owner-command <discord message link>" in helped.stdout


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


def test_a_drifted_node_helper_is_refused_before_any_approval_traffic(tmp_path: Path) -> None:
    """승인을 쓰기 전에 멈춘다 — 2026-09-22 에는 태그까지 자른 뒤에야 노드가 거부했다."""
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="0", helper_probe="drift")

    assert result.returncode == 4
    assert "RELEASE-BLOCK" in result.stderr
    assert "provision-deploy-converge.sh" in result.stderr
    # 카드도 태그도 없다: 승인 표면은 한 번도 불리지 않았다.
    assert [call for call in _calls(tmp_path) if call in {"plan", "command", "decision"}] == []
    assert "refs/tags/" not in _origin_tags(work)


def test_an_unjudgeable_node_helper_warns_and_still_releases(tmp_path: Path) -> None:
    """판정 불가(노드 불통·권한)는 위반이 아니다 — 여기서 막으면 릴리스가 노드 가용성에 묶인다."""
    for mode in ("unknown", "unreachable"):
        case = tmp_path / mode
        case.mkdir()
        _origin, work = _origin_with_commits(case)

        result = _run(case, work, decisions="2 0", helper_probe=mode)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "HELPER-DRIFT-UNKNOWN" in result.stderr
        assert "refs/tags/v1.0.0^{}" in _origin_tags(work)


def test_helper_drift_can_be_waived_for_a_deliberate_release(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2 0", helper_probe="drift",
                  allow_helper_drift="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE_ALLOW_HELPER_DRIFT=1" in result.stderr
    assert "refs/tags/v1.0.0^{}" in _origin_tags(work)


# ── 릴리스 = 태그 + Release 노트 (2026-10-09) ──────────────────────────────────────


def _published(tmp_path: Path, tag: str) -> dict[str, object]:
    import json

    path = tmp_path / "gh-state" / f"{tag}.json"
    assert path.exists(), f"no GitHub Release for {tag}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_an_approved_release_publishes_its_note_with_the_tag(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="0", arguments=("--no-deploy",))

    assert result.returncode == 0, result.stdout + result.stderr
    release = _published(tmp_path, "v1.0.0")
    assert release["title"] == "v1.0.0 — 메일 다이제스트 형식 개편"
    assert release["latest"] == "true"
    body = str(release["body"])
    assert "다이제스트가 메일 한 통에 메시지 하나를 쓴다" in body
    assert "## 포함된 PR" in body
    assert "지문: `SHA256:" in body
    assets = release["assets"]
    assert isinstance(assets, list) and len(assets) == 1
    # 서명키 경로가 개인키여도 올라가는 것은 유도한 공개키다.
    assert assets[0]["name"] == "update-trust.pub"
    assert str(assets[0]["content"]).startswith("ssh-ed25519 ")
    gh_calls = [line for line in _call_lines(tmp_path) if line.startswith("gh release create")]
    assert len(gh_calls) == 1


def test_a_release_without_a_note_draft_is_refused_before_any_approval_traffic(
    tmp_path: Path,
) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2 0", note=None)

    assert result.returncode == 4, result.stdout + result.stderr
    assert "no release note draft" in result.stderr
    assert "command" not in _calls(tmp_path)
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_note_draft_without_a_title_line_is_refused(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="0", note="본문만 있고 제목 줄이 없다\n")

    assert result.returncode == 4, result.stdout + result.stderr
    assert "RELEASE-NOTE-FAIL" in result.stderr
    assert "refs/tags/v" not in _origin_tags(work)


def test_a_failed_note_fails_the_release_and_a_rerun_publishes_it(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    head = _git(work, "rev-parse", "HEAD")

    first = _run(tmp_path, work, decisions="0", gh_create_rc="1")

    assert first.returncode == 11, first.stdout + first.stderr
    assert "RELEASE-NOTE-FAIL" in first.stderr
    assert f"{head}\trefs/tags/v1.0.0^{{}}" in _origin_tags(work)
    assert "deploy-all" not in _calls(tmp_path)

    second = _run(tmp_path, work, decisions="0", note=None)

    assert second.returncode == 0, second.stdout + second.stderr
    assert "## 포함된 PR" in str(_published(tmp_path, "v1.0.0")["body"])


def test_a_previous_tag_without_a_release_note_blocks_the_next_release(
    tmp_path: Path,
) -> None:
    """v1.16.0·v1.17.0 의 모양 — 노트 없이 남은 태그는 다음 릴리스가 먼저 잡는다."""
    _origin, work = _origin_with_commits(tmp_path)
    _git(work, "tag", "-a", "v1.0.0", "-m", "release: v1.0.0", "HEAD~1")
    _git(work, "push", "origin", "v1.0.0")

    result = _run(tmp_path, work, decisions="2 0")

    assert result.returncode == 4, result.stdout + result.stderr
    assert "RELEASE-NOTE-MISSING: v1.0.0" in result.stderr
    assert "automation/release-note.sh v1.0.0" in result.stderr
    assert "command" not in _calls(tmp_path)


def test_the_wrapper_plan_is_bound_into_the_release_plan(tmp_path: Path) -> None:
    """허용 목록 지문이 다른 노드는 같은 승인 계획의 표면이 된다 — 카드 한 줄과 ✅ 하나."""
    _origin, work = _origin_with_commits(tmp_path)
    plan = tmp_path / "wrapper-plan"
    _ = plan.write_text(f"printf 'node-a={'1' * 64}\\n'\n", encoding="utf-8")

    result = _run(tmp_path, work, decisions="2 0", wrapper_plan=f"bash {plan}")

    assert result.returncode == 0, result.stderr
    args = (tmp_path / "plan-args").read_text(encoding="utf-8")
    assert f"--healthcheck-wrapper node-a={'1' * 64}" in args


def test_an_unknown_wrapper_plan_does_not_block_the_release(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)

    result = _run(tmp_path, work, decisions="2 0", wrapper_plan="false")

    assert result.returncode == 0, result.stderr
    assert "WRAPPER-PLAN-UNKNOWN" in result.stderr
    assert "--healthcheck-wrapper" not in (tmp_path / "plan-args").read_text(encoding="utf-8")
