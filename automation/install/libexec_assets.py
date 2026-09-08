"""Root-owned helper programs the installer places under ``libexec``.

Split out of ``assets.py`` on 2026-09-07. A landed FS3 settlement asserts that file stays
at least twenty lines below the 250 pure-LOC ceiling, and the follow-up behind it predicted
the reason exactly: "다음 always-on 유닛·helper 추가가 F2 게이트를 깨뜨린다". Adding the
converger's package was that next helper addition, and the settlement's own remedy was to
split by file kind rather than to register an exception — a registered exception would not
satisfy the probe, which asserts the count unconditionally.

The two groups live together because they share one contract: root owns them, and the
privileged converger executes ONLY these copies, never the mutable runtime tree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

from automation.install.plan import FileSpec
from automation.node_asset_renderer import render_asset
from automation.node_config import NodeConfig

#: Programs installed directly under libexec. (release source, installed name, mode)
HELPER_SOURCES: Final = (
    ("automation/release_store.py", "autophagy-install-release", 0o755),
    ("automation/release_provenance.py", "release_provenance.py", 0o644),
    ("automation/skill_store.py", "autophagy-install-skill", 0o755),
    ("automation/converge_origin_main.sh", "autophagy-converge-origin-main", 0o755),
    ("automation/libexec/autophagy-resume-deploy", "autophagy-resume-deploy", 0o755),
)

#: Everything the privileged converger imports, mirrored from
#: automation/provision-deploy-converge.sh. A module absent here is not a degraded install
#: but a first-convergence SYNC-BLOCK: the node cannot produce a release at all
#: (2026-09-07, a node the installer built alone). The provisioner has placed all nine
#: since it was written; the installer placed three, and nothing compared the two.
#: tests/unit/test_install_converge_parity.py compares them now, so the source of truth is
#: the other installer rather than a third hand-written list.
CONVERGE_HELPERS: Final = (
    ("automation/origin_snapshot.sh", "origin_snapshot.sh", 0o755),
    ("automation/release_store.py", "release_store.py", 0o755),
    ("automation/release_provenance.py", "release_provenance.py", 0o644),
    ("automation/__init__.py", "automation/__init__.py", 0o644),
    ("automation/git_tag_signature.py", "automation/git_tag_signature.py", 0o644),
    ("automation/update_trust.py", "automation/update_trust.py", 0o755),
    ("automation/update_trust_state.py", "automation/update_trust_state.py", 0o644),
    ("automation/node_config.py", "automation/node_config.py", 0o644),
    ("configs/node.example.toml", "automation/node.example.toml", 0o644),
)


def libexec_files(repo_root: Path, config: NodeConfig) -> tuple[FileSpec, ...]:
    """Every root-owned libexec program, including the converger's package copies."""
    root = "root"
    converge_dir = config.libexec_dir / "autophagy-converge.d"
    placed = [
        FileSpec(
            config.libexec_dir / name,
            render_asset(repo_root / relative, config),
            mode,
            root,
            root,
        )
        for relative, name, mode in HELPER_SOURCES
    ]
    placed.extend(
        FileSpec(
            converge_dir / name,
            render_asset(repo_root / relative, config),
            mode,
            root,
            root,
        )
        for relative, name, mode in CONVERGE_HELPERS
    )
    return tuple(placed)
