from __future__ import annotations

from pathlib import Path

import pytest

from automation.regression_bank.scenario_registry import (
    RegistrationStatus,
    ScenarioRegistry,
    ScenarioValidationError,
)


def write_driver(root: Path) -> None:
    driver = root / "tests/e2e/drivers/w6_local.sh"
    driver.parent.mkdir(parents=True)
    driver.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    driver.chmod(0o755)


def write_scenario(path: Path, scenario_id: str, case_id: str = "repair_regression") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                "version: 1",
                f"id: {scenario_id}",
                'title: "W6 repair regression"',
                "driver: tests/e2e/drivers/w6_local.sh",
                "cases:",
                f"  - id: {case_id}",
                "    kind: failure",
                "    steps:",
                '      - "assert the repaired failure stays fixed"',
                "    expect:",
                "      repair_exit: 0",
                "      error: null",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_register_when_scenario_matches_bank_contract_then_adds_to_bank(tmp_path: Path) -> None:
    # Given: a repair-produced YAML and the driver required by the existing bank contract.
    write_driver(tmp_path)
    source = tmp_path / "incoming/w6-repair-regression.yaml"
    write_scenario(source, "w6-repair-regression")
    registry = ScenarioRegistry(tmp_path)

    # When: W6-2 registers the scenario through the bank registry.
    result = registry.register(source)

    # Then: the valid scenario is added under its contract-bound bank filename.
    assert result.status is RegistrationStatus.ADDED
    assert result.path == tmp_path / "tests/e2e/scenarios/w6-repair-regression.yaml"
    assert result.path.is_file()


def test_register_when_normalized_signature_already_exists_then_merges(tmp_path: Path) -> None:
    # Given: two differently named repair scenarios with identical judged semantics.
    write_driver(tmp_path)
    registry = ScenarioRegistry(tmp_path)
    first = tmp_path / "incoming/w6-first-regression.yaml"
    duplicate = tmp_path / "incoming/w6-second-regression.yaml"
    write_scenario(first, "w6-first-regression")
    write_scenario(duplicate, "w6-second-regression")
    added = registry.register(first)

    # When: the second scenario is registered.
    merged = registry.register(duplicate)

    # Then: it resolves to the prior scenario and does not grow the bank twice.
    assert added.status is RegistrationStatus.ADDED
    assert merged.status is RegistrationStatus.MERGED
    assert merged.path == added.path
    assert list((tmp_path / "tests/e2e/scenarios").glob("*.yaml")) == [added.path]


def test_register_when_yaml_violates_naming_contract_then_rejects_with_reason(tmp_path: Path) -> None:
    # Given: a source YAML whose id cannot be a bank scenario filename.
    write_driver(tmp_path)
    source = tmp_path / "incoming/invalid.yaml"
    write_scenario(source, "W6 Invalid")

    # When: the registry validates the repair-produced scenario.
    with pytest.raises(ScenarioValidationError, match="scenario id"):
        ScenarioRegistry(tmp_path).register(source)

    # Then: no invalid YAML reaches the bank directory.
    assert not (tmp_path / "tests/e2e/scenarios").exists()
