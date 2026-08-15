"""Reconciler state must survive reboots without ever becoming the reason prod is stale.

MD-2. The state file is what makes "exactly one owner notice per incident" true across
ticks and restarts. That gives it two failure modes that matter more than persistence
itself:

* A torn write (power loss mid-tick) must not leave a half-file that crashes every
  later tick — the reconciler would then be dead exactly when it is needed.
* A corrupt or unreadable file must degrade to "nothing is wrong yet", never abort the
  tick. Refusing to converge because a notification bookkeeping file is unparseable
  would trade a missing DM for a stale production runtime.

The file lives under /srv/autophagy-private because the unit runs with ProtectHome=yes
and cannot see $HOME — the same constraint that already governs the repair push key.
"""
from __future__ import annotations

import json
from pathlib import Path

from automation.deploy_reconcile import ReconcileState
from automation.deploy_reconcile_state import (
    DEFAULT_STATE_PATH,
    load_state,
    save_state,
)


def test_default_path_is_outside_home_and_inside_the_private_root() -> None:
    """ProtectHome=yes units see an empty /home; $HOME state would vanish at runtime."""
    assert str(DEFAULT_STATE_PATH).startswith("/srv/autophagy-private/")
    assert "/home/" not in str(DEFAULT_STATE_PATH)


def test_absent_state_reads_as_nothing_is_wrong(tmp_path: Path) -> None:
    assert load_state(tmp_path / "missing.json") == ReconcileState()


def test_round_trip_preserves_every_field(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = ReconcileState(
        consecutive_failures=2,
        drift_since=1234.5,
        notified_target="c" * 40,
        pending_notice="queued",
        incident_open=True,
    )
    save_state(path, state)
    assert load_state(path) == state


def test_state_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, ReconcileState())
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_save_is_atomic_leaving_no_partial_file(tmp_path: Path) -> None:
    """A reader must never observe a half-written state, so the write renames into place."""
    path = tmp_path / "state.json"
    save_state(path, ReconcileState(consecutive_failures=1))
    save_state(path, ReconcileState(consecutive_failures=2))
    assert load_state(path).consecutive_failures == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_corrupt_state_degrades_instead_of_killing_the_tick(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _ = path.write_text("{not json", encoding="utf-8")
    assert load_state(path) == ReconcileState()


def test_unknown_fields_from_a_future_version_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _ = path.write_text(
        json.dumps({"consecutive_failures": 1, "invented_later": True}), encoding="utf-8"
    )
    assert load_state(path) == ReconcileState(consecutive_failures=1)


def test_wrongly_typed_fields_degrade_rather_than_raise(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    _ = path.write_text(json.dumps({"consecutive_failures": "many"}), encoding="utf-8")
    assert load_state(path) == ReconcileState()


def test_save_creates_the_parent_directory_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "deploy-reconcile" / "state.json"
    save_state(path, ReconcileState())
    assert oct(path.parent.stat().st_mode)[-3:] == "700"
