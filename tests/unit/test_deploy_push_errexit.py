r"""`push_file` 은 호출자의 `set -e` 와 무관하게 같은 진단·같은 종료 코드를 낸다.

배포기는 헬퍼를 `set -euo pipefail` 아래에서 source 한다. 수정 전에는 쓰기 단계의 원격
호출이 실패(ssh 255)하는 순간 호출자의 errexit 가 셸을 끝내 `DEPLOY-BLOCK` 줄도 헬퍼의
코드(5)도 없이 끝났다(RCB todo 28 수동 QA, todo 59). 트리 교체 헬퍼의 같은 결함은 todo 45 가
고쳤다 — `tests/unit/test_deploy_tree_swap.py` 의 errexit 시험과 같은 방식이다.

todo 8 의 `test_deploy_push_verify.py` 는 FS3 고정 대상이 아니지만, 같은 헬퍼를 건드리는
병렬 레인과 충돌하지 않도록 이 시험은 새 파일에 둔다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "automation" / "deploy_push.sh"
_OLD = b"old version\n"
_NEW = b"print('v2')\n"
_ERREXIT = "set -euo pipefail; "

# 가짜 노드: n 번째 run_agent 호출을 CUT 으로 끊는다. skipN = 원격이 아예 돌지 않고 255,
# cutN = 원격은 끝까지 돌았지만 연결이 255 를 보고.
_STUB = r"""
run_agent() {
  local n rc=0
  n=$(( $(cat "$CALLS") + 1 ))
  echo "$n" > "$CALLS"
  if [[ "$CUT" == "skip$n" ]]; then cat > /dev/null; return 255; fi
  HOME="$NODE_HOME" bash -c "$1" || rc=$?
  if [[ "$CUT" == "cut$n" ]]; then return 255; fi
  return "$rc"
}
"""


def _run(
    tmp_path: Path, *, errexit: bool, cut: str, source_exists: bool = True
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    case = tmp_path / ("errexit" if errexit else "plain")
    home = case / "node-home"
    dest = home / "scripts" / "payload.py"
    dest.parent.mkdir(parents=True)
    _ = dest.write_bytes(_OLD)
    source = case / "payload.py"
    if source_exists:
        _ = source.write_bytes(_NEW)
    calls = case / "calls"
    _ = calls.write_text("0\n", encoding="utf-8")
    stub = case / "stub.sh"
    _ = stub.write_text(_STUB, encoding="utf-8")
    script = (
        (_ERREXIT if errexit else "")
        + f'source "{stub}"; source "{_HELPER}"; '
        + "trap 'echo \"OPTS-AFTER $- pipefail=$(shopt -qo pipefail && echo on || echo off)\" >&2' EXIT; "
        + f'push_file "{source}" scripts/payload.py; rc=$?; echo AFTER-CALL >&2; exit "$rc"'
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(case),
        "NODE_HOME": str(home),
        "CALLS": str(calls),
        "CUT": cut,
    }
    result = subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False, env=env
    )
    return result, dest, calls


def _temps(dest: Path) -> list[str]:
    return sorted(p.name for p in dest.parent.iterdir() if ".deploy-tmp." in p.name)


def _outcome(
    tmp_path: Path, cut: str, **kwargs: bool
) -> list[tuple[int, bytes, list[str], bool]]:
    outcomes = []
    for errexit in (True, False):
        result, dest, _calls = _run(tmp_path, errexit=errexit, cut=cut, **kwargs)
        if errexit:
            assert "OPTS-AFTER " in result.stderr, result.stderr
            opts = result.stderr.split("OPTS-AFTER ", 1)[1].split()
            assert "e" in opts[0] and "u" in opts[0] and opts[1] == "pipefail=on", opts
        outcomes.append(
            (
                result.returncode,
                dest.read_bytes(),
                _temps(dest),
                "DEPLOY-BLOCK" in result.stderr,
            )
        )
    return outcomes


def test_a_write_step_that_never_reached_the_node_returns_the_helpers_code(
    tmp_path: Path,
) -> None:
    outcomes = _outcome(tmp_path, "skip1")

    assert outcomes[0] == (5, _OLD, [], True), outcomes
    assert outcomes[0] == outcomes[1]


def test_a_dropped_read_back_returns_the_helpers_code(tmp_path: Path) -> None:
    # The write step landed (rename done) before the read-back call was cut, so the node
    # holds the whole new file; the helper cannot verify it and must say so with its code.
    outcomes = _outcome(tmp_path, "skip2")

    assert outcomes[0] == (5, _NEW, [], True), outcomes
    assert outcomes[0] == outcomes[1]


def test_an_unreadable_source_returns_the_helpers_code_without_contacting_the_node(
    tmp_path: Path,
) -> None:
    outcomes = _outcome(tmp_path, "none", source_exists=False)

    assert outcomes[0] == (5, _OLD, [], True), outcomes
    assert outcomes[0] == outcomes[1]
    for errexit in (True, False):
        _result, _dest, calls = _run(
            tmp_path / "again", errexit=errexit, cut="none", source_exists=False
        )
        assert calls.read_text(encoding="utf-8") == "0\n"


def test_a_write_step_that_reported_failure_after_landing_is_still_a_failure(
    tmp_path: Path,
) -> None:
    # The remote finished the write, then the connection reported 255. Errexit deployers
    # always stopped here; the helper keeps that fail-closed outcome but names it.
    outcomes = _outcome(tmp_path, "cut1")

    assert outcomes[0] == (5, _NEW, [], True), outcomes
    assert outcomes[0] == outcomes[1]


def test_a_landed_file_is_a_success_and_the_callers_options_survive(
    tmp_path: Path,
) -> None:
    outcomes = _outcome(tmp_path, "none")

    assert outcomes[0] == (0, _NEW, [], False), outcomes
    assert outcomes[0] == outcomes[1]
    result, _dest, _calls = _run(tmp_path / "again", errexit=True, cut="none")
    assert "AFTER-CALL" in result.stderr
