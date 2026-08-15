"""The repair push key must be reachable from inside the service sandbox.

Both repair units run with `ProtectHome=yes`, which makes /home, /root and
/run/user appear EMPTY to the service. A key stored there exists on disk, is
readable by hand over ssh, and is still invisible to the unit that needs it —
the push fails only in production, long after every test has passed. So the
constraint is derived from the unit files themselves rather than restated.
"""

from __future__ import annotations

import re
from pathlib import Path

from automation.repair import repair_ops_cli as cli

_UNITS = Path(__file__).resolve().parents[2] / "automation/repair/systemd"
# What ProtectHome= hides. https://www.freedesktop.org/software/systemd/man/systemd.exec.html
_SHADOWED = ("/home/", "/root/", "/run/user/")


def _units_protecting_home() -> list[Path]:
    return [
        unit
        for unit in sorted(_UNITS.glob("*.service"))
        if re.search(r"^ProtectHome=(yes|read-only)$", unit.read_text(encoding="utf-8"), re.M)
    ]


def test_default_push_key_when_units_protect_home_then_key_lives_outside_it() -> None:
    protecting = _units_protecting_home()
    assert protecting, "expected the repair units to sandbox home; the guard below assumes it"

    default = str(cli.DEFAULT_PUSH_KEY)
    assert not default.startswith(_SHADOWED), (
        f"{default} is hidden by ProtectHome in {[u.name for u in protecting]} — "
        "the unit would report a missing key even though the file exists"
    )


def test_default_push_key_when_chosen_then_sits_in_the_private_state_root() -> None:
    # Root AGENTS.md: runtime state and credentials live outside the checkout,
    # under /srv/autophagy-private (or ~/.hermes) — never in a tracked path.
    assert str(cli.DEFAULT_PUSH_KEY).startswith("/srv/autophagy-private/")


def test_default_known_hosts_when_units_protect_home_then_lives_outside_it() -> None:
    # Same trap as the key, one layer down: ssh resolves ~/.ssh/known_hosts through
    # the passwd entry, so ProtectHome hides it even when HOME is redirected. The
    # node has no /etc/ssh/ssh_known_hosts and no global ssh_config pin either, so
    # without a reachable file the push dies at host-key verification.
    assert not str(cli.DEFAULT_KNOWN_HOSTS).startswith(_SHADOWED)
    assert str(cli.DEFAULT_KNOWN_HOSTS).startswith("/srv/autophagy-private/")


def test_push_branch_when_given_known_hosts_then_verifies_against_it(tmp_path: Path) -> None:
    from tests.unit.test_repair_branch_push import _FakeRunner  # noqa: PLC0415

    runner = _FakeRunner()
    clone = cli.RepairWorkClone(tmp_path / "deploy", tmp_path / "work", runner)
    clone.push_branch(
        "t_abc123", ssh_key=tmp_path / "key", known_hosts=tmp_path / "known_hosts"
    )

    ssh_command = " ".join(runner.pushes()[0])
    assert "UserKnownHostsFile=" in ssh_command
    # Pinned, not bypassed: accept-new/no would trust any key presented on the
    # one path that carries a write credential.
    assert "StrictHostKeyChecking=yes" in ssh_command
    assert "StrictHostKeyChecking=no" not in ssh_command
    assert "StrictHostKeyChecking=accept-new" not in ssh_command
