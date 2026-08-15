from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

from automation.install.assets import build_inputs
from automation.node_config import load_node_config


_REPO = Path(__file__).resolve().parents[2]


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm
    material += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} installer-test"


def test_build_inputs_renders_node_bound_units_helpers_hooks_and_runtime_config(
    tmp_path: Path,
) -> None:
    config = replace(
        load_node_config(_REPO / "configs" / "node.example.toml"),
        agent_account="third-agent",
        peer_account="third-peer",
        ops_account="third-ops",
        service_group="third-services",
        primary_node_name="third-node",
        agent_home=Path("/home/third-agent"),
        peer_home=Path("/home/third-peer"),
        ops_home=Path("/home/third-ops"),
        service_root=Path("/srv/third"),
        deploy_checkout=Path("/srv/third/checkout"),
        repair_work=Path("/srv/third/repair"),
        release_current=Path("/srv/third/autophagy-agent-current"),
        release_store=Path("/srv/third/autophagy-agent-releases"),
        libexec_dir=Path("/opt/third/libexec"),
    )

    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}

    service = files[Path("/etc/systemd/system/autophagy-deploy-reconcile.service")]
    assert "User=third-ops" in service.content
    assert "WorkingDirectory=/srv/third/autophagy-agent-current" in service.content
    helper = files[Path("/opt/third/libexec/autophagy-converge-origin-main")]
    assert helper.mode == 0o755
    assert "MIRROR=/srv/third/checkout" in helper.content
    assert "STORE_PARENT=/srv/third" in helper.content
    assert "LIBDIR=/opt/third/libexec/autophagy-converge.d" in helper.content
    assert files[Path("/srv/third/checkout/.git/hooks/pre-commit")].mode == 0o755
    assert "REFUSED" in files[Path("/srv/third/checkout/.git/hooks/pre-commit")].content
    repair_hook = files[Path("/srv/third/repair/.git/hooks/pre-commit")].content
    assert "gitleaks git --pre-commit --staged --redact --verbose" in repair_hook

    node_toml = tmp_path / "node.toml"
    _ = node_toml.write_text(
        files[Path("/home/third-ops/.hermes/node.toml")].content,
        encoding="utf-8",
    )
    assert load_node_config(node_toml) == replace(config, peer_attest_mode="signed")


def test_build_inputs_installs_same_node_config_for_each_runtime_account() -> None:
    config = load_node_config(_REPO / "configs" / "node.example.toml")

    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}

    contents = {
        files[home / ".hermes" / "node.toml"].content
        for home in (config.agent_home, config.peer_home, config.ops_home)
    }
    assert len(contents) == 1


def test_update_trust_file_comes_from_existing_bootstrap_contract() -> None:
    config = load_node_config(_REPO / "configs" / "node.example.toml")

    inputs = build_inputs(_REPO, config, _public_key())
    trust = next(spec for spec in inputs.files if spec.path == Path("/etc/autophagy/update-allowed-signers"))

    assert trust.mode == 0o644
    assert trust.owner == "root" and trust.group == "root"
    assert 'namespaces="git"' in trust.content
