"""재조정 실패가 소유자에게 실제로 도달하는가.

2026-08-02 실측이 이 파일의 이유다. 타이머가 15시간·약 450회 돌면서 한 번도 수렴하지
못했는데 아무도 몰랐고, 같은 날 노드 에이전트가 배포 체크아웃에 커밋해 9시간 동안 모든
ff-pull 이 막혔을 때 healthcheck 는 그것을 **52번 FAIL 로 정확히 탐지하고도** 소유자에게
닿지 못했다. 탐지는 여러 겹으로 있는데 도달이 없었다.

`unconfigured_notifier` 는 언제나 False 를 돌려주는 자리표시자였다. 이 파일은 그 자리를
실제 전송으로 채우면서, 그 과정에서 **더 나쁜 실패를 만들지 않도록** 계약을 고정한다.

가장 중요한 계약은 **절대 예외를 던지지 않는다**는 것이다. `reconcile_tick` 은 `deliver`
가 False 를 돌려줄 때만 통지를 상태에 큐잉해 다음 틱에 재시도한다. 전송 실패가 예외로
빠져나가면 그 복구가 통째로 무력화되고, 수렴 자체도 함께 죽는다 — 알림을 붙이려다
프로덕션을 멈추는 셈이다.

자격증명은 새로 만들지 않는다. `/etc/autophagy/repair-approval.env` 가 이미 존재하고
(`root:ops 0640`) 수리 워처가 같은 파일을 쓰며, 재조정 유닛도 `User=ops` 라 읽을 수 있다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from automation import deploy_reconcile_cli as cli
from automation import owner_notice

_REPO = Path(__file__).resolve().parents[2]
_SERVICE = _REPO / "automation" / "systemd" / "autophagy-deploy-reconcile.service"
_ENV_FILE = "/etc/autophagy/repair-approval.env"

_TOKEN = "TOKEN-SHOULD-NEVER-BE-PRINTED"


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """자격증명이 갖춰지고 전송이 성공하는 상태. 보낸 것을 기록한다."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", _TOKEN)
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "owner-1")
    # 전송은 공유 모듈에 있다. 그런데도 아래에서 cli.notify_owner 를 부르는 것은
    # 재조정 CLI 가 그 공유 전송을 실제로 쓰고 있음을 함께 고정하기 위해서다.
    monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda token, owner: "dm-1")
    monkeypatch.setattr(
        owner_notice, "send_notice", lambda token, channel, body: sent.append((channel, body))
    )
    return sent


def test_a_wired_notifier_delivers_and_reports_success(wired: list[tuple[str, str]]) -> None:
    assert cli.notify_owner("prod가 수렴하지 못했습니다") is True
    assert wired == [("dm-1", "prod가 수렴하지 못했습니다")]


@pytest.mark.parametrize("missing", ["DISCORD_BOT_TOKEN", "AUTOPHAGY_OWNER_ID"])
def test_missing_credentials_report_failure_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """자격증명이 없으면 조용히 성공했다고 하지도, 예외로 죽지도 않는다."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", _TOKEN)
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "owner-1")
    monkeypatch.delenv(missing, raising=False)
    assert cli.notify_owner("notice") is False


@pytest.mark.parametrize(
    "boom",
    [OSError("network"), RuntimeError("directory refused"), ValueError("bad payload")],
)
def test_a_transport_failure_never_escapes(
    monkeypatch: pytest.MonkeyPatch, wired: list[tuple[str, str]], boom: Exception
) -> None:
    """전송 실패가 예외로 빠져나가면 reconcile_tick 의 큐잉 복구가 통째로 무력화된다.

    그러면 알림을 붙이려다 수렴 자체를 멈추게 된다 — 붙이기 전보다 나쁘다.
    """
    def explode(token: str, channel: str, body: str) -> None:
        raise boom

    monkeypatch.setattr(owner_notice, "send_notice", explode)
    assert cli.notify_owner("notice") is False


def test_a_directory_failure_never_escapes(
    monkeypatch: pytest.MonkeyPatch, wired: list[tuple[str, str]]
) -> None:
    def explode(token: str, owner: str) -> str:
        raise OSError("cannot open owner dm")

    monkeypatch.setattr(owner_notice, "owner_dm_channel", explode)
    assert cli.notify_owner("notice") is False


def test_the_token_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, wired: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    """실패 경로는 시끄러워야 하지만, 시끄러움에 토큰이 섞이면 안 된다."""
    monkeypatch.setattr(
        owner_notice, "send_notice", lambda t, c, b: (_ for _ in ()).throw(OSError("boom"))
    )
    _ = cli.notify_owner("notice")
    captured = capsys.readouterr()
    assert _TOKEN not in captured.out + captured.err


def test_the_failure_path_is_loud(
    monkeypatch: pytest.MonkeyPatch, wired: list[tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    """큐잉된 채 아무 흔적도 남기지 않으면, 그것이 바로 이 기능이 없애려던 침묵이다."""
    monkeypatch.setattr(
        owner_notice, "send_notice", lambda t, c, b: (_ for _ in ()).throw(OSError("boom"))
    )
    _ = cli.notify_owner("notice")
    assert "NOTIFY" in capsys.readouterr().err


def test_the_tick_is_wired_to_the_real_notifier() -> None:
    """구현이 있어도 main() 이 자리표시자를 계속 쓰면 아무것도 달라지지 않는다."""
    source = (_REPO / "automation" / "deploy_reconcile_cli.py").read_text(encoding="utf-8")
    body = source[source.index("def main("):]
    assert "deliver=notify_owner" in body
    assert "unconfigured_notifier" not in body


def test_the_unit_reads_the_existing_approval_env() -> None:
    """새 시크릿을 만들지 않는다 — 수리 워처가 이미 쓰는 파일을 그대로 읽는다."""
    directives = [
        line.strip()
        for line in _SERVICE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert f"EnvironmentFile={_ENV_FILE}" in directives


def test_no_credential_is_hardcoded_in_the_unit() -> None:
    """토큰 모양 문자열이 유닛에 들어가면 secret-scan 이 배포를 막는다(그리고 마땅히 그렇다)."""
    body = _SERVICE.read_text(encoding="utf-8")
    assert not re.search(r"DISCORD_BOT_TOKEN\s*=\s*\S", body)
    assert not re.search(r"AUTOPHAGY_OWNER_ID\s*=\s*\S", body)
