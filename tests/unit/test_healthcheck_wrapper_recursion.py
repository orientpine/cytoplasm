"""허용목록 기록 스윕은 자기 자신을 다시 부르면 안 된다 — healthcheck 폭주의 진짜 원인.

`healthcheck_probe_wrapper.sh --inputs-digest` 는 허용목록을 **추론하지 않고 관측한다**:
`capture_on_node` 를 기록기로 갈아끼운 뒤 그 노드의 모든 체크를 한 바퀴 돌린다. 그런데 그
체크 목록 안에는 래퍼 드리프트 프로브 자신이 있고, 그 프로브는 기대 지문을 얻으려고
`bash <generator> --inputs-digest <node>` 를 **새 프로세스로** 실행한다. 그 프로세스가 다시
기록 스윕을 돌리고, 그 안에서 또 같은 프로브가... 끝이 없다(523371d6 이 프로브 명령 해시를
지문에 접으면서 들어왔다).

실측(2026-08-31, 노드): cron 실행 2 개가 ops 프로세스 **436 개**로 불어났고 전부
`bash .../healthcheck_probe_wrapper.sh --inputs-digest <node>` 였다. 한 번의 healthcheck 가
몇 시간씩 걸린 이유, 노드가 memory pressure critical 에 닿은 이유, 그리고 래퍼 프로브의
"기대" 지문이 쓰레기가 되어 영구 FAIL(티켓 t_d2ac107a ~1946 회)이던 이유가 모두 이것이다.

단위 테스트가 이걸 놓친 이유도 기록해 둔다: 워크스테이션에는
`HEALTHCHECK_RELEASE_SOURCE_ROOT` 아래 생성기가 **없어서** 프로브가
`WRAPPER-DRIFT-UNKNOWN` 으로 조기 반환했다. 그래서 여기서는 생성기가 **읽히는** 환경을
일부러 만든다 — 재귀가 실제로 일어나는 조건이다.

hermetic: 원격 명령은 한 줄도 나가지 않는다(기록 스윕은 정의상 ssh 를 부르지 않고, PATH 의
가짜 `ssh` 가 남은 경로를 막는다). 통합 테스트는 새 세션에서 띄워 마감 시간에 프로세스
그룹째 정리하므로, 재귀가 살아 있어도 테스트 러너를 함께 끌고 가지 않는다.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO / "automation" / "healthcheck_probe_wrapper.sh"
_PROBE = _REPO / "automation" / "healthcheck_wrapper_probe.sh"
#: 프로브가 노드에 보내는 유일한 명령. 허용목록 해시의 입력이므로 **바이트 단위로** 고정한다
#: — 기록 모드와 실제 호출이 한 글자라도 달라지면 래퍼가 실제 호출을 거부한다(exit 126).
_PROBE_COMMAND = (
    "sed -n 's/^# wrapper-inputs: //p' \"$HOME/.local/libexec/autophagy-healthcheck-probe\""
)
_FAKE_DIGEST = "a" * 64
#: 재귀가 살아 있으면 이 시간 안에 끝나지 않는다. 정상 경로는 수 초면 끝난다.
_DEADLINE_SECONDS = 60.0

_FAKE_SSH = """#!/usr/bin/env bash
printf 'no remote command may run during a recording sweep\\n' >&2
exit 97
"""


def _harness(tmp_path: Path, *, recording: bool) -> subprocess.CompletedProcess[str]:
    """프로브 하나만 돌린다 — 생성기는 자기가 불렸다는 사실만 남기는 스텁이다."""
    release = tmp_path / "release" / "automation"
    release.mkdir(parents=True)
    marker = tmp_path / "generator-was-called"
    _ = (release / "healthcheck_probe_wrapper.sh").write_text(
        f'#!/usr/bin/env bash\ntouch "$GENERATOR_MARKER"\necho {_FAKE_DIGEST}\n',
        encoding="utf-8",
    )
    captured = tmp_path / "captured-commands.txt"
    env = {
        **os.environ,
        "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(tmp_path / "release"),
        "GENERATOR_MARKER": str(marker),
        "CAPTURED": str(captured),
        "FAKE_DIGEST": _FAKE_DIGEST,
    }
    if recording:
        env["HEALTHCHECK_WRAPPER_RECORDING"] = "1"
    else:
        env.pop("HEALTHCHECK_WRAPPER_RECORDING", None)
    return subprocess.run(
        (
            "bash",
            "-c",
            'set -uo pipefail\n'
            'capture_on_node() { printf "%s\\n" "$2" >> "$CAPTURED"; printf %s "$FAKE_DIGEST"; }\n'
            f'source "{_PROBE}"\n'
            'probe_healthcheck_wrapper_current node ops ignored\n',
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_recording_mode_records_the_command_without_invoking_the_generator(
    tmp_path: Path,
) -> None:
    """기록 스윕 안에서는 생성기를 부르지 않는다 — 그 호출이 곧 재귀다."""
    result = _harness(tmp_path, recording=True)

    assert result.returncode == 0, result.stdout + result.stderr
    # 그래도 명령은 목록에 남아야 한다. 안 남기면 실제 호출이 exit 126 으로 거부된다.
    captured = (tmp_path / "captured-commands.txt").read_text(encoding="utf-8").splitlines()
    assert captured == [_PROBE_COMMAND], result.stdout + result.stderr
    assert not (tmp_path / "generator-was-called").exists(), (
        "기록 모드가 생성기를 다시 불렀다 — 이것이 무한 재귀의 한 단계다"
    )


def test_a_normal_tick_still_asks_the_generator_for_the_expected_digest(
    tmp_path: Path,
) -> None:
    """평시 판정 경로는 그대로다 — 가드가 드리프트 탐지를 꺼버리면 안 된다."""
    result = _harness(tmp_path, recording=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WRAPPER-PASS" in result.stderr
    assert (tmp_path / "generator-was-called").exists(), (
        "평시에는 생성기에서 기대 지문을 받아야 판정이 가능하다"
    )


def _primary_node(env: dict[str, str]) -> str:
    """생성기가 쓸 **바로 그 env** 로 노드 이름을 해석한다.

    노드 신원은 HOME 에 따라 달라진다(격리 HOME 은 예시 이름으로 떨어진다). 다른 env 로
    구한 이름을 넘기면 기록 루프가 한 체크도 못 고르고 빈 손으로 rc 1 이 되어, 재귀와는
    무관한 이유로 테스트가 붉어진다.
    """
    resolved = subprocess.run(
        (
            "bash",
            "-c",
            f'eval "$(python3 "{_REPO}/automation/node_config_sh.py" --print-env)"\n'
            'printf %s "$NODE_PRIMARY_NODE_NAME"\n',
        ),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()
    assert resolved, "노드 이름을 해석하지 못했다"
    return resolved


def _recording_env(tmp_path: Path) -> dict[str, str]:
    """생성기가 **읽히는** 환경 — 재귀가 실제로 일어나는 조건 그대로."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh = fake_bin / "ssh"
    _ = ssh.write_text(_FAKE_SSH, encoding="utf-8")
    ssh.chmod(0o755)
    watcher_manifest = tmp_path / "watcher-manifest.txt"
    _ = watcher_manifest.write_text(
        "agent|automation/healthcheck.sh|.hermes/scripts/x.py|required\n", encoding="utf-8"
    )
    runtime_manifest = tmp_path / "runtime-package-manifest.txt"
    _ = runtime_manifest.write_text(
        "agent|automation/hermes_compat|.hermes/hermes_compat|required\n", encoding="utf-8"
    )
    for name in ("isolated-home", "empty-checkout", "skill-runtime", "skill-live"):
        (tmp_path / name).mkdir()
    return {
        **os.environ,
        "HOME": str(tmp_path / "isolated-home"),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "HEALTHCHECK_SSH_USER": "",
        "HEALTHCHECK_SSH_IDENTITY": "",
        # 여기가 이 테스트의 핵심 조건이다: 생성기가 읽히면 프로브가 그것을 실행한다.
        "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(_REPO),
        "HEALTHCHECK_WATCHER_MANIFEST": str(watcher_manifest),
        "HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST": str(runtime_manifest),
        # 로컬 프로브가 이 워크스테이션의 실제 트리(그리고 origin 네트워크)를 만지지 않게 한다.
        "HEALTHCHECK_OPS_CHECKOUT": str(tmp_path / "empty-checkout"),
        "AUTOPHAGY_RUNTIME_ROOT": str(tmp_path / "skill-runtime"),
        "HEALTHCHECK_SKILL_LIVE_ROOT": str(tmp_path / "skill-live"),
    }


def _inputs_digest(env: dict[str, str], node: str) -> str:
    """마감 시간 안에 지문 하나. 넘기면 프로세스 그룹째 죽이고 실패로 판정한다."""
    process = subprocess.Popen(
        ("bash", str(_GENERATOR), "--inputs-digest", node),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=_DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        _ = process.communicate()
        pytest.fail(
            f"--inputs-digest did not finish within {_DEADLINE_SECONDS:.0f}s — "
            "the recording sweep is re-entering its own wrapper probe"
        )
    assert process.returncode == 0, stdout + stderr
    return stdout.strip()


def test_the_inputs_digest_terminates_and_is_deterministic(tmp_path: Path) -> None:
    """재귀가 없으면 기록 스윕은 끝나고, 같은 입력은 같은 지문을 낸다."""
    env = _recording_env(tmp_path)
    node = _primary_node(env)

    first = _inputs_digest(env, node)
    second = _inputs_digest(env, node)

    assert re.fullmatch(r"[0-9a-f]{64}", first), first
    assert first == second, "같은 입력에서 지문이 흔들리면 드리프트 판정 자체가 무의미하다"
