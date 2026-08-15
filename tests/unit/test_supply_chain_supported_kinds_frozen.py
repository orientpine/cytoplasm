from __future__ import annotations

from automation.supply_chain_plan import SUPPORTED_KINDS


def test_supported_kinds_remains_skill_deploy_only() -> None:
    assert SUPPORTED_KINDS == frozenset({"skill-deploy"}), (
        "Automatic-resume kinds are frozen; decide and satisfy the prerequisites in "
        "docs/guide/공급망-자동재개-설계.md before changing SUPPORTED_KINDS."
    )
