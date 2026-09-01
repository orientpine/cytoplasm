"""ON-1 회귀: `owner_notice` 파사드의 통지 채널 지정 (`owner_notice_channel_id`).

고정하는 것:

* env(`OWNER_NOTICE_CHANNEL_ID`) → interop config → 미설정(DM) 순의 해석.
* 채널이 지정되면 **그 채널로만** 보낸다 — DM 폴백 없음, 실패는 False(호출자 큐잉).
* 설정을 읽을 수 없어도(EACCES 포함) 답은 ""(=DM) 이다 — ProtectHome 유닛에서
  홈을 찌르는 코드는 답해야지 던지면 안 된다(2026-08-21 repair 워처 5일 정지).
* 「절대 예외를 던지지 않는다」 계약 불변.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation import owner_notice


@pytest.fixture(autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-token")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "42")
    monkeypatch.delenv("OWNER_NOTICE_CHANNEL_ID", raising=False)


def _config(home: Path, payload: object) -> None:
    directory = home / ".hermes" / "interop"
    directory.mkdir(parents=True)
    _ = (directory / "config.json").write_text(json.dumps(payload), encoding="utf-8")


class TestChannelResolution:
    def test_a_env_wins_over_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
        _config(tmp_path, {"owner_notice_channel_id": "222"})
        assert owner_notice.owner_notice_channel(tmp_path) == "111"

    def test_b_config_key_is_read_when_env_is_unset(self, tmp_path: Path) -> None:
        _config(tmp_path, {"owner_notice_channel_id": " 333 "})
        assert owner_notice.owner_notice_channel(tmp_path) == "333"

    def test_c_missing_config_means_unconfigured(self, tmp_path: Path) -> None:
        assert owner_notice.owner_notice_channel(tmp_path) == ""

    def test_d_unreadable_config_answers_instead_of_raising(self, tmp_path: Path) -> None:
        _config(tmp_path, {"owner_notice_channel_id": "444"})
        config = tmp_path / ".hermes" / "interop" / "config.json"
        config.chmod(0o000)
        try:
            assert owner_notice.owner_notice_channel(tmp_path) == ""
        finally:
            config.chmod(0o600)

    def test_e_non_string_value_is_unconfigured(self, tmp_path: Path) -> None:
        _config(tmp_path, {"owner_notice_channel_id": 555})
        assert owner_notice.owner_notice_channel(tmp_path) == ""


class TestNotifyOwnerTargeting:
    def test_a_configured_channel_is_the_only_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "777")
        sent: list[tuple[str, str]] = []
        monkeypatch.setattr(
            owner_notice, "send_notice", lambda token, channel, body: sent.append((channel, body))
        )

        def no_dm(token: str, owner_id: str) -> str:
            raise AssertionError("채널이 지정되면 DM 해석을 시도하면 안 된다")

        monkeypatch.setattr(owner_notice, "owner_dm_channel", no_dm)
        assert owner_notice.notify_owner("hello") is True
        assert sent == [("777", "hello")]

    def test_b_unconfigured_falls_back_to_dm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda home=None: "")
        sent: list[str] = []
        monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda token, owner_id: "dm-1")
        monkeypatch.setattr(
            owner_notice, "send_notice", lambda token, channel, body: sent.append(channel)
        )
        assert owner_notice.notify_owner("hello") is True
        assert sent == ["dm-1"]

    def test_c_channel_send_failure_is_false_not_dm_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "777")
        attempts: list[str] = []

        def failing(token: str, channel: str, body: str) -> None:
            attempts.append(channel)
            raise RuntimeError("channel gone")

        monkeypatch.setattr(owner_notice, "send_notice", failing)
        monkeypatch.setattr(
            owner_notice,
            "owner_dm_channel",
            lambda token, owner_id: pytest.fail("실패 시 DM 으로 새지 않는다"),
        )
        assert owner_notice.notify_owner("hello") is False
        assert attempts == ["777"]  # 정확히 한 번, 지정 채널로만
