from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

import automation.deploy_reconcile_cli as reconcile_cli
from automation.deploy_update_channel import save_update_channel_binding
from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config
from automation.update_trust import TrustedUpdate

_REPO = Path(__file__).resolve().parents[2]
_SERVICE = _REPO / "automation" / "systemd" / "autophagy-deploy-reconcile.service"
_SUDOERS = _REPO / "automation" / "sudoers.d" / "autophagy-roster-read"


def test_service_reexposes_only_the_agent_roster_read_only() -> None:
    rendered = render_asset(_SERVICE, default_node_config())
    directives = {
        line.strip()
        for line in rendered.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "BindReadOnlyPaths=-/home/agent/.hermes/roster.yaml" in directives
    assert "BindPaths=/home/agent/.hermes/roster.yaml" not in directives


def test_ops_may_only_read_the_fixed_agent_roster_for_channel_selection() -> None:
    rendered = render_asset(_SUDOERS, default_node_config()).splitlines()

    assert (
        "ops ALL=(agent) NOPASSWD: /usr/bin/cat -- /home/agent/.hermes/roster.yaml"
        in rendered
    )


def test_candidate_update_sha_when_channel_is_null_uses_the_signature_only_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[Path, str | None, Path]] = []

    def resolve(mirror: Path, *, remote_url: str | None, floor_path: Path) -> TrustedUpdate:
        observed.append((mirror, remote_url, floor_path))
        return TrustedUpdate(tag="v1.0.0", commit_sha="c" * 40)

    monkeypatch.setattr(reconcile_cli, "resolve_signed_update", resolve)

    assert reconcile_cli.candidate_update_sha() == "c" * 40
    # No policy argument travels with it: `require_signed_updates` must never be able to
    # let this pre-gate approve a target the root helper would then refuse (2026-08-21).
    # The anti-rollback floor (C1) does travel, so this path and the helper's independent
    # re-verification share one anchor.
    assert observed == [(reconcile_cli.MIRROR, None, reconcile_cli.RELEASE_FLOOR)]


def test_candidate_update_sha_when_channel_is_set_passes_the_roster_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = "https://updates.example.invalid/autophagy.git"
    observed: list[tuple[str | None, Path]] = []

    def resolve(_mirror: Path, *, remote_url: str | None, floor_path: Path) -> TrustedUpdate:
        observed.append((remote_url, floor_path))
        return TrustedUpdate(tag="v2.0.0", commit_sha="d" * 40)

    monkeypatch.setattr(reconcile_cli, "resolve_signed_update", resolve)

    assert reconcile_cli.candidate_update_sha(channel) == "d" * 40
    assert observed == [(channel, reconcile_cli.RELEASE_FLOOR)]


def test_roster_update_channel_when_present_returns_it(tmp_path: Path) -> None:
    roster = tmp_path / "roster.yaml"
    _ = roster.write_text(
        (_REPO / "configs" / "roster.example.yaml").read_text(encoding="utf-8")
        + "\nupdate_channel: https://updates.example.invalid/autophagy.git\n",
        encoding="utf-8",
    )

    assert reconcile_cli.roster_update_channel(roster) == (
        "https://updates.example.invalid/autophagy.git"
    )


def test_roster_update_channel_when_unset_or_invalid_preserves_upstream(
    tmp_path: Path,
) -> None:
    roster = tmp_path / "roster.yaml"
    _ = roster.write_text(
        (_REPO / "configs" / "roster.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert reconcile_cli.roster_update_channel(roster) is None
    _ = roster.write_text("schema: 1\n", encoding="utf-8")
    assert reconcile_cli.roster_update_channel(roster) is None


def test_update_channel_binding_is_atomic_owner_only_and_represents_null(
    tmp_path: Path,
) -> None:
    binding = tmp_path / "deploy-reconcile" / "update-channel.json"

    save_update_channel_binding("https://updates.example.invalid/autophagy.git", binding)
    assert json.loads(binding.read_text(encoding="utf-8")) == {
        "update_channel": "https://updates.example.invalid/autophagy.git",
        "version": 1,
    }
    assert stat.S_IMODE(binding.stat().st_mode) == 0o600

    save_update_channel_binding(None, binding)
    assert json.loads(binding.read_text(encoding="utf-8")) == {
        "update_channel": None,
        "version": 1,
    }


def test_main_when_roster_channel_is_set_binds_the_same_channel_before_convergence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    channel = "https://updates.example.invalid/autophagy.git"
    target = "e" * 40
    events: list[tuple[str, str | None]] = []

    monkeypatch.setattr(reconcile_cli, "roster_update_channel", lambda: channel)

    monkeypatch.setattr(reconcile_cli, "unconfigured_reason", lambda _config: None)

    def candidate(selected_channel: str) -> str:
        events.append(("resolve", selected_channel))
        return target

    def bind(selected_channel: str | None, _path: Path) -> None:
        events.append(("bind", selected_channel))

    def transition(_candidate_sha: str, _prior_sha: str) -> int:
        events.append(("converge", None))
        return 0

    def sync(_sha: str, *, update_channel: str) -> str:
        events.append(("sync", update_channel))
        return reconcile_cli.MIRROR_IN_SYNC

    def deliver(_notice: str) -> bool:
        return True

    monkeypatch.setattr(reconcile_cli, "candidate_update_sha", candidate)
    def current() -> str:
        return "a" * 40

    monkeypatch.setattr(reconcile_cli, "persist_update_channel_binding", bind)
    monkeypatch.setattr(reconcile_cli, "current_release_sha", current)
    monkeypatch.setattr(reconcile_cli, "DEFAULT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(reconcile_cli, "run_release_update", transition)
    monkeypatch.setattr(reconcile_cli, "notify_owner", deliver)
    monkeypatch.setattr(reconcile_cli, "sync_mirror", sync)

    assert reconcile_cli.main() == 0
    assert events == [
        ("resolve", channel),
        ("bind", channel),
        ("converge", None),
        ("sync", channel),
    ]
