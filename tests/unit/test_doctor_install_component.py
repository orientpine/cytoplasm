"""doctor 알람이 설치기 경로로도 온다 — 오케스트레이터 없는 단일 노드 설치(cytoplasm)의 알람 경로.

Hermes cron 경로(`automation/doctor/deploy.sh`)는 오케스트레이터가 SSH 로 배포할 때만 생긴다.
프로필로 설치하면 같은 래퍼를 systemd 타이머로 받아야 한다. 두 경로가 한 노드에 겹쳐도 알람은
상태 잠금으로 한 번만 나간다.
"""
from __future__ import annotations

import base64
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import pytest

from automation.doctor import alarm
from automation.doctor.capabilities import Finding
from automation.install.assets import build_inputs
from automation.install.checks import Status
from automation.install.installer import main as installer_main
from automation.install.profiles import PROFILES
from automation.node_config import default_node_config

_REPO: Final = Path(__file__).resolve().parents[2]
_UNITS: Final = (
    "autophagy-doctor-watch-agent.service", "autophagy-doctor-watch-agent.timer",
    "autophagy-doctor-watch-peer.service", "autophagy-doctor-watch-peer.timer",
)


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm + (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} doctor-test"


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_every_profile_installs_the_doctor_alarm(profile: str) -> None:
    inputs = build_inputs(_REPO, default_node_config(), _public_key(), profile=profile)

    names = {spec.path.name for spec in inputs.files}
    assert set(_UNITS) <= names
    assert {"autophagy-doctor-watch-agent.timer", "autophagy-doctor-watch-peer.timer"} <= set(inputs.timers)


def test_the_units_run_each_account_from_the_release_with_its_home_visible() -> None:
    config = default_node_config()
    inputs = build_inputs(_REPO, config, _public_key(), components=("doctor-watch",))
    units = {spec.path.name: spec.content for spec in inputs.files if spec.path.name in _UNITS}

    agent, peer = units["autophagy-doctor-watch-agent.service"], units["autophagy-doctor-watch-peer.service"]
    assert f"User={config.agent_account}" in agent and f"HOME={config.agent_home}" in agent
    assert f"User={config.peer_account}" in peer and f"HOME={config.peer_home}" in peer
    for unit in (agent, peer):
        assert f"{config.release_current}/automation/doctor/cron/doctor_watch.py" in unit
        assert "ProtectHome=no" in unit


def test_without_a_profile_or_the_component_nothing_changes() -> None:
    inputs = build_inputs(_REPO, default_node_config(), _public_key())

    assert not {spec.path.name for spec in inputs.files} & set(_UNITS)


def test_the_real_dry_run_plans_the_doctor_units(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key = tmp_path / "trust.pub"
    _ = key.write_text(f"{_public_key()}\n", encoding="utf-8")

    assert installer_main(("--update-trust-key", str(key), "--dry-run", "--profile", "core")) == 0

    out = capsys.readouterr().out
    assert "file /etc/systemd/system/autophagy-doctor-watch-agent.service" in out
    assert "timer autophagy-doctor-watch-peer.timer" in out


def test_a_held_alarm_lock_keeps_the_second_checker_from_sending(tmp_path: Path) -> None:
    sent: list[str] = []
    lock_path = alarm.state_path(tmp_path).parent / "alarm.lock"
    lock_path.parent.mkdir(parents=True)
    finding = Finding("primary-model", "주 모델", Status.FAIL, "d", "a", ("s",))

    with lock_path.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = alarm.run_alarm("agent", (finding,), home=tmp_path, now=datetime.now(timezone.utc),
                                 send=lambda body, _message: sent.append(body) is None)

    assert result.startswith("DOCTOR-ALARM-BUSY") and sent == []
