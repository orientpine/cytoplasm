from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config


def test_systemd_asset_uses_configured_accounts_homes_and_runtime_paths(tmp_path: Path) -> None:
    source = tmp_path / "service"
    source.write_text(
        "User=$NODE_AGENT_ACCOUNT\nGroup=$NODE_AGENT_ACCOUNT\n"
        "EnvironmentFile=$NODE_AGENT_HOME/.env.secrets\n"
        "WorkingDirectory=$NODE_RELEASE_CURRENT\n",
        encoding="utf-8",
    )
    config = replace(
        default_node_config(),
        agent_account="runner",
        agent_home=Path("/users/runner"),
        release_current=Path("/opt/autophagy/current"),
    )

    rendered = render_asset(source, config)

    assert "User=runner" in rendered
    assert "Group=runner" in rendered
    assert "/users/runner/.env.secrets" in rendered
    assert "WorkingDirectory=/opt/autophagy/current" in rendered


def test_sudoers_asset_uses_configured_operator_and_service_accounts(tmp_path: Path) -> None:
    source = tmp_path / "sudoers"
    source.write_text(
        "$NODE_OPERATOR_ACCOUNT ALL=($NODE_AGENT_ACCOUNT,$NODE_PEER_ACCOUNT,$NODE_OPS_ACCOUNT) NOPASSWD: ALL\n",
        encoding="utf-8",
    )
    config = replace(
        default_node_config(),
        operator_account="owner",
        agent_account="primary",
        peer_account="reviewer",
        ops_account="infra",
    )

    assert render_asset(source, config) == "owner ALL=(primary,reviewer,infra) NOPASSWD: ALL\n"
