from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

from automation.install.assets import build_inputs, render_node_toml
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


def test_build_inputs_installs_node_config_for_reconciler_and_runtime_accounts() -> None:
    config = load_node_config(_REPO / "configs" / "node.example.toml")

    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}
    rendered = render_node_toml(replace(config, peer_attest_mode="signed"))

    reconciler = files[Path("/etc/autophagy/node.toml")]
    assert reconciler.content == rendered
    assert reconciler.mode == 0o644
    assert reconciler.owner == "root"
    assert reconciler.group == "root"

    for account, home in (
        (config.agent_account, config.agent_home),
        (config.peer_account, config.peer_home),
        (config.ops_account, config.ops_home),
    ):
        runtime = files[home / ".hermes" / "node.toml"]
        assert runtime.content == reconciler.content
        assert runtime.mode == 0o600
        assert runtime.owner == account
        assert runtime.group == account


def test_build_inputs_installs_command_sync_dropins_for_both_gateways() -> None:
    config = replace(
        load_node_config(_REPO / "configs" / "node.example.toml"),
        agent_account="third-agent",
        peer_account="third-peer",
        agent_home=Path("/home/third-agent"),
        peer_home=Path("/home/third-peer"),
        agent_gateway_unit="third-agent-gateway.service",
        peer_gateway_unit="third-peer-gateway.service",
    )

    inputs = build_inputs(_REPO, config, _public_key())
    files = {spec.path: spec for spec in inputs.files}
    content = "[Service]\nEnvironment=DISCORD_COMMAND_SYNC_POLICY=bulk\n"
    expected = {
        Path(
            "/home/third-agent/.config/systemd/user/"
            "third-agent-gateway.service.d/30-command-sync.conf"
        ): "third-agent",
        Path(
            "/home/third-peer/.config/systemd/user/"
            "third-peer-gateway.service.d/30-command-sync.conf"
        ): "third-peer",
    }

    for path, account in expected.items():
        dropin = files[path]
        assert dropin.content == content
        assert dropin.mode == 0o600
        assert dropin.owner == account
        assert dropin.group == account


def test_update_trust_file_comes_from_existing_bootstrap_contract() -> None:
    config = load_node_config(_REPO / "configs" / "node.example.toml")

    inputs = build_inputs(_REPO, config, _public_key())
    trust = next(spec for spec in inputs.files if spec.path == Path("/etc/autophagy/update-allowed-signers"))

    assert trust.mode == 0o644
    assert trust.owner == "root" and trust.group == "root"
    assert 'namespaces="git"' in trust.content
