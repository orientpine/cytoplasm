"""Deploy-checkout drift must reach the repair-ticket path, carrying its own recovery rule.

``probe_checkout_mirrors_origin`` only reports; ``main`` is what turns a report into work.
This drives the real ``healthcheck.sh`` end to end - real registry, real dispatch, real
``report_repair`` - and asserts a drifted checkout produces exactly one repair ticket.

The ticket body matters as much as its existence: the documented recovery for this fault is
``git format-patch`` -> ``git am`` on the workstation checkout, because the commits stranded
in prod exist nowhere else. A ticket that says only "check failed" invites the one repair
that destroys them (``git reset --hard``), so the guidance travels with the ticket.

Hermetic: an ``ssh`` stand-in first on PATH answers every probe locally - canned responses
for the service probes, the throwaway ``tmp_path`` checkout for the mirror probe, and a
journal file for the repair CLI invocation. No node, no network, no real repair ticket.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
_CHECK_NAME = "example-primary-node ops checkout mirrors origin/main"
_TICKET_ID = "t_testticket"

_FAKE_SSH = r"""#!/usr/bin/env bash
set -uo pipefail
cmd="${*: -1}"
cmd="${cmd//\/srv\/autophagy-agents/$FAKE_CHECKOUT}"
case "$cmd" in
  *repair_cli.py*detect*)
    { printf 'DETECT %s\n' "$cmd"; cat; } >> "$TICKET_JOURNAL"
    echo "t_testticket"
    ;;
  *health/liveliness*) echo 200 ;;
  *:8800/*) echo 401 ;;
  *:8001/health*) echo '{"status":"ok","model":"BAAI/bge-m3","dimensions":1024}' ;;
  *:6333/healthz*) echo 'healthz check passed' ;;
  *:8765/health*) echo '{"status":"ok","collection":"personal_cha"}' ;;
  *synthetic-does-not-exist*) echo inactive; exit 3 ;;
  *systemctl*is-active*) echo active ;;
  *merge-base*--is-ancestor*) exec bash -c "$cmd" ;;
  *) printf 'unexpected remote command: %s\n' "$cmd" >&2; exit 97 ;;
esac
"""

_FAKE_SUDO = """#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|-H) shift ;;
    -u) shift 2 ;;
    *) break ;;
  esac
done
exec env "$@"
"""


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    )


def _mirror_checkout(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _ = subprocess.run(
        ("git", "init", "--bare", "--initial-branch=main", str(origin)),
        check=True,
        capture_output=True,
        text=True,
    )
    checkout = tmp_path / "autophagy-agents"
    checkout.mkdir()
    _git(checkout, "init", "--initial-branch=main")
    _git(checkout, "config", "user.email", "ops@test.local")
    _git(checkout, "config", "user.name", "ops")
    _git(checkout, "config", "commit.gpgsign", "false")
    (checkout / "SKILL.md").write_text("version: 1.5.3\n", encoding="utf-8")
    _git(checkout, "add", "SKILL.md")
    _git(checkout, "commit", "-m", "deployed state")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-u", "origin", "main")
    return checkout


def _drifted_checkout(tmp_path: Path) -> Path:
    """The exact 2026-07-27 shape: prod holds a commit origin/main has never seen."""
    checkout = _mirror_checkout(tmp_path)
    (checkout / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
    _git(checkout, "commit", "-am", "learned in prod, never pushed")
    return checkout


def _behind_checkout(tmp_path: Path) -> Path:
    """Origin moved one commit ahead; prod is stale. Invisible to the old probe."""
    checkout = _mirror_checkout(tmp_path)
    mover = tmp_path / "mover"
    _ = subprocess.run(
        ("git", "clone", str(tmp_path / "origin.git"), str(mover)),
        check=True, capture_output=True, text=True,
    )
    _git(mover, "config", "user.email", "mover@test.local")
    _git(mover, "config", "user.name", "mover")
    _git(mover, "config", "commit.gpgsign", "false")
    (mover / "SKILL.md").write_text("version: 1.6.0\n", encoding="utf-8")
    _git(mover, "commit", "-am", "a commit only origin has")
    _git(mover, "push", "origin", "main")
    return checkout


def _fake_bin(tmp_path: Path, *, ssh_down: bool = False) -> Path:
    fake_bin = tmp_path / ("bin_down" if ssh_down else "bin")
    fake_bin.mkdir(exist_ok=True)
    ssh_body = "#!/usr/bin/env bash\nexit 255\n" if ssh_down else _FAKE_SSH
    for name, body in (("ssh", ssh_body), ("sudo", _FAKE_SUDO)):
        stub = fake_bin / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    return fake_bin


@dataclass(frozen=True, slots=True)
class Sweep:
    """One healthcheck run plus the repair tickets it produced."""

    returncode: int
    output: str
    tickets: str


def _quiet_skill_mounts(tmp_path: Path) -> tuple[Path, Path]:
    """A release/live pair that agrees with itself: no skills, no mounts, no drift."""
    runtime, live = tmp_path / "skill-runtime", tmp_path / "skill-live"
    (runtime / "automation").mkdir(parents=True, exist_ok=True)
    (runtime / "skills").mkdir(parents=True, exist_ok=True)
    live.mkdir(parents=True, exist_ok=True)
    (runtime / "automation" / "__init__.py").touch()
    for module in ("skill_mount_drift.py", "skill_review.py"):
        source = _REPO / "automation" / module
        _ = (runtime / "automation" / module).write_bytes(source.read_bytes())
    return runtime, live


def _quiet_release_probes(tmp_path: Path, checkout: Path) -> tuple[Path, Path, Path]:
    sha = subprocess.run(
        ("git", "-C", str(checkout), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    generation = tmp_path / "releases" / sha
    source = generation / "automation"
    installed = tmp_path / "installed-helper"
    source.mkdir(parents=True)
    installed.mkdir()
    for source_name, installed_name in (
        ("release_store.py", "autophagy-install-release"),
        ("release_provenance.py", "release_provenance.py"),
    ):
        payload = (_REPO / "automation" / source_name).read_bytes()
        _ = (source / source_name).write_bytes(payload)
        _ = (installed / installed_name).write_bytes(payload)
    current = tmp_path / "current-release"
    current.symlink_to(generation, target_is_directory=True)
    return current, installed / "autophagy-install-release", installed / "release_provenance.py"


def _sweep(tmp_path: Path, checkout: Path, *args: str, ssh_down: bool = False) -> Sweep:
    journal = tmp_path / "tickets.txt"
    env = dict(os.environ)
    env["PATH"] = f"{_fake_bin(tmp_path, ssh_down=ssh_down)}{os.pathsep}{env['PATH']}"
    env["HEALTHCHECK_LOG_DIR"] = str(tmp_path / "logs")
    env["HEALTHCHECK_SSH_USER"] = ""
    env["HEALTHCHECK_SSH_IDENTITY"] = ""
    env["FAKE_CHECKOUT"] = str(checkout)
    # Once the checkout probe runs locally (no ssh) the FAKE_CHECKOUT rewrite no
    # longer reaches it; the local probe reads its target from this env instead.
    env["HEALTHCHECK_OPS_CHECKOUT"] = str(checkout)
    current, helper, provenance = _quiet_release_probes(tmp_path, checkout)
    env["RUNTIME_RELEASE_CURRENT"] = str(current)
    env["HEALTHCHECK_RELEASE_SOURCE_ROOT"] = str(current)
    env["HEALTHCHECK_RELEASE_HELPER"] = str(helper)
    env["HEALTHCHECK_RELEASE_PROVENANCE"] = str(provenance)
    update_trust = tmp_path / "update_trust_probe.py"
    update_trust.write_text("raise SystemExit(0)\n", encoding="utf-8")
    env["UPDATE_TRUST_SCRIPT"] = str(update_trust)
    env["TICKET_JOURNAL"] = str(journal)
    # The skill-mount probe also runs locally, so a real sweep would judge THIS host's
    # /srv and fail for reasons unrelated to the checkout. Point it at a matching
    # (empty) pair so it passes and these assertions stay about the checkout ticket.
    runtime, live = _quiet_skill_mounts(tmp_path)
    env["AUTOPHAGY_RUNTIME_ROOT"] = str(runtime)
    env["HEALTHCHECK_SKILL_LIVE_ROOT"] = str(live)
    result = subprocess.run(
        ("bash", str(_HEALTHCHECK), *args),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return Sweep(
        returncode=result.returncode,
        output=result.stdout + result.stderr,
        tickets=journal.read_text(encoding="utf-8") if journal.exists() else "",
    )


def test_a_mirrored_checkout_sweeps_clean(tmp_path: Path) -> None:
    # Given a checkout that still mirrors origin/main
    sweep = _sweep(tmp_path, _mirror_checkout(tmp_path))

    # Then the sweep is healthy and nothing is ticketed
    assert sweep.returncode == 0, sweep.output
    assert "ALL_HEALTHY" in sweep.output
    assert sweep.tickets == ""


def test_drift_fails_the_sweep_and_raises_a_repair_ticket(tmp_path: Path) -> None:
    # Given a checkout holding a commit origin/main has never seen
    sweep = _sweep(tmp_path, _drifted_checkout(tmp_path))

    # Then the sweep fails and the drift reaches the existing repair-ticket path
    assert sweep.returncode == 1, sweep.output
    assert f"FAIL {_CHECK_NAME}" in sweep.output
    assert f"REPAIR_TICKET {_TICKET_ID}" in sweep.output
    assert "--source healthcheck" in sweep.tickets
    assert f"--location '{_CHECK_NAME}'" in sweep.tickets


def test_only_the_drifted_check_is_ticketed(tmp_path: Path) -> None:
    """The healthy services must not ride along as ticket noise."""
    sweep = _sweep(tmp_path, _drifted_checkout(tmp_path))

    assert sweep.tickets.count("DETECT ") == 1


def test_the_drift_ticket_names_the_non_destructive_recovery(tmp_path: Path) -> None:
    """Stranded commits exist nowhere else - the ticket must not invite a discard."""
    sweep = _sweep(tmp_path, _drifted_checkout(tmp_path))

    body = sweep.tickets
    assert "git format-patch" in body
    assert "git am" in body
    assert "reset --hard" in body
    # The two drift shapes must not merge: an AHEAD checkout has stranded commits
    # and needs format-patch, NOT a pull. land.sh here would be the wrong advice.
    assert "land.sh" not in body


def test_a_healthy_service_probe_is_never_given_drift_guidance(tmp_path: Path) -> None:
    """Guidance is per-probe: an unrelated failure must not inherit the checkout advice."""
    sweep = _sweep(tmp_path, _mirror_checkout(tmp_path), "--synthetic-failure")

    assert sweep.returncode == 1, sweep.output
    assert "DETECT " in sweep.tickets


def test_a_behind_checkout_is_ticketed_with_pull_not_patch_guidance(tmp_path: Path) -> None:
    """Origin moved ahead; prod just needs to catch up — land.sh, never format-patch."""
    sweep = _sweep(tmp_path, _behind_checkout(tmp_path))

    assert sweep.returncode == 1, sweep.output
    assert f"FAIL {_CHECK_NAME}" in sweep.output
    assert sweep.tickets.count("DETECT ") == 1
    assert "RELEASE-STALE-SUPPRESSED-WARN" in sweep.output
    assert "land.sh" in sweep.tickets
    assert "format-patch" not in sweep.tickets


def test_a_total_ssh_outage_reports_infra_failure_not_nine_tickets(tmp_path: Path) -> None:
    """Regression lock for finding γ / d7ed0ad.

    Once the checkout probe runs locally it passes during a fleet-wide SSH outage,
    so the all-fail guard would see 9-of-10 and emit nine tickets — back to the
    'one credential problem looks like nine outages' failure. A total remote outage
    must still collapse to a single INFRA_FAILURE, never per-service tickets.
    """
    sweep = _sweep(tmp_path, _mirror_checkout(tmp_path), ssh_down=True)

    assert sweep.returncode == 1, sweep.output
    assert "INFRA_FAILURE" in sweep.output
    assert sweep.tickets == ""
    assert "format-patch" not in sweep.tickets
