from __future__ import annotations

import os
import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
_PROBE = _REPO / "automation" / "selfskill_root_probe.sh"
_CHECK_TYPE = "agent_selfskill_root_topology"
_MAIN_INVOCATION = 'main "$@"'
_REPO_ROOT_RESOLUTION = 'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"'
#: The registration is written against the node configuration, not against one
#: installation's hostnames and accounts, so pin the parameterized form verbatim.
_REGISTRATION = (
    '  "$PRIMARY_NODE agent selfskill root topology|agent_selfskill_root_topology'
    '|${PRIMARY_NODE}|$NODE_OPS_ACCOUNT|$NODE_SKILL_STORE/live"'
)


def _sourceable_healthcheck(tmp_path: Path) -> Path:
    """Return a healthcheck copy whose main guard cannot reach a real node."""
    body = _HEALTHCHECK.read_text(encoding="utf-8").replace(_MAIN_INVOCATION, ":")
    # healthcheck resolves its node configuration relative to its own location; the
    # copy lives in tmp_path, so pin the resolution at the real checkout the way
    # test_healthcheck_skill_mount_probe.py does. Everything else stays byte-identical.
    body = body.replace(_REPO_ROOT_RESOLUTION, f'REPO_ROOT="{_REPO}"')
    sourceable = tmp_path / "healthcheck_sourceable.sh"
    _ = sourceable.write_text(body, encoding="utf-8")
    for library in sorted((_REPO / "automation").glob("*.sh")):
        _ = (tmp_path / library.name).write_bytes(library.read_bytes())
    return sourceable


def _run_selfskill_check(
    tmp_path: Path, agent_home: Path, live_root: Path
) -> subprocess.CompletedProcess[str]:
    """Run the registered check with isolated topology paths."""
    env = dict(os.environ)
    env["HEALTHCHECK_SELFSKILL_AGENT_HOME"] = str(agent_home)
    env["HEALTHCHECK_SELFSKILL_LIVE_ROOT"] = str(live_root)
    return subprocess.run(
        (
            "bash",
            "-c",
            'source "$1"; run_check "selfskill|agent_selfskill_root_topology|node|ops|$2"',
            "bash",
            str(_sourceable_healthcheck(tmp_path)),
            str(live_root),
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_healthcheck_registers_the_agent_selfskill_root_probe_locally() -> None:
    # Given: the production healthcheck source
    healthcheck = _HEALTHCHECK.read_text(encoding="utf-8")

    # When: the static probe wiring is inspected
    registration = [
        line
        for line in healthcheck.splitlines()
        if f"|{_CHECK_TYPE}|" in line and line.startswith('  "')
    ]

    # Then: it is sourced, scheduled locally, guided, and dispatched exactly once.
    assert 'source "$(dirname "${BASH_SOURCE[0]}")/selfskill_root_probe.sh"' in healthcheck
    assert registration == [_REGISTRATION]
    assert _CHECK_TYPE in healthcheck.split('readonly LOCAL_PROBES="', 1)[1].split('"', 1)[0]
    assert f"{_CHECK_TYPE}) selfskill_root_guidance ;;" in healthcheck
    assert f"{_CHECK_TYPE}) probe_selfskill_root_topology" in healthcheck


def test_agent_selfskill_probe_asserts_the_provisioned_topology_read_only() -> None:
    # Given: the detector that healthcheck sources
    probe = _PROBE.read_text(encoding="utf-8")

    # When: its topology contract is inspected without touching the node
    required_assertions = (
        'mountpoint -q "$AGENT_SKILLS_ROOT"',
        '"$NODE_AGENT_ACCOUNT:$NODE_AGENT_ACCOUNT:700"',
        'guard_agent_created: true',
        'external_dirs:',
        '"- $GOVERNED_SKILLS_ROOT"',
        'root:root:755',
    )

    # Then: it detects each provisioner invariant and tells the operator how to recover.
    for assertion in required_assertions:
        assert assertion in probe
    for failure in (
        "SELFSKILL-ROOT-MOUNTPOINT",
        "SELFSKILL-ROOT-OWNER-MODE",
        "SELFSKILL-CONFIG-EXTERNAL-DIRS-MISSING",
        "SELFSKILL-CONFIG-GUARD-MISSING",
        "SELFSKILL-LIVE-OWNER-MODE",
    ):
        assert failure in probe
    assert "automation/provision-skill-roots.sh" in probe
    assert "docs/patch/2026-08-15-agent-selfskill-root-inversion.md#rollback" in probe
    assert "capture_on_node" not in probe
    assert "sudo -n" not in probe
    # And: the topology it pins belongs to the node configuration, never to one
    # installation — a re-hardcoded home, account, or store would pass every
    # assertion above while silently probing the wrong node.
    for literal in ("/home/agent", "/srv/autophagy-skills", '"agent:agent:700"'):
        assert literal not in probe
    assert 'node_config_sh.py" --print-env' in probe


def test_selfskill_topology_failure_is_reported_when_the_subject_exists(tmp_path: Path) -> None:
    # Given: a node-shaped agent home whose self-skill root has the wrong owner
    agent_home = tmp_path / "agent"
    (agent_home / ".hermes" / "skills").mkdir(parents=True)
    live_root = tmp_path / "live"
    live_root.mkdir()

    # When: healthcheck dispatches the registered self-skill probe
    result = _run_selfskill_check(tmp_path, agent_home, live_root)

    # Then: topology drift is a failure with a path-specific diagnostic.
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "SELFSKILL-ROOT-OWNER-MODE" in output
    assert str(agent_home / ".hermes" / "skills") in output


def test_absent_selfskill_subject_passes_without_a_ticket_or_failure(tmp_path: Path) -> None:
    # Given: a fixture host with neither an agent home nor a governed live store
    agent_home = tmp_path / "absent-agent"
    live_root = tmp_path / "absent-live"

    # When: healthcheck dispatches the registered self-skill probe
    result = _run_selfskill_check(tmp_path, agent_home, live_root)

    # Then: there is no topology subject to diagnose, so no failed check can ticket it.
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "SELFSKILL-" not in output
    assert "REPAIR_TICKET" not in output
