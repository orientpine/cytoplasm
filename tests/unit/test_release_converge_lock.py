"""수렴 락은 root 와 ops 가 **같은 파일**을 열 수 있어야 한다.

`current` 는 두 경로가 뒤집는다: 타이머의 `converge_origin_main.sh`(root)와 배포의
`converge-release-runtime.sh`(ops). 공유 락이 없으면 느린 옛 수렴이 새 수렴 위에
릴리스를 **되감을** 수 있어서, 둘은 반드시 같은 파일을 잡아야 한다.

그 락이 `/tmp` 에 있었고, 그래서 2026-08-01 에 타이머가 조용히 멈췄다:

    /usr/local/libexec/autophagy-converge-origin-main: line 72:
    /tmp/autophagy-release-converge.lock: Permission denied

root 인데 거부됐다. 이 노드는 `fs.protected_regular=2` 이고 `/tmp` 는 sticky·world-writable
이라, **파일 소유자가 다르면 열기가 거부된다 — root 도 예외가 아니다**. 락은 배포 경로가
ops 로 먼저 만들어 두었으므로 root 가 영영 열 수 없었다.

그래서 여기서 고정하는 것은 셋이다: 두 경로가 같은 경로를 말할 것, 그 경로가 sticky
디렉터리 밖일 것, 그리고 만들어진 락이 두 계정 모두에게 열릴 것(그룹 쓰기 + setgid 디렉터리).
"""
from __future__ import annotations

import re
from pathlib import Path

from automation.node_config import default_node_config

_REPO = Path(__file__).resolve().parents[2]
_ROOT_SIDE = _REPO / "automation" / "converge_origin_main.sh"
_OPS_SIDE = _REPO / "automation" / "converge-release-runtime.sh"
_PROVISION = _REPO / "automation" / "provision-deploy-converge.sh"

#: sticky + world-writable 이라 fs.protected_regular 가 교차 소유자 열기를 막는 곳
_STICKY_DIRS = ("/tmp/", "/var/tmp/", "/run/lock/", "/dev/shm/")


def _default(body: str, name: str) -> str:
    """`NAME=...` 의 기본값 — `${VAR:-default}` 형태면 default 를 돌려준다."""
    match = re.search(rf'^(?:readonly )?{name}=("?)(.+?)\1$', body, re.MULTILINE)
    assert match is not None, f"{name}= 을 읽지 못했다"
    value = match.group(2)
    fallback = re.fullmatch(r"\$\{[A-Z_]+:-(.+)\}", value)
    return fallback.group(1) if fallback else value


def _lock_path(script: Path) -> str:
    body = script.read_text(encoding="utf-8")
    lock_dir = _default(body, "LOCK_DIR").replace(
        "$NODE_PRIVATE_ROOT", str(default_node_config().private_root)
    )
    return _default(body, "LOCK").replace("$LOCK_DIR", lock_dir)


def test_both_convergers_name_the_same_lock() -> None:
    # 서로 다른 파일을 잡으면 그것은 락이 아니다 — 릴리스가 되감길 수 있다.
    assert _lock_path(_ROOT_SIDE) == _lock_path(_OPS_SIDE)


def test_the_lock_is_not_in_a_sticky_world_writable_directory() -> None:
    """`/tmp` 계열은 fs.protected_regular 때문에 교차 소유자 공유 락을 담을 수 없다."""
    for script in (_ROOT_SIDE, _OPS_SIDE):
        path = _lock_path(script)
        assert path.startswith("/"), f"{script.name}: 절대경로여야 한다"
        for sticky in _STICKY_DIRS:
            assert not path.startswith(sticky), f"{script.name}: {path} 는 {sticky} 안이다"


def test_each_converger_prepares_a_lock_both_accounts_can_open() -> None:
    """root 가 먼저 만들면 ops 가, ops 가 먼저 만들면 root 가 열 수 있어야 한다.

    root 는 DAC 를 우회하므로 문제는 언제나 ops 쪽이다 — 그래서 락은 그룹 쓰기여야 하고,
    디렉터리는 setgid 라 누가 만들든 그룹이 같아야 한다.
    """
    for script in (_ROOT_SIDE, _OPS_SIDE):
        body = script.read_text(encoding="utf-8")
        assert "umask 007" in body, f"{script.name}: 그룹 쓰기 가능하게 만들지 않는다"
        assert "2770" in body, f"{script.name}: 락 디렉터리를 setgid 로 두지 않는다"
        assert "autophagy" in body, f"{script.name}: 공유 그룹을 지정하지 않는다"


def test_the_provisioner_creates_the_lock_directory() -> None:
    """첫 수렴 전에 디렉터리가 결정적으로 존재해야 스크립트 폴백에 기대지 않는다."""
    body = _PROVISION.read_text(encoding="utf-8")
    assert _lock_path(_ROOT_SIDE).rsplit("/", 1)[0] in body
    assert "2770" in body


def test_the_ops_side_keeps_its_env_seam_and_never_follows_tmpdir() -> None:
    """기존 계약 유지: 호출자 환경을 따라 움직이는 락은 락이 아니다."""
    body = _OPS_SIDE.read_text(encoding="utf-8")
    lock_line = next(line for line in body.splitlines() if line.startswith("LOCK="))
    assert "TMPDIR" not in lock_line
    assert "RELEASE_CONVERGE_LOCK" in lock_line
