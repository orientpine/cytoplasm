"""provision-release-store.sh: idempotently install the root-only release helper
and create the release store root — WITHOUT touching bootstrap-accounts.sh (C2).

The provisioner mirrors provision-skill-roots.sh: create /srv/autophagy-agent-releases
(0755 root:root), install automation/release_store.py at
/usr/local/libexec/autophagy-install-release, install the sudoers stanza. It is a
new file precisely so it does not collide with repair-report-rollout's sole claimed
source file (automation/bootstrap-accounts.sh).

Hermetic: run the script with a fake install prefix under tmp_path and an install()
shim that drops -o/-g ownership flags (no root); assert idempotency and the helper
lands exactly once.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PROVISION = _REPO / "automation" / "provision-release-store.sh"


def _run(tmp_path: Path, *, times: int = 1) -> subprocess.CompletedProcess[str]:
    """Run the provisioner against a fake prefix, stripping ownership flags."""
    prefix = tmp_path / "prefix"
    shim = (
        "install() {\n"
        "  local -a args=()\n"
        "  while (( $# > 0 )); do\n"
        '    case "$1" in\n'
        "      -o|-g) shift 2 ;;\n"
        '      *) args+=("$1"); shift ;;\n'
        "    esac\n"
        "  done\n"
        '  command install "${args[@]}"\n'
        "}\n"
        "visudo() { return 0; }\n"           # no real sudoers validation off-node
        'id() { return 0; }\n'               # accounts absent in the harness
        "export -f install visudo id\n"     # make the shims visible to the child bash
    )
    calls = "".join(
        "".join((
            f'RELEASE_STORE_ROOT="{prefix}/srv/autophagy-agent-releases" ',
            f'HELPER_PATH="{prefix}/usr/local/libexec/autophagy-install-release" ',
            f'GATEWAY_HELPER_PATH="{prefix}/usr/local/libexec/autophagy-gateway-pair" ',
            f'SUDOERS_PATH="{prefix}/etc/sudoers.d/autophagy-release-store" ',
            f'RELEASE_PROVISION_ASSUME_ROOT=1 bash "{_PROVISION}"\n',
        ))
        for _ in range(times)
    )
    script = shim + calls
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False
    )


def test_provision_installs_helper_and_store_root(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    prefix = tmp_path / "prefix"
    assert (prefix / "srv" / "autophagy-agent-releases").is_dir()
    helper = prefix / "usr" / "local" / "libexec" / "autophagy-install-release"
    assert helper.is_file()
    # The helper is the release_store.py content.
    assert "RELEASE-STORE-BLOCK" in helper.read_text(encoding="utf-8")
    gateway_helper = prefix / "usr" / "local" / "libexec" / "autophagy-gateway-pair"
    assert gateway_helper.is_file()
    assert "$NODE_" not in gateway_helper.read_text(encoding="utf-8")
    assert (prefix / "etc" / "sudoers.d" / "autophagy-release-store").is_file()


def test_provision_is_idempotent(tmp_path: Path) -> None:
    result = _run(tmp_path, times=2)
    assert result.returncode == 0, result.stdout + result.stderr
    helper_dir = tmp_path / "prefix" / "usr" / "local" / "libexec"
    assert {path.name for path in helper_dir.iterdir()} == {
        "autophagy-gateway-pair", "autophagy-install-release", "release_provenance.py",
        "automation", "configs",
    }


def test_provision_refuses_without_root(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    script = "".join((
        "install() { command install \"$@\"; }\n",
        f'RELEASE_STORE_ROOT="{prefix}/srv/autophagy-agent-releases" ',
        f'HELPER_PATH="{prefix}/usr/local/libexec/autophagy-install-release" ',
        f'GATEWAY_HELPER_PATH="{prefix}/usr/local/libexec/autophagy-gateway-pair" ',
        f'SUDOERS_PATH="{prefix}/etc/sudoers.d/autophagy-release-store" ',
        f'RELEASE_PROVISION_ASSUME_ROOT=0 bash "{_PROVISION}"\n',
    ))
    result = subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "run as root" in (result.stdout + result.stderr)


def test_provisioned_helper_installs_the_canonical_release_layout(tmp_path: Path) -> None:
    # Integration lock for the 2026-07-31 rollout bug: after provision, run the
    # INSTALLED helper with --store-root <prefix>/srv and assert it lands the
    # canonical autophagy-agent-releases/<sha> + autophagy-agent-current, never the
    # generic /srv/releases + /srv/current.
    import io
    import tarfile

    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    prefix = tmp_path / "prefix"
    helper = prefix / "usr" / "local" / "libexec" / "autophagy-install-release"
    store_parent = prefix / "srv"
    store_parent.mkdir(parents=True, exist_ok=True)

    sha = "a" * 40
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"x\n"
        info = tarfile.TarInfo("automation/peer_attest.py")
        info.size = len(data)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))

    installed = subprocess.run(
        ("python3", str(helper), "install", "--sha", sha, "--store-root", str(store_parent)),
        input=buf.getvalue(), capture_output=True, check=False,
    )
    assert installed.returncode == 0, installed.stderr.decode()
    assert (store_parent / "autophagy-agent-releases" / sha).is_dir()
    assert (store_parent / "autophagy-agent-current").is_symlink()
    # the generic layout must never appear
    assert not (store_parent / "releases").exists()
    assert not (store_parent / "current").exists()
