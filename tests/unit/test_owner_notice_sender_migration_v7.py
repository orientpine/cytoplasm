"""V7 regular-notice migration coverage, separated from FS3-pinned DM inventory tests.

These tests exercise only final stub transports: configured
``owner_notice_channel_id`` selects the notice channel, while its absence selects
owner DM. Patent export deliberately is not covered: its completion notice
contains a private patent Drive link and must remain DM-only.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "budget" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "procurement" / "scripts"))

from automation import owner_notice  # noqa: E402
from automation.selfskill_audit import report  # noqa: E402

budget_binding = importlib.import_module("budget_binding")
budget_confirm = importlib.import_module("budget_confirm")
procure_review = importlib.import_module("procure_review")


def _load_cost_report() -> ModuleType:
    path = _REPO / "automation" / "cost-report" / "send_cost_report.py"
    spec = importlib.util.spec_from_file_location("cost_report_sender_v7", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cost_report = _load_cost_report()


@pytest.fixture
def notice_attempts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    attempts: list[tuple[str, str]] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setattr(owner_notice, "_config_owner_id", lambda: "owner-1")
    monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda _token, _owner: "owner-dm")
    monkeypatch.setattr(
        owner_notice,
        "send_notice",
        lambda _token, channel_id, body: attempts.append((channel_id, body)),
    )
    return attempts


@pytest.mark.parametrize(
    ("channel", "expected_target"),
    (("notice-channel", "notice-channel"), ("", "owner-dm")),
)
def test_procurement_review_uses_facade_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
    channel: str,
    expected_target: str,
) -> None:
    """Attachment delivery still delegates target selection to owner_notice."""
    del notice_attempts
    target = tmp_path / "review.txt"
    _ = target.write_text("review", encoding="utf-8")
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: channel)
    monkeypatch.setattr(
        procure_review,
        "_post_attachment",
        lambda channel_id, _file, content: posted.append((channel_id, content)) or {"id": "1"},
    )

    procure_review.send_review(target, "검토")

    assert [channel_id for channel_id, _content in posted] == [expected_target]


@pytest.mark.parametrize(
    ("channel", "expected_target"),
    (("notice-channel", "notice-channel"), ("", "owner-dm")),
)
def test_cost_report_uses_facade_transport(
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
    channel: str,
    expected_target: str,
) -> None:
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: channel)

    cost_report.send_dm("masked cost report")

    assert notice_attempts == [(expected_target, "masked cost report")]


@pytest.mark.parametrize(
    ("channel", "expected_target"),
    (("notice-channel", "notice-channel"), ("", "owner-dm")),
)
def test_selfskill_report_uses_facade_transport(
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
    channel: str,
    expected_target: str,
) -> None:
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: channel)

    assert report.send_report((), account_label="agent") is True

    assert len(notice_attempts) == 1
    assert notice_attempts[0][0] == expected_target
    assert "[자체 스킬 감사]" in notice_attempts[0][1]


@pytest.mark.parametrize(
    ("channel", "expected_target"),
    (("notice-channel", "notice-channel"), ("", "owner-dm")),
)
def test_budget_result_notice_uses_facade_transport(
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
    channel: str,
    expected_target: str,
) -> None:
    class _LegacyDirectory:
        def owner_dm(self) -> str:
            return "legacy-dm"

    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: channel)
    monkeypatch.setattr(budget_binding, "approval_directory", _LegacyDirectory)
    # 클린 러너에는 ~/.hermes/interop/config.json 도 ~/.env.secrets 도 없다 — 형제 테스트처럼
    # 자격증명 해석을 스텁해 워크스테이션 상태에 기대지 않는다.
    monkeypatch.setattr(budget_confirm, "bot_token", lambda: "test-token")
    monkeypatch.setattr(budget_confirm, "owner_id", lambda: "owner-from-interop")
    monkeypatch.setattr(budget_confirm, "_api", lambda *_args, **_kwargs: {"id": "direct-dm"})

    result = budget_confirm.dm_owner("금액 없는 결과")

    assert result == "OWNER-NOTICE-SENT"
    assert notice_attempts == [(expected_target, "금액 없는 결과")]


def test_budget_result_notice_preserves_secret_file_credentials(
    monkeypatch: pytest.MonkeyPatch,
    notice_attempts: list[tuple[str, str]],
) -> None:
    """The facade receives credentials budget's legacy direct sender loaded itself."""
    class _LegacyDirectory:
        def owner_dm(self) -> str:
            return "legacy-dm"

    monkeypatch.delenv("DISCORD_BOT_TOKEN")
    monkeypatch.delenv("AUTOPHAGY_OWNER_ID", raising=False)
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda _home=None: "")
    monkeypatch.setattr(budget_binding, "approval_directory", _LegacyDirectory)
    monkeypatch.setattr(budget_confirm, "bot_token", lambda: "secret-file-token")
    monkeypatch.setattr(budget_confirm, "owner_id", lambda: "owner-from-interop")
    monkeypatch.setattr(budget_confirm, "_api", lambda *_args, **_kwargs: {"id": "direct-dm"})

    assert budget_confirm.dm_owner("금액 없는 결과") == "OWNER-NOTICE-SENT"

    assert os.environ["DISCORD_BOT_TOKEN"] == "secret-file-token"
    assert os.environ["AUTOPHAGY_OWNER_ID"] == "owner-from-interop"
    assert notice_attempts == [("owner-dm", "금액 없는 결과")]
