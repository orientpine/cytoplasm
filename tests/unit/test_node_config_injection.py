"""Regression: node.toml values must never inject lines into root-owned assets.

F1 (security audit, 2026-08-15). Every ``NodeConfig`` path field was validated
only by ``Path.is_absolute()``, ``origin_url`` only for non-emptiness, and
``deploy_ssh_host`` not at all — while ``node_asset_renderer`` substitutes all of
them verbatim into ``/etc/sudoers.d/*`` (0440 root:root) and
``/etc/systemd/system/*``. ``automation.install.assets`` writes ``node.toml``
into each service account's home at 0600 **owned by that account**, so the agent
account could rewrite its own copy: one embedded newline yields an extra sudoers
directive that ``visudo -cf`` accepts (it is syntactically valid), or an extra
``User=root`` / ``ExecStart=`` in a unit that normally runs unprivileged.

Two independent layers are asserted here, because the audit's root cause was
exactly the assumption that one validation point suffices:

1. ``node_config._validate`` rejects the load.
2. ``node_asset_renderer.render_asset`` rejects the substitution even for a
   ``NodeConfig`` built directly, bypassing the parser.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from automation.node_asset_renderer import render_asset
from automation.node_config import (
    NodeConfig,
    NodeConfigError,
    default_node_config,
    load_node_config,
)

ROOT = Path(__file__).resolve().parents[2]
SUDOERS_SEED = ROOT / "automation" / "sudoers.d" / "autophagy-skill-store"
UNIT_SEED = ROOT / "automation" / "systemd" / "autophagy-deploy-reconcile.service"

# The audit's proof-of-concept payloads, verbatim.
SUDOERS_PAYLOAD = "/srv/autophagy/libexec\nautophagy-agent ALL=(ALL) NOPASSWD: ALL\n#"
UNIT_PAYLOAD = "/srv/autophagy/current\nUser=root\nExecStart=/bin/sh -c 'id > /tmp/pwned'\n#"


def _write_override(tmp_path: Path, field: str, value: str) -> Path:
    path = tmp_path / "node.toml"
    _ = path.write_text(f"{field} = {json.dumps(value)}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("field", "payload"),
    [
        # A path field — the audit's escalation vector.
        ("libexec_dir", SUDOERS_PAYLOAD),
        ("release_current", UNIT_PAYLOAD),
        ("service_root", "/srv\ninjected"),
        # A URL field, previously checked only for emptiness.
        ("origin_url", "ssh://git.example.invalid/repo.git\ninjected"),
        # A host field, previously not validated at all.
        ("deploy_ssh_host", "node.example.invalid\ninjected"),
    ],
)
def test_embedded_newline_is_rejected_in_every_field_category(
    tmp_path: Path,
    field: str,
    payload: str,
) -> None:
    # Given: an agent-writable node.toml carrying the audit's injection payload.
    path = _write_override(tmp_path, field, payload)

    # When/Then: the load fails closed rather than producing a renderable config.
    with pytest.raises(NodeConfigError):
        _ = load_node_config(path)


@pytest.mark.parametrize("control", ["\r", "\x00", "\x1b", "\x7f", "\u0085", "\u2028"])
def test_other_control_characters_are_rejected_too(tmp_path: Path, control: str) -> None:
    # Given: a path field poisoned with a non-newline control character.
    path = _write_override(tmp_path, "libexec_dir", f"/srv/libexec{control}injected")

    # When/Then: the charset check is not newline-specific.
    with pytest.raises(NodeConfigError):
        _ = load_node_config(path)


def test_deploy_ssh_host_may_not_begin_with_a_dash(tmp_path: Path) -> None:
    # Given: a host that ssh would consume as an option instead of a destination.
    path = _write_override(tmp_path, "deploy_ssh_host", "-oProxyCommand=/bin/sh")

    with pytest.raises(NodeConfigError):
        _ = load_node_config(path)


def test_legitimate_paths_urls_and_hosts_are_still_accepted(tmp_path: Path) -> None:
    # Given: ordinary values, including a space and non-ASCII text in a path.
    path = tmp_path / "node.toml"
    _ = path.write_text(
        "\n".join((
            'origin_url = "git@github.example:team/project.git"',
            'deploy_ssh_host = "node-01.example.invalid"',
            'libexec_dir = "/srv/autophagy/lib exec"',
            'agent_home = "/home/에이전트"',
        )),
        encoding="utf-8",
    )

    # When: the override is loaded.
    config = load_node_config(path)

    # Then: nothing legitimate was broken by the new charset check.
    assert config.origin_url == "git@github.example:team/project.git"
    assert config.deploy_ssh_host == "node-01.example.invalid"
    assert config.libexec_dir == Path("/srv/autophagy/lib exec")
    assert config.agent_home == Path("/home/에이전트")


def test_renderer_rejects_a_directly_constructed_config() -> None:
    # Given: a NodeConfig built without the parser, so layer 1 never ran.
    poisoned = replace(default_node_config(), libexec_dir=Path(SUDOERS_PAYLOAD))

    # When/Then: the renderer refuses independently (defense in depth).
    with pytest.raises(NodeConfigError):
        _ = render_asset(SUDOERS_SEED, poisoned)


def test_renderer_rejects_a_directly_constructed_unit_payload() -> None:
    poisoned = replace(default_node_config(), release_current=Path(UNIT_PAYLOAD))

    with pytest.raises(NodeConfigError):
        _ = render_asset(UNIT_SEED, poisoned)


def test_clean_config_renders_exactly_the_template_lines() -> None:
    # Given: the tracked seed config and the real sudoers/unit templates.
    config: NodeConfig = default_node_config()

    for seed in (SUDOERS_SEED, UNIT_SEED):
        rendered = render_asset(seed, config)

        # Then: substitution never adds or removes a line.
        assert len(rendered.splitlines()) == len(
            seed.read_text(encoding="utf-8").splitlines()
        )

    # And: no sudoers directive granting blanket privileges appeared.
    sudoers = render_asset(SUDOERS_SEED, config)
    assert "NOPASSWD: ALL" not in sudoers
    unit = render_asset(UNIT_SEED, config)
    assert "User=root" not in unit
