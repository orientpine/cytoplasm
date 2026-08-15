from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from automation.node_config import NodeConfigError, default_node_config, load_node_config


ROOT = Path(__file__).resolve().parents[2]


def test_missing_runtime_file_uses_public_safe_defaults(tmp_path: Path) -> None:
    config = load_node_config(tmp_path / "missing.toml")

    assert config == default_node_config()
    assert config.primary_node_name == "example-primary-node"
    assert config.rag_node_name == "example-rag-node"
    assert config.operator_account == "operator"
    assert config.deploy_checkout == Path("/srv/autophagy-agents")
    assert config.require_signed_updates is True


def test_runtime_file_without_update_policy_requires_signatures_by_default(tmp_path: Path) -> None:
    # Given: a third-party runtime file predating the optional update policy field.
    path = tmp_path / "node.toml"
    _ = path.write_text('primary_node_name = "third-party"\n', encoding="utf-8")

    # When: the partial runtime override is loaded.
    config = load_node_config(path)

    # Then: omission preserves the seed's secure signed-update policy.
    assert config.require_signed_updates is True


def test_runtime_file_replaces_every_default(tmp_path: Path) -> None:
    seed = default_node_config()
    values = {
        field: f"custom-{field}"
        for field in seed.__dataclass_fields__
        if field != "require_signed_updates" and field not in {
            "service_root", "deploy_checkout", "release_current", "release_store",
            "private_root", "skill_store", "repair_work", "repair_report_queue",
            "repair_report_ack", "repair_capability", "libexec_dir", "agent_home",
            "peer_home", "ops_home",
        }
    }
    for field in seed.__dataclass_fields__:
        if field != "require_signed_updates" and field not in values:
            values[field] = f"/custom/{field}"
    values["origin_url"] = "ssh://git.example.invalid/team/project.git"
    values["peer_attest_mode"] = "signed"
    values["agent_gateway_unit"] = "custom-agent.service"
    values["peer_gateway_unit"] = "custom-peer.service"
    path = tmp_path / "node.toml"
    text = "\n".join(f'{key} = "{value}"' for key, value in values.items())
    _ = path.write_text(f"{text}\nrequire_signed_updates = true\n", encoding="utf-8")

    config = load_node_config(path)

    assert config.primary_node_name == "custom-primary_node_name"
    assert config.agent_account == "custom-agent_account"
    assert config.release_current == Path("/custom/release_current")
    assert config.require_signed_updates is True
    assert config.peer_attest_mode == "signed"


@pytest.mark.parametrize(
    "text",
    [
        "not = [valid",
        'unknown = "field"',
        'origin_url = ""',
        'deploy_checkout = "relative/path"',
        'agent_account = "bad account"',
        'agent_gateway_unit = "not-a-service"',
        'require_signed_updates = "false"',
        "require_signed_updates = 1",
        'peer_attest_mode = "automatic"',
    ],
)
def test_malformed_or_invalid_runtime_file_fails_closed(tmp_path: Path, text: str) -> None:
    path = tmp_path / "node.toml"
    _ = path.write_text(text, encoding="utf-8")

    with pytest.raises(NodeConfigError):
        _ = load_node_config(path)


def test_shell_bridge_prints_sourceable_resolved_values(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config_dir = home / ".hermes"
    config_dir.mkdir(parents=True)
    _ = (config_dir / "node.toml").write_text(
        'deploy_ssh_host = "third party host"\nagent_account = "runner"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        (sys.executable, "-m", "automation.node_config_sh", "--print-env"),
        cwd=ROOT,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    shell = subprocess.run(
        ("bash", "-c", 'eval "$1"; printf "%s|%s" "$NODE_DEPLOY_SSH_HOST" "$NODE_AGENT_ACCOUNT"', "bash", result.stdout),
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.stdout == "third party host|runner"
