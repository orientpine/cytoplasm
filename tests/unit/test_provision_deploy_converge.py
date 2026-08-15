"""The privileged convergence helper: root-owned, argument-less, tree-independent.

MD-1. A GitHub Actions workflow (and any future automated trigger) must be able to
converge prod without being handed a shell that can do anything else. The helper is
therefore the ONLY thing the runner may sudo, and it takes no arguments: it resolves
the trusted public release tag itself, so a caller cannot aim it at a sha of their choosing.

Two properties are load-bearing and are asserted from the source text, because they
are exactly what a later well-meaning edit would break:

* The privileged path must NOT execute anything out of ``/srv/autophagy-agent-current``.
  That tree is replaced by every merge — running it under sudo would turn "merge a PR"
  into "run arbitrary code as root's helper", which is the escalation the whole design
  exists to prevent.
* It must take the SAME lock file as ``converge-release-runtime.sh``. Two convergence
  paths with different locks can install out of order and flip the runtime BACKWARDS.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HELPER_SRC = _REPO / "automation" / "converge_origin_main.sh"
_PROVISION = _REPO / "automation" / "provision-deploy-converge.sh"
_CONVERGE = _REPO / "automation" / "converge-release-runtime.sh"

_SHARED_LOCK = "/tmp/autophagy-release-converge.lock"


def _provision(prefix: Path, *, times: int = 1) -> subprocess.CompletedProcess[str]:
    """Run the provisioner off-node.

    ``install -o root -g root`` needs real root, so the harness shims ``install`` to
    drop the ownership flags — the same seam ``test_provision_release_store.py`` uses.
    Ownership itself is a node-side property, verified by MD3-S7 in the work plan.
    """
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
        "export -f install\n"
    )
    call = " ".join(
        (
            "DEPLOY_CONVERGE_ASSUME_ROOT=1",
            f'HELPER_PATH="{prefix}/usr/local/libexec/autophagy-converge-origin-main"',
            f'HELPER_LIBDIR="{prefix}/usr/local/libexec/autophagy-converge.d"',
            f'LOCK_DIR="{prefix}/srv/autophagy-private/locks"',
            f'bash "{_PROVISION}"',
        )
    )
    calls = f"{call}\n" * times
    return subprocess.run(
        ("bash", "-c", shim + calls), capture_output=True, text=True, check=False
    )


def test_installs_the_helper_root_owned_and_executable(tmp_path: Path) -> None:
    result = _provision(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    helper = tmp_path / "usr/local/libexec/autophagy-converge-origin-main"
    assert helper.is_file()
    assert oct(helper.stat().st_mode)[-3:] == "755"


def test_installs_its_dependencies_beside_it_not_from_the_mutable_tree(tmp_path: Path) -> None:
    """The helper's dependencies are installed BY VALUE under a root-owned libdir.

    Sourcing them from the deploy checkout or the current release would reintroduce
    exactly the escalation this design removes: a merge would change privileged code.
    """
    assert _provision(tmp_path).returncode == 0
    libdir = tmp_path / "usr/local/libexec/autophagy-converge.d"
    assert (libdir / "origin_snapshot.sh").is_file()
    assert (libdir / "release_store.py").is_file()
    assert (libdir / "release_provenance.py").is_file()
    package = libdir / "automation"
    assert (package / "__init__.py").is_file()
    assert (package / "git_tag_signature.py").is_file()
    assert (package / "update_trust.py").is_file()
    # The verifier imports the C1 anti-rollback floor at module scope, so a libdir
    # missing it turns every convergence into an ImportError under sudo.
    assert (package / "update_trust_state.py").is_file()
    assert (package / "node_config.py").is_file()
    assert (package / "node.example.toml").is_file()


def test_provisioning_is_idempotent(tmp_path: Path) -> None:
    assert _provision(tmp_path).returncode == 0
    first = (tmp_path / "usr/local/libexec/autophagy-converge-origin-main").read_bytes()
    assert _provision(tmp_path).returncode == 0
    assert (tmp_path / "usr/local/libexec/autophagy-converge-origin-main").read_bytes() == first


def test_the_target_sha_comes_from_update_trust_not_from_the_caller() -> None:
    """A caller must not be able to choose what gets installed.

    Both channels have to be closed: the environment variable the interactive path
    uses (``RELEASE_EXPECTED_SHA``) is cleared, and the assignment that decides the
    target invokes the root-owned signature verifier — not a positional argument.
    """
    text = _HELPER_SRC.read_text(encoding="utf-8")
    assert "unset RELEASE_EXPECTED_SHA" in text
    target_assignments = [
        line for line in text.splitlines() if line.lstrip().startswith("target=")
    ]
    assert len(target_assignments) == 1, target_assignments
    assert "UPDATE_TRUST" in target_assignments[0]
    assert "refs/heads/main" not in target_assignments[0]


def test_helper_resets_the_inherited_environment() -> None:
    """PATH/PYTHONPATH/GIT_*/SSH_*/LD_* inherited from a caller are an injection seam."""
    text = _HELPER_SRC.read_text(encoding="utf-8")
    assert "export PATH=" in text
    for name in ("PYTHONPATH", "GIT_DIR", "GIT_WORK_TREE", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        assert name in text, f"{name} must be explicitly neutralised"


def test_privileged_path_never_executes_the_mutable_runtime_tree() -> None:
    text = _HELPER_SRC.read_text(encoding="utf-8")
    assert "/srv/autophagy-agent-current" not in text
    assert "RELEASE_CURRENT" not in text


def test_helper_shares_the_lock_with_converge_release_runtime() -> None:
    """Different lock files = two convergences can flip the runtime out of order."""
    assert _SHARED_LOCK in _HELPER_SRC.read_text(encoding="utf-8")
    assert _SHARED_LOCK in _CONVERGE.read_text(encoding="utf-8")


def test_helper_resolves_the_signed_public_release_itself() -> None:
    text = _HELPER_SRC.read_text(encoding="utf-8")
    assert 'UPDATE_TRUST="$LIBDIR/automation/update_trust.py"' in text
    assert 'ALLOWED_SIGNERS="/etc/autophagy/update-allowed-signers"' in text
    assert "resolve --mirror" in text


def test_helper_applies_the_bound_update_channel_to_verification_and_snapshot() -> None:
    text = _HELPER_SRC.read_text(encoding="utf-8")

    assert "deploy-reconcile/update-channel.json" in text
    assert "GIT_CONFIG_KEY_0=remote.origin.url" in text
    assert text.count('"${remote_env[@]}"') == 2
