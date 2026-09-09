"""생성기를 운영자로 강등해 부르면 ops 전용 체크아웃을 읽지 못한다.

컨테이너 하네스는 `operator_account='root'`(tests/e2e/install/systemd_container/run.sh)라
무엇이든 읽혔고, 이 자산은 그 가정 위에서 v1.6.1 로 나갔다. 실제 노드의 운영자는
`autophagy` 그룹에 없고 배포 체크아웃은 `ops:autophagy 2750` 이라, `runuser -u <operator>`
로 생성기를 부르면 `Permission denied`(rc=126)로 죽는다 — 2026-09-08 실호스트 실측.
같은 argv 를 판정(`inspect_probe`)도 쓰는데 `runuser` 는 root 전용이므로, ops 로 도는
계획 단계에서는 **언제나** 실패해 이미 수렴한 노드에도 같은 액션이 매번 다시 계획됐다.

여기서 고정하는 불변식은 하나다: **설치기는 이 액션에서 권한을 내려놓지 않는다.** 대신
운영자의 것은 `HOME` 과 래퍼 경로로 넘기고, 산출물의 소유·모드는 적용 쪽이 맞춘다.
자산의 정상 동작은 test_install_healthcheck_probe_asset.py 가 계속 다루고, 이 파일은 그
한 가지만 본다 — 그래서 갈라 두었다.
"""
from __future__ import annotations

import base64
import os
import pwd
import stat
import subprocess
from pathlib import Path

from automation.install import plan as planning
from automation.install.healthcheck_probe_asset import (
    generator_command,
    provision_probe,
    wrapper_path,
)

_DIGEST = "a" * 64


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm + (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} privilege-test"


def _action(tmp_path: Path) -> planning.ProvisionHealthcheckProbe:
    account = pwd.getpwuid(os.getuid()).pw_name
    return planning.ProvisionHealthcheckProbe(
        operator_account=account,
        operator_home=tmp_path / "operator",
        ops_account=account,
        ops_home=tmp_path / "ops",
        private_path=tmp_path / "ops" / ".ssh" / "autophagy-healthcheck",
        source_dir=tmp_path / "checkout",
        node_name="qa-node",
    )


def _place_key_pair(action: planning.ProvisionHealthcheckProbe) -> None:
    action.private_path.parent.mkdir(parents=True, exist_ok=True)
    _ = action.private_path.write_text("PRIVATE-KEY-PLACEHOLDER\n", encoding="utf-8")
    _ = action.private_path.with_suffix(".pub").write_text(f"{_public_key()}\n", encoding="utf-8")


class _Generator:
    """설치기가 부르는 유일한 명령을 대신한다: 래퍼를 남기고 완료 표식을 낸다."""

    def __init__(self, action: planning.ProvisionHealthcheckProbe, *, mode: int) -> None:
        self._action = action
        self._mode = mode
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        target = wrapper_path(self._action)
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(f"#!/usr/bin/env bash\n# wrapper-inputs: {_DIGEST}\n", encoding="utf-8")
        os.chmod(target, self._mode)
        return subprocess.CompletedProcess(command, 0, f"WRAPPER-INSTALLED {target}\n", "")


def test_the_generator_keeps_the_privilege_it_needs_to_read_an_ops_only_checkout(
    tmp_path: Path,
) -> None:
    # Given: 배포 체크아웃은 ops:autophagy 2750 이고 운영자는 그 그룹에 없다.
    command = generator_command(_action(tmp_path), "--inputs-digest")

    # Then: 운영자로 강등하지 않는다. 강등하면 실호스트에서 rc=126 이고,
    # 판정 경로는 `runuser` 가 root 전용이라 ops 로 돌 때 항상 실패한다.
    assert "runuser" not in command, command


def test_the_operator_identity_still_rides_along_as_environment(tmp_path: Path) -> None:
    action = _action(tmp_path)

    command = generator_command(action, "--install")

    # 권한은 내려놓지 않지만 산출물의 주인은 여전히 운영자다.
    assert f"HOME={action.operator_home}" in command
    assert f"HEALTHCHECK_WRAPPER_PATH={wrapper_path(action)}" in command
    assert f"HEALTHCHECK_RELEASE_SOURCE_ROOT={action.source_dir}" in command


def test_the_installed_wrapper_is_handed_to_the_operator(tmp_path: Path) -> None:
    # Given: root 가 만든 래퍼는 root 소유 0600 으로 남을 수 있다.
    action = _action(tmp_path)
    _place_key_pair(action)
    generator = _Generator(action, mode=0o600)

    provision_probe(action, generator)

    # Then: 판정(inspect_probe)이 요구하는 0755·운영자 소유로 맞춰진다 —
    # 맞추지 않으면 적용은 성공했는데 다음 계획이 같은 액션을 다시 올린다.
    metadata = wrapper_path(action).lstat()
    assert stat.S_IMODE(metadata.st_mode) == 0o755
    assert metadata.st_uid == os.getuid()
