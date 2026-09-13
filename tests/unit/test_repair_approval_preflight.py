"""CLI construction must defer external surfaces until the card is frozen."""
from __future__ import annotations

import os
import sys
from datetime import datetime, tzinfo
from pathlib import Path
from unittest.mock import patch

import pytest

from automation.interop.approval_types import Probe
from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, POLICY_VERSION, RequestThread, required_surface
from automation.repair import repair_approval_render, repair_ops_cli, repair_ops_discord
from automation.repair.repair_ops_approval_gate import probe_pending
from automation.repair.repair_ops_pending import PendingRepairApprovalStore
from automation.repair.repair_approval_render import ApprovalRenderError
from tests.unit.test_repair_approval_binding import _FakeDiscordHttp, _ThreadOpeningOpsDirectory
from tests.unit.test_repair_approval_envelope import NOW, PATCH, TICKET


class _Clock:
    @staticmethod
    def now(_zone: tzinfo) -> datetime:
        return NOW.replace(microsecond=123456)


def test_cli_has_zero_effects_when_both_renderers_refuse(tmp_path: Path) -> None:
    # Given: real CLI configuration and a directory that counts actual thread opens.
    config = repair_ops_cli.RepairOpsConfig(
        TICKET, tmp_path / "checkout", tmp_path / "logs", tmp_path / "plans",
        tmp_path / "approvals.jsonl", None, None,
    )
    source = tmp_path / "patch.diff"
    source.write_text(PATCH, encoding="utf-8")
    directory = _ThreadOpeningOpsDirectory()
    http = _FakeDiscordHttp()
    with (
        patch.dict(os.environ, {
            "DISCORD_BOT_TOKEN": "synthetic", "AUTOPHAGY_OWNER_ID": "111",
            "REPAIR_APPROVAL_PENDING_ROOT": str(tmp_path / "pending"),
        }, clear=True),
        patch.object(repair_ops_discord, "directory_for_ops", lambda token, owner: directory),
        patch.object(repair_ops_discord, "_open_discord", http),
        patch.object(repair_approval_render, "MAX_APPROVAL_CONTENT_CHARS", 10),
        patch.object(repair_ops_cli, "datetime", _Clock),
    ):
        # When: construction and permits both run, not a preconstructed posting fake.
        approval = repair_ops_cli._approval(config)
        assert approval is not None
        with pytest.raises(ApprovalRenderError):
            approval.permits(TICKET, source)
    # Then: refusal leaves no thread, card or durable approval state.
    assert directory.opened == []
    assert http.posts == []
    assert not (tmp_path / "pending").exists()


@pytest.mark.parametrize("capability", [True, False], ids=["v3", "v2-fallback"])
def test_cli_posts_frozen_card_when_surface_setup_loses_rendering(
    tmp_path: Path, capability: bool,
) -> None:
    # Given: preflight has its capability, but surface setup makes rendering fail.
    config = repair_ops_cli.RepairOpsConfig(
        TICKET, tmp_path / "checkout", tmp_path / "logs", tmp_path / "plans",
        tmp_path / "approvals.jsonl", None, None,
    )
    source = tmp_path / "patch.diff"
    source.write_text(PATCH, encoding="utf-8")
    directory = _ThreadOpeningOpsDirectory()
    http = _FakeDiscordHttp()
    setup_calls: list[str] = []

    def resolve(token: str, owner: str) -> _ThreadOpeningOpsDirectory:
        setup_calls.append(owner)
        repair_approval_render.MAX_APPROVAL_CONTENT_CHARS = 10
        return directory

    with (
        patch.dict(os.environ, {
            "DISCORD_BOT_TOKEN": "synthetic", "AUTOPHAGY_OWNER_ID": "111",
            "REPAIR_APPROVAL_PENDING_ROOT": str(tmp_path / "pending"),
        }, clear=True),
        patch.dict(sys.modules, {} if capability else {"automation.interop.owner_message": None}),
        patch.object(repair_ops_discord, "directory_for_ops", resolve),
        patch.object(repair_ops_discord, "_open_discord", http),
        patch.object(repair_approval_render, "MAX_APPROVAL_CONTENT_CHARS", 1900),
        patch.object(repair_ops_cli, "datetime", _Clock),
    ):
        approval = repair_ops_cli._approval(config)
        assert approval is not None
        assert setup_calls == []
        # When: the real CLI posting path resolves its surface after preflight.
        approval.permits(TICKET, source)
    # Then: the frozen chosen version posts once to the unchanged request policy.
    record = PendingRepairApprovalStore(tmp_path / "pending").get(TICKET)
    assert record is not None and record.render_version == (3 if capability else 2)
    assert setup_calls == ["111"]
    assert directory.opened == [RequestThread(title=TICKET)]
    assert len(http.posts) == 1
    binding = ApprovalBinding(
        ApprovalKind.REPAIR, required_surface(ApprovalKind.REPAIR), directory.thread_id(0), POLICY_VERSION,
    )
    api = repair_ops_discord.RepairDiscordApi("synthetic", binding, directory, "111")
    with patch.object(repair_ops_discord, "_open_discord", http):
        assert probe_pending(record, "111", api) is Probe.BOUND_PENDING
