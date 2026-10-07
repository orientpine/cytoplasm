"""RCB todo 19: the standing runtime-package table and the compat carrier match declarations.

The standing probe (`automation/runtime_package_probe.sh`) reads
`configs/runtime-package-manifest.txt`; the release observer reads the v2 declarations. Two
lists of the same trees drift unless every table row names a declared tree with the same
account, source, destination and (when the row narrows it) file list.

The vendor-patch carrier `~/.hermes/hermes-compat/automation/hermes_compat` is held (an owner
action deploys it), but its `files=` must still be exactly what the two patch deployers stage
into `staging/automation/hermes_compat` — otherwise the observer compares the wrong set.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from automation.deploy_declarations import Declaration, all_declarations

_REPO: Final = Path(__file__).resolve().parents[2]
_TABLE: Final = _REPO / "configs" / "runtime-package-manifest.txt"
_COMPAT: Final = _REPO / "automation" / "hermes_compat"
_PATCH_DEPLOYERS: Final = ("deploy-owner-dm.sh", "deploy-public-message-policy.sh")
_CARRIER_DEST: Final = ".hermes/hermes-compat/automation/hermes_compat"
_CARRIER_TAR: Final = re.compile(
    r'tar -C "\$hc" -czf - (?P<files>[^\\\n]+?)\s*\\\n[^\n]*staging/automation/hermes_compat'
)


def unmatched_table_rows(table_text: str, declarations: tuple[Declaration, ...]) -> list[str]:
    """Data rows of the runtime-package table that no v2 tree declaration matches."""
    trees = [d for d in declarations if d.kind == "tree" and not d.legacy]
    missing: list[str] = []
    for line in table_text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        account, source, runtime = fields[0], fields[1], fields[2]
        files = tuple(fields[6].split(",")) if len(fields) == 7 else None
        if not any(
            (d.account, d.source, d.destination) == (account, source, runtime)
            and (files is None or tuple(d.attr("files").split(",")) == files)
            for d in trees
        ):
            missing.append(line)
    return missing


def staged_carrier_files(deployer_texts: tuple[str, ...]) -> frozenset[str]:
    """Union of the modules the patch deployers stage into the carrier package."""
    staged: set[str] = set()
    for text in deployer_texts:
        matches = list(_CARRIER_TAR.finditer(text))
        assert len(matches) == 1, f"expected one carrier staging tar, found {len(matches)}"
        staged.update(matches[0].group("files").split())
    return frozenset(staged)


def _carrier() -> Declaration:
    rows = [d for d in all_declarations(_REPO) if d.destination == _CARRIER_DEST]
    assert len(rows) == 1, rows
    return rows[0]


def _deployer_texts() -> tuple[str, ...]:
    return tuple((_COMPAT / name).read_text(encoding="utf-8") for name in _PATCH_DEPLOYERS)


def test_every_manifest_row_has_a_matching_tree_declaration() -> None:
    table = _TABLE.read_text(encoding="utf-8")
    assert [line for line in table.splitlines() if line and not line.startswith("#")]
    assert unmatched_table_rows(table, all_declarations(_REPO)) == []


def test_compat_carrier_declaration_matches_the_patch_deployers() -> None:
    carrier = _carrier()
    assert (carrier.account, carrier.source, carrier.kind, carrier.policy) == (
        "agent", "automation/hermes_compat", "tree", "held",
    )
    declared = carrier.attr("files").split(",")
    assert len(declared) == len(set(declared))
    assert frozenset(declared) == staged_carrier_files(_deployer_texts())


def test_a_manifest_row_without_a_declaration_is_rejected() -> None:
    extra = "agent|automation/undeclared|.hermes/undeclared_runtime|required"
    table = _TABLE.read_text(encoding="utf-8") + extra + "\n"
    assert unmatched_table_rows(table, all_declarations(_REPO)) == [extra]
    narrowed = "agent|automation/regression_bank|.hermes/regression_bank_runtime|required|default|python|bank_state.py"
    assert unmatched_table_rows(narrowed + "\n", all_declarations(_REPO)) == [narrowed]


def test_a_carrier_module_missing_from_the_declaration_is_rejected() -> None:
    texts = list(_deployer_texts())
    texts[0] = texts[0].replace(
        "receipt_apply.py \\\n  | ssh", "receipt_apply.py extra_carrier.py \\\n  | ssh", 1
    )
    assert texts[0] != _deployer_texts()[0], "mutation did not apply"
    staged = staged_carrier_files(tuple(texts))
    declared = frozenset(_carrier().attr("files").split(","))
    assert staged - declared == {"extra_carrier.py"}
