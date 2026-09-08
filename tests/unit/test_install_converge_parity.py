"""The two install paths must agree on what the privileged converger may import.

``automation/converge_origin_main.sh`` executes ONLY the root-owned copies installed
beside it — never the mutable runtime tree — so every module it reaches has to be in
``<libexec>/autophagy-converge.d/``.  Two things install that directory:
``automation/provision-deploy-converge.sh`` (which existing nodes were built with) and
``automation/install/assets.py`` (the newer installer).  They drifted: the provisioner
placed nine files, the installer three, and the six missing ones are exactly the Python
package the verifier imports.

A node installed by the installer alone therefore failed its FIRST convergence with
``SYNC-BLOCK: missing root-owned update verifier`` and could not produce a release at all
(2026-09-07, third-party installation; ``docs/troubleshooting/신규-노드-설치-공백.md`` §3).
Nothing compared the two sets, so the gap was invisible until someone used the newer path
on a clean host.

This pins the sets against each other rather than against a hand-written list: a list
would be a third place to drift.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Final

from automation.install.assets import build_inputs
from automation.node_config import default_node_config

_REPO: Final = Path(__file__).resolve().parents[2]
_PROVISIONER: Final = _REPO / "automation" / "provision-deploy-converge.sh"

#: `install -m MODE -o root -g root "$SRC" "$HELPER_LIBDIR/DEST"`.  Directory creation
#: (`install -d`) does not match, so the `automation/` directory entry stays out.
_INSTALL_LINE: Final = re.compile(
    r'install -m (0[0-7]{3}) -o root -g root "[^"]+" "\$HELPER_LIBDIR/([^"]+)"'
)


def _update_trust_key() -> str:
    algorithm = b"ssh-ed25519"
    blob = len(algorithm).to_bytes(4, "big") + algorithm
    blob += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} converge-parity"


def _provisioner_converge_files() -> frozenset[tuple[str, int]]:
    text = _PROVISIONER.read_text(encoding="utf-8")
    return frozenset(
        (destination, int(mode, 8)) for mode, destination in _INSTALL_LINE.findall(text)
    )


def _installer_converge_files() -> frozenset[tuple[str, int]]:
    config = default_node_config()
    inputs = build_inputs(_REPO, config, _update_trust_key())
    root = config.libexec_dir / "autophagy-converge.d"
    return frozenset(
        (spec.path.relative_to(root).as_posix(), spec.mode)
        for spec in inputs.files
        if root in spec.path.parents
    )


def test_both_install_paths_place_the_same_converge_helper_set() -> None:
    # Given: the two independent installers of the privileged converger's library.
    provisioner = _provisioner_converge_files()
    installer = _installer_converge_files()

    # Then: neither may place a file the other does not, at the same mode.
    assert provisioner, "provisioner install lines no longer parse"
    assert installer == provisioner, (
        f"missing from installer: {sorted(provisioner - installer)}; "
        f"extra in installer: {sorted(installer - provisioner)}"
    )


def test_the_verifier_the_converger_requires_is_installed_with_its_package() -> None:
    # Given: converge_origin_main.sh resolves the verifier under the package directory.
    helper = (_REPO / "automation" / "converge_origin_main.sh").read_text(encoding="utf-8")
    assert 'UPDATE_TRUST="$LIBDIR/automation/update_trust.py"' in helper

    # When: the installer's converge.d set is enumerated.
    placed = {destination for destination, _ in _installer_converge_files()}

    # Then: the verifier and every module its import graph needs are present, because a
    # partial package fails at import time with the same SYNC-BLOCK-shaped silence.
    assert {
        "automation/__init__.py",
        "automation/update_trust.py",
        "automation/git_tag_signature.py",
        "automation/update_trust_state.py",
        "automation/node_config.py",
        "automation/node.example.toml",
    } <= placed, sorted(placed)
