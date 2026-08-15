from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from automation.rag_ingest.config import ConfigError, load_config


def write_config(tmp_path: Path, obsidian: dict[str, Any] | None = None) -> Path:
    secrets_file = tmp_path / "secrets"
    _ = secrets_file.write_text("RAG_MCP_API_KEY=test-key\n", encoding="utf-8")
    raw: dict[str, Any] = {
        "mcp_base_url": "http://rag.example.invalid:8765",
        "secrets_file": str(secrets_file),
        "api_key_env": "RAG_MCP_API_KEY",
        "state_dir": str(tmp_path / "state"),
        "wiki_dir": str(tmp_path / "wiki"),
        "notes_dir": str(tmp_path / "notes"),
        "meetings_dir": str(tmp_path / "notes" / "meetings"),
        "perspective": {"agent_id": "agent", "owner": "owner"},
    }
    if obsidian is not None:
        raw["obsidian"] = obsidian
    config_path = tmp_path / "config.json"
    _ = config_path.write_text(json.dumps(raw), encoding="utf-8")
    return config_path


def test_load_config_populates_valid_obsidian_source(tmp_path: Path) -> None:
    # Given
    mirror_dir = tmp_path / "obsidian-mirror"
    ssh_key_path = tmp_path / "id_ed25519"
    sensitivity_rules_path = tmp_path / "sensitivity-rules.yaml"
    config_path = write_config(
        tmp_path,
        {
            "enabled": True,
            "repo_url": "https://example.invalid/placeholder/obsidian.git",
            "mirror_dir": str(mirror_dir),
            "ssh_key_path": str(ssh_key_path),
            "branch": "develop",
            "exclude_names": [".custom", "archive*"],
            "sensitivity_rules_path": str(sensitivity_rules_path),
        },
    )

    # When
    config = load_config(config_path)

    # Then
    assert config.obsidian is not None
    assert config.obsidian.enabled is True
    assert config.obsidian.repo_url == "https://example.invalid/placeholder/obsidian.git"
    assert config.obsidian.mirror_dir == mirror_dir
    assert config.obsidian.ssh_key_path == ssh_key_path
    assert config.obsidian.branch == "develop"
    assert config.obsidian.exclude_names == (".custom", "archive*")
    assert config.obsidian.sensitivity_rules_path == sensitivity_rules_path


@pytest.mark.parametrize("missing_key", ["repo_url", "ssh_key_path", "sensitivity_rules_path"])
def test_enabled_obsidian_requires_security_and_repository_keys(
    tmp_path: Path, missing_key: str
) -> None:
    # Given
    obsidian = {
        "enabled": True,
        "repo_url": "https://example.invalid/placeholder/obsidian.git",
        "mirror_dir": str(tmp_path / "obsidian-mirror"),
        "ssh_key_path": str(tmp_path / "id_ed25519"),
        "sensitivity_rules_path": str(tmp_path / "sensitivity-rules.yaml"),
    }
    del obsidian[missing_key]
    config_path = write_config(tmp_path, obsidian)

    # When
    with pytest.raises(ConfigError, match=missing_key):
        load_config(config_path)


def test_absent_obsidian_block_is_disabled(tmp_path: Path) -> None:
    # Given
    config_path = write_config(tmp_path)

    # When
    config = load_config(config_path)

    # Then
    assert config.obsidian is None


def test_disabled_obsidian_block_is_not_half_populated(tmp_path: Path) -> None:
    # Given
    config_path = write_config(tmp_path, {"enabled": False})

    # When
    config = load_config(config_path)

    # Then
    assert config.obsidian is None


def test_malformed_obsidian_block_raises_config_error(tmp_path: Path) -> None:
    # Given
    config_path = write_config(tmp_path, {"enabled": "true"})

    # When / Then
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_wrong_obsidian_field_type_raises_config_error(tmp_path: Path) -> None:
    # Given
    config_path = write_config(
        tmp_path,
        {
            "enabled": True,
            "repo_url": 42,
            "mirror_dir": str(tmp_path / "obsidian-mirror"),
            "ssh_key_path": str(tmp_path / "id_ed25519"),
            "sensitivity_rules_path": str(tmp_path / "sensitivity-rules.yaml"),
        },
    )

    # When / Then
    with pytest.raises(ConfigError, match="repo_url"):
        load_config(config_path)
