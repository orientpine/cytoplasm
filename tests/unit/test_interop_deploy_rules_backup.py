"""인터롭 배포기가 규칙 파일을 바꾸기 전에 이전 바이트를 같은 실행의 rollback 에 남긴다(RCB todo 63).

v1.14.1 에서 `push_file` 이 roster 관문 앞에서 두 계정의 규칙 파일을 바꿨고, 그 실행의
`~/.hermes/interop/rollback/<스탬프>-<pid>/` 는 관문 뒤에서 드롭인·플러그인·가드만 담았다 — 갱신 전
규칙은 어디에도 남지 않았다. 여기서 증명하는 것:
  B1 홈 사본이 새 바이트와 다르면 rollback 에 이전 바이트가 sha256 그대로, 디렉터리 700·파일 600 으로 남는다.
  B2 같으면 규칙 사본을 남기지 않는다.
  B3 roster 가 없어 HELD 로 끝나는 실행도 B1 을 지킨다.
  B4 사본을 만들 수 없으면 어느 계정의 규칙 파일도 바뀌지 않고 rc≠0 이다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from tests.unit.test_interop_deploy import _ACCOUNTS, _REPO, _RULES, Node

_RELEASE_RULES: Final = (_REPO / "configs/external-effect-tools.yaml").read_bytes()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rule_copies(home: Path) -> list[Path]:
    return sorted((home / ".hermes/interop/rollback").glob(f"*/{_RULES}"))


def _assert_old_rules_kept(node: Node, old: dict[str, bytes]) -> None:
    for account in _ACCOUNTS:
        home = node.homes / account
        copies = _rule_copies(home)
        assert len(copies) == 1, (account, copies)
        assert _sha(copies[0].read_bytes()) == _sha(old[account])
        assert copies[0].stat().st_mode & 0o777 == 0o600
        run_dir = copies[0].parents[2]
        assert run_dir.parent.name == "rollback" and run_dir.stat().st_mode & 0o777 == 0o700
        assert (home / _RULES).read_bytes() == _RELEASE_RULES


def test_differing_rules_are_kept_in_the_run_rollback_before_replacement(tmp_path: Path) -> None:
    node = Node(tmp_path)
    old = {a: (node.homes / a / _RULES).read_bytes() for a in _ACCOUNTS}
    result = node.run()
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_old_rules_kept(node, old)
    for account in _ACCOUNTS:
        runs = list((node.homes / account / ".hermes/interop/rollback").iterdir())
        assert len(runs) == 1, "the rules copy shares the run's rollback directory"


def test_identical_rules_leave_no_copy(tmp_path: Path) -> None:
    node = Node(tmp_path)
    for account in _ACCOUNTS:
        _ = (node.homes / account / _RULES).write_bytes(_RELEASE_RULES)
    result = node.run()
    assert result.returncode == 0, result.stdout + result.stderr
    for account in _ACCOUNTS:
        assert _rule_copies(node.homes / account) == []


def test_a_held_run_still_keeps_the_old_rules(tmp_path: Path) -> None:
    node = Node(tmp_path)
    old = {a: (node.homes / a / _RULES).read_bytes() for a in _ACCOUNTS}
    for account in _ACCOUNTS:
        (node.homes / account / ".hermes/roster.yaml").unlink()
    result = node.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INTEROP-DEPLOY-HELD" in result.stdout
    _assert_old_rules_kept(node, old)


def test_when_the_copy_cannot_be_made_no_rule_file_is_replaced(tmp_path: Path) -> None:
    node = Node(tmp_path)
    old = {a: (node.homes / a / _RULES).read_bytes() for a in _ACCOUNTS}
    locked = node.homes / "fixture-peer/.hermes/interop/rollback"
    locked.mkdir(parents=True)
    locked.chmod(0o500)
    try:
        result = node.run()
    finally:
        locked.chmod(0o700)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "INTEROP-DEPLOY-BLOCK" in result.stderr
    for account in _ACCOUNTS:
        assert (node.homes / account / _RULES).read_bytes() == old[account], account
