"""healthcheck 가 스킬 마운트 드리프트를 실제로 집어내도록 배선돼 있는지.

판정 로직은 `test_skill_mount_drift.py`가 고정한다. 여기서 지키는 것은 **배선**이다 —
프로브가 등록돼 있고, `run_check`가 그 타입을 올바른 프로브로 보내며, 실패했을 때
수리 안내가 "패치하지 말고 배포하라"를 말하는지. 셸이므로 셸로 검증한다.

배선이 끊기면 조용히 통과한다는 점이 이 테스트가 필요한 이유다 — 이 드리프트는 원래
아무 신호도 내지 않아서, 프로브가 안 도는 것과 드리프트가 없는 것이 구별되지 않는다.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
_PROBE_LIB = _REPO / "automation" / "skill_mount_probe.sh"
_MAIN_INVOCATION = 'main "$@"'
_CHECK_TYPE = "skill_mounts_current"


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("bash", "-c", script), capture_output=True, text=True, cwd=_REPO)


def _sourceable_healthcheck(tmp_path: Path) -> Path:
    """main 호출만 뗀 healthcheck — source 해도 노드로 나가지 않는다."""
    body = _HEALTHCHECK.read_text(encoding="utf-8").replace(_MAIN_INVOCATION, ":")
    body = body.replace(
        'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"',
        f'REPO_ROOT="{_REPO}"',
    )
    copy = tmp_path / "healthcheck_sourceable.sh"
    _ = copy.write_text(body, encoding="utf-8")
    # healthcheck sources its libraries from its own directory — the copy needs them
    # beside it. Copy all of them so adding a library never breaks this test.
    for lib in sorted((_REPO / "automation").glob("*.sh")):
        _ = (tmp_path / lib.name).write_bytes(lib.read_bytes())
    return copy


def _release(tmp_path: Path, *skills: str) -> Path:
    """automation/ 과 skills/ 를 갖춘 최소 릴리스 트리."""
    runtime = tmp_path / "runtime"
    (runtime / "automation").mkdir(parents=True)
    for module in ("skill_mount_drift.py", "skill_review.py"):
        _ = (runtime / "automation" / module).write_bytes(
            (_REPO / "automation" / module).read_bytes()
        )
    (runtime / "automation" / "__init__.py").touch()
    for name in skills:
        directory = runtime / "skills" / name
        directory.mkdir(parents=True)
        _ = (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return runtime


def _digest(runtime: Path, skill: str) -> str:
    result = _run(
        "python3 -c 'import sys;sys.path.insert(0,\".\");"
        "from pathlib import Path;from automation.skill_review import skill_digest;"
        f"print(skill_digest(Path(\"{runtime / 'skills' / skill}\")))'"
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _mount(live: Path, skill: str, digest: str) -> None:
    target = live.parent / "releases" / skill / digest
    target.mkdir(parents=True, exist_ok=True)
    live.mkdir(parents=True, exist_ok=True)
    (live / skill).symlink_to(target)


def test_the_probe_is_registered_as_a_live_check() -> None:
    # 프로브가 있어도 LIVE_CHECKS 에 없으면 영원히 돌지 않는다
    body = _HEALTHCHECK.read_text(encoding="utf-8")
    registration = [
        line for line in body.splitlines() if f"|{_CHECK_TYPE}|" in line and line.startswith('  "')
    ]
    assert len(registration) == 1, registration
    assert "$NODE_SKILL_STORE/live" in registration[0]


def test_run_check_dispatches_the_type_to_the_probe(tmp_path: Path) -> None:
    # Given: 마운트가 릴리스와 일치하는 트리
    runtime = _release(tmp_path, "mail")
    live = tmp_path / "live"
    _mount(live, "mail", _digest(runtime, "mail"))
    sourceable = _sourceable_healthcheck(tmp_path)
    # When: run_check 를 그 타입으로 부른다 (배선을 통과해야만 도달한다)
    result = _run(
        f'source "{sourceable}"; '
        f'AUTOPHAGY_RUNTIME_ROOT="{runtime}" run_check "x|{_CHECK_TYPE}|node|ops|{live}"'
    )
    # Then: 일치하므로 통과
    assert result.returncode == 0, result.stderr


def test_a_stale_mount_makes_the_check_fail(tmp_path: Path) -> None:
    # Given: 마운트가 릴리스와 다른 내용을 가리킨다 (2026-08-01 실측 상황)
    runtime = _release(tmp_path, "mail")
    live = tmp_path / "live"
    _mount(live, "mail", "0" * 64)
    sourceable = _sourceable_healthcheck(tmp_path)
    # When
    result = _run(
        f'source "{sourceable}"; '
        f'AUTOPHAGY_RUNTIME_ROOT="{runtime}" run_check "x|{_CHECK_TYPE}|node|ops|{live}"'
    )
    # Then: 실패하고, 어느 스킬인지 남긴다
    assert result.returncode != 0
    assert "SKILL-STALE" in result.stdout + result.stderr
    assert "mail" in result.stdout + result.stderr


def test_an_unreadable_release_fails_instead_of_passing_quietly(tmp_path: Path) -> None:
    # Given: 릴리스 트리가 없다. 이 드리프트는 원래 조용하므로, 판정 불가를 통과로
    # 처리하면 프로브가 있으나 마나가 된다.
    sourceable = _sourceable_healthcheck(tmp_path)
    live = tmp_path / "live"
    live.mkdir()
    # When
    result = _run(
        f'source "{sourceable}"; '
        f'AUTOPHAGY_RUNTIME_ROOT="{tmp_path / "absent"}" '
        f'run_check "x|{_CHECK_TYPE}|node|ops|{live}"'
    )
    # Then
    assert result.returncode != 0


@pytest.mark.parametrize(
    "phrase",
    ["패치하지 말 것", "deploy-skill.sh", "readlink"],
)
def test_repair_guidance_sends_the_operator_to_deploy_not_to_a_patch(
    tmp_path: Path, phrase: str
) -> None:
    # 수리 자동화의 반사적 조치는 '코드를 고치는 것'이다. 여기서는 코드가 멀쩡하므로
    # 그 반사가 곧 헛수고가 된다 — 안내가 먼저 그것을 막아야 한다.
    sourceable = _sourceable_healthcheck(tmp_path)
    result = _run(f'source "{sourceable}"; repair_guidance "{_CHECK_TYPE}"')
    assert result.returncode == 0, result.stderr
    assert phrase in result.stdout


def test_the_detector_never_writes() -> None:
    """탐지기가 스스로 고치려 든다면 소유자 승인 게이트를 우회하게 된다.

    문자열 검색은 쓸 수 없다 — 안내문이 `deploy-skill.sh`를 정당하게 언급하기
    때문이다. 실제로 금지해야 하는 것은 '쓰기 호출'이므로 AST로 본다."""
    source = (_REPO / "automation" / "skill_mount_drift.py").read_text(encoding="utf-8")
    writes = {
        "write_text", "write_bytes", "mkdir", "unlink", "rmdir", "chmod",
        "symlink_to", "rename", "replace", "touch", "system", "run", "Popen",
    }
    called = {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (called & writes), called & writes


def test_the_shell_library_runs_nothing_but_python_and_printf() -> None:
    # 셀 쪽은 AST가 없으니 **명령 위치**만 본다 — 안내문 안의 인용은 제외된다.
    for raw in _PROBE_LIB.read_text(encoding="utf-8").splitlines():
        head = raw.strip().split(" ", maxsplit=1)[0]
        assert head not in {
            "deploy-skill.sh", "systemctl", "rm", "mkdir", "ln", "sudo", "git", "ssh",
        }, raw
