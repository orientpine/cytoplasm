"""Staging dispatch has no content callback; authentication and budgets remain."""

from __future__ import annotations

from pathlib import Path

import yaml

_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "litellm-staging" / "config.yaml"


def test_staging_does_not_install_a_content_dispatch_guard() -> None:
    config = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))

    assert "callbacks" not in config["litellm_settings"]
    assert not config.get("router_settings", {}).get("enable_tag_filtering", False)


def test_staging_keeps_authenticated_fail_closed_budget_enforcement() -> None:
    config = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
    settings = config["litellm_settings"]

    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"
    assert config["general_settings"]["fail_closed_budget_enforcement"] is True
    assert settings["default_key_generate_params"]["max_budget"] == "os.environ/LITELLM_MONTHLY_HARD_CAP"
    assert settings["upperbound_key_generate_params"]["max_budget"] == "os.environ/LITELLM_MONTHLY_HARD_CAP"
