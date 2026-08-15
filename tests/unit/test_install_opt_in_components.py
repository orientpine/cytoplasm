"""W-F3-B — the installer's opt-in component surface, with managed-sync as its first user.

Opt-in has to mean two things at once and both are tested here: an unrequested component
contributes nothing at all, and a requested one converges exactly once no matter how many
times the installer runs.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from automation.install.assets import build_inputs, render_plan
from automation.install.components import (
    OPT_IN_COMPONENTS,
    UnknownComponentError,
    resolve_components,
)
from automation.install.installer import main as installer_main
from automation.install.plan import Check, EnableTimer, EnsureFile, SystemState, build_plan
from automation.node_config import default_node_config

_REPO = Path(__file__).resolve().parents[2]
_SERVICE = Path("/etc/systemd/system/autophagy-managed-sync.service")
_TIMER_UNIT = "autophagy-managed-sync.timer"
_TIMER = Path("/etc/systemd/system") / _TIMER_UNIT


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm
    material += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} opt-in-test"


def _inputs(*components: str):
    return build_inputs(_REPO, default_node_config(), _public_key(), components=components)


# --- opt-in means absent, not disabled ---------------------------------------------


def test_a_default_install_contributes_no_managed_sync_file_or_timer() -> None:
    inputs = _inputs()

    assert not [spec for spec in inputs.files if "managed-sync" in spec.path.name]
    assert _TIMER_UNIT not in inputs.timers


def test_a_requested_component_installs_its_units_root_owned_and_enables_its_timer() -> None:
    inputs = _inputs("managed-sync")
    files = {spec.path: spec for spec in inputs.files}

    for path in (_SERVICE, _TIMER):
        assert files[path].owner == "root" and files[path].group == "root"
        assert files[path].mode == 0o644
    assert _TIMER_UNIT in inputs.timers
    # The .service is installed but never enabled on its own — the timer starts it.
    assert "autophagy-managed-sync.service" not in inputs.timers


def test_component_units_are_rendered_for_this_node_not_copied_verbatim() -> None:
    inputs = _inputs("managed-sync")
    files = {spec.path: spec for spec in inputs.files}
    config = default_node_config()

    assert "$NODE_" not in files[_SERVICE].content
    assert f"User={config.agent_account}" in files[_SERVICE].content


def test_an_unknown_component_is_refused_by_name_instead_of_ignored() -> None:
    # Silently dropping a typo would install less than the operator asked for and still
    # report success — the one outcome an installer must never produce.
    with pytest.raises(UnknownComponentError) as error:
        _ = resolve_components(("managed-sink",))

    assert "managed-sink" in str(error.value)
    assert "managed-sync" in str(error.value)


def test_components_resolve_deterministically_and_deduplicate() -> None:
    assert resolve_components(("managed-sync", "managed-sync")) == (
        OPT_IN_COMPONENTS["managed-sync"],
    )


# --- idempotency: run the installer twice, get exactly one timer -------------------


def test_running_the_installer_twice_yields_exactly_one_timer() -> None:
    inputs = _inputs("managed-sync")

    first = build_plan(inputs, SystemState.empty())
    second = build_plan(inputs, SystemState.from_actions(first.actions))

    enabled = [
        action for action in first.actions
        if isinstance(action, EnableTimer) and action.name == _TIMER_UNIT
    ]
    assert len(enabled) == 1
    # The second plan is check-only: no unit is rewritten and no timer is re-enabled.
    assert all(isinstance(action, Check) for action in second.actions)


def test_a_second_run_does_not_rewrite_an_already_converged_unit() -> None:
    inputs = _inputs("managed-sync")
    first = build_plan(inputs, SystemState.empty())

    second = build_plan(inputs, SystemState.from_actions(first.actions))

    rewritten = [
        action for action in second.actions
        if isinstance(action, EnsureFile) and action.spec.path in (_SERVICE, _TIMER)
    ]
    assert rewritten == []


# --- the CLI flag ------------------------------------------------------------------


def test_dry_run_without_the_flag_never_mentions_the_component(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "trust.pub"
    _ = key.write_text(f"{_public_key()}\n", encoding="utf-8")

    code = installer_main(("--update-trust-key", str(key), "--dry-run"))

    assert code == 0
    assert "managed-sync" not in capsys.readouterr().out


def test_dry_run_with_the_flag_plans_the_units_and_the_timer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "trust.pub"
    _ = key.write_text(f"{_public_key()}\n", encoding="utf-8")

    code = installer_main(
        ("--update-trust-key", str(key), "--dry-run", "--with-component", "managed-sync")
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "file /etc/systemd/system/autophagy-managed-sync.service" in out
    assert f"timer {_TIMER_UNIT} enabled" in out


def test_an_unknown_component_name_stops_the_cli(tmp_path: Path) -> None:
    key = tmp_path / "trust.pub"
    _ = key.write_text(f"{_public_key()}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        _ = installer_main(
            ("--update-trust-key", str(key), "--dry-run", "--with-component", "nope")
        )

    assert exit_info.value.code != 0


# --- D3: the component delivers, it does not mount ---------------------------------


def test_no_planned_component_action_can_mount_a_release() -> None:
    plan = build_plan(_inputs("managed-sync"), SystemState.empty())

    rendered = render_plan(plan)

    assert "deploy-skill.sh" not in rendered
    assert "--activate-managed" not in rendered
