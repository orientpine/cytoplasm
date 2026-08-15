"""The account a CI runner executes as, and the one command it may escalate.

MD-3. A self-hosted runner executes whatever the workflow file says, and the workflow
file changes with every merge. So the question is never "is the workflow safe" but
"what can the account it runs as reach". Running it as `ops` was rejected for exactly
that reason: `ops` can read /srv/autophagy-private (the repair push key lives there)
and holds the release-install grant, so merging a PR would have been an escalation.

`deploy-runner` therefore exists to be boring. It is in no interesting group — most
sharply not `docker`, because /var/run/docker.sock is present on the node (root:docker,
verified) and membership in that group is root by another name. Its sudoers grant names
one absolute path with NO arguments and NO wildcard, which is what makes the privileged
surface a single fixed command rather than a command family.

This is its own provisioner rather than a change to bootstrap-accounts.sh: that file is
claimed as the sole source change of the active repair-report-rollout plan
(.omo/plans/repair-report-rollout.md:32), and provision-release-store.sh set the
precedent for sidestepping the collision the same way.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PROVISION = _REPO / "automation" / "provision-deploy-runner.sh"
_SUDOERS = _REPO / "automation" / "sudoers.d" / "autophagy-deploy-runner"
_CONVERGE_PROVISION = _REPO / "automation" / "provision-deploy-converge.sh"

_HELPER = "/usr/local/libexec/autophagy-converge-origin-main"
_FORBIDDEN_GROUPS = ("docker", "lxd", "ops", "agent", "peer", "autophagy", "sudo", "adm")


def _run(prefix: Path, *, times: int = 1) -> subprocess.CompletedProcess[str]:
    """Run the provisioner off-node: shim the commands that need a real root."""
    shim = (
        "install() {\n"
        '  local args=()\n'
        '  while (( $# )); do\n'
        '    case "$1" in\n'
        "      -o|-g) shift 2 ;;\n"
        '      *) args+=("$1"); shift ;;\n'
        "    esac\n"
        "  done\n"
        '  command install "${args[@]}"\n'
        "}\n"
        f'useradd() {{ printf "useradd %s\\n" "$*" >> "{prefix}/calls.log"; }}\n'
        f'usermod() {{ printf "usermod %s\\n" "$*" >> "{prefix}/calls.log"; }}\n'
        f'gpasswd() {{ printf "gpasswd %s\\n" "$*" >> "{prefix}/calls.log"; }}\n'
        f'chown() {{ printf "chown %s\\n" "$*" >> "{prefix}/calls.log"; }}\n'
        f'visudo() {{ printf "visudo %s\\n" "$*" >> "{prefix}/calls.log"; return 0; }}\n'
        'id() { return 1; }\n'  # the account does not exist yet
        "export -f install useradd usermod gpasswd chown visudo id\n"
    )
    calls = "".join(
        f'DEPLOY_RUNNER_ASSUME_ROOT=1 '
        f'RUNNER_ROOT="{prefix}/srv/actions-runner" '
        f'SUDOERS_PATH="{prefix}/etc/sudoers.d/autophagy-deploy-runner" '
        f'bash "{_PROVISION}"\n'
        for _ in range(times)
    )
    return subprocess.run(
        ("bash", "-c", shim + calls), capture_output=True, text=True, check=False
    )


def _calls(prefix: Path) -> str:
    log = prefix / "calls.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_sudoers_grants_exactly_one_command() -> None:
    lines = [
        line for line in _SUDOERS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(lines) == 1, lines


def test_sudoers_names_the_helper_with_no_arguments_and_no_wildcard() -> None:
    """A wildcard would turn one fixed command back into a command family."""
    stanza = _SUDOERS.read_text(encoding="utf-8")
    assert f"deploy-runner ALL=(root) NOPASSWD: {_HELPER}" in stanza
    assert "*" not in stanza
    assert "ALL\n" not in stanza and "NOPASSWD: ALL" not in stanza


def test_sudoers_targets_the_same_helper_the_converge_provisioner_installs() -> None:
    """Two files naming the privileged path must not drift apart."""
    assert _HELPER in _CONVERGE_PROVISION.read_text(encoding="utf-8")


def test_provisioner_creates_the_account_without_supplementary_groups(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _calls(tmp_path)
    assert "useradd " in calls
    for group in _FORBIDDEN_GROUPS:
        assert f"-G {group}" not in calls and f",{group}" not in calls, group


def test_provisioner_never_adds_the_account_to_a_group(tmp_path: Path) -> None:
    """usermod -aG / gpasswd -a are how an account quietly becomes root-equivalent."""
    assert _run(tmp_path).returncode == 0
    calls = _calls(tmp_path)
    assert "usermod" not in calls
    assert "gpasswd" not in calls


def test_runner_tree_is_root_owned_with_only_work_and_log_writable(tmp_path: Path) -> None:
    """Writable runner binaries or hook config would let a workflow persist itself."""
    assert _run(tmp_path).returncode == 0
    root = tmp_path / "srv" / "actions-runner"
    assert root.is_dir()
    assert (root / "_work").is_dir()
    calls = _calls(tmp_path)
    assert "chown" in calls and "_work" in calls
    assert f"chown deploy-runner:deploy-runner {root}\n" not in calls


def test_sudoers_is_validated_before_being_trusted(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 0
    assert "visudo" in _calls(tmp_path)


def test_provisioning_is_idempotent(tmp_path: Path) -> None:
    assert _run(tmp_path, times=2).returncode == 0


def test_it_is_a_separate_file_and_does_not_touch_bootstrap_accounts() -> None:
    """bootstrap-accounts.sh is claimed as another plan's sole source change."""
    assert _PROVISION.is_file()
    assert "bootstrap-accounts" not in _PROVISION.read_text(encoding="utf-8")
