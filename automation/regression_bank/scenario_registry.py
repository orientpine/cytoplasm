"""Validate and deduplicate W6-2 scenarios against the existing E2E bank contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from tests.e2e.drivers.judge_expectations import parse_yaml_subset


SCENARIO_ID: Final = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
CASE_ID: Final = re.compile(r"[a-z][a-z0-9_]*")
DRIVER: Final = re.compile(r"tests/e2e/drivers/[a-z0-9_/-]+\.sh")
IGNORED_SIGNATURE_FIELDS: Final = frozenset({"id", "title", "steps", "fault"})


class ScenarioValidationError(RuntimeError):
    """Explain why an incoming repair scenario cannot join the regression bank."""


class RegistrationStatus(StrEnum):
    """Whether a scenario changed the bank or merged into an equivalent scenario."""

    ADDED = "added"
    MERGED = "merged"


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """The validated identity and normalized semantic signature of one scenario."""

    scenario_id: str
    signature: str


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """The stable result returned to W6-2 after one registration attempt."""

    status: RegistrationStatus
    path: Path
    signature: str


@dataclass(frozen=True, slots=True)
class ScenarioRegistry:
    """Own the single scenario directory used by ``tests/e2e/run_bank.sh``."""

    repo_root: Path

    @property
    def scenarios_dir(self) -> Path:
        """Locate the unchanged E2E runner's scenario discovery directory."""
        return self.repo_root / "tests/e2e/scenarios"

    def register(self, source: Path) -> RegistrationResult:
        """Validate an incoming YAML, then add it once or merge it by judged semantics."""
        incoming = self.validate(source)
        target = self.scenarios_dir / f"{incoming.scenario_id}.yaml"
        if target.is_file():
            existing = self.validate(target)
            if existing.signature == incoming.signature:
                return RegistrationResult(RegistrationStatus.MERGED, target, existing.signature)
            raise ScenarioValidationError(f"bank filename collision: {target.name}")
        for candidate in sorted(self.scenarios_dir.glob("*.yaml")):
            existing = self.validate(candidate)
            if existing.signature == incoming.signature:
                return RegistrationResult(RegistrationStatus.MERGED, candidate, existing.signature)
        self.scenarios_dir.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(source, target)
        target.chmod(0o644)
        return RegistrationResult(RegistrationStatus.ADDED, target, incoming.signature)

    def validate(self, source: Path) -> ScenarioSpec:
        """Enforce runner naming plus every shape that the shared judge consumes."""
        if source.suffix != ".yaml" or not source.is_file():
            raise ScenarioValidationError("scenario must be a readable .yaml file")
        try:
            scenario = parse_yaml_subset(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ScenarioValidationError(f"scenario YAML cannot be parsed: {error}") from error
        scenario_id = self._required_string(scenario, "id", "scenario")
        if SCENARIO_ID.fullmatch(scenario_id) is None:
            raise ScenarioValidationError("scenario id must use lowercase hyphenated naming")
        if source.parent == self.scenarios_dir and source.name != f"{scenario_id}.yaml":
            raise ScenarioValidationError("scenario filename must equal its id plus .yaml")
        version = scenario.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version != 1:
            raise ScenarioValidationError("scenario version must be integer 1")
        _ = self._required_string(scenario, "title", "scenario")
        driver = self._required_string(scenario, "driver", "scenario")
        if DRIVER.fullmatch(driver) is None or not (self.repo_root / driver).is_file():
            raise ScenarioValidationError("scenario driver must name an existing tests/e2e/drivers/*.sh file")
        cases = scenario.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ScenarioValidationError("scenario cases must be a non-empty list")
        case_ids: set[str] = set()
        for index, case in enumerate(cases, start=1):
            if not isinstance(case, dict):
                raise ScenarioValidationError(f"case {index} must be a mapping")
            case_id = self._required_string(case, "id", f"case {index}")
            if CASE_ID.fullmatch(case_id) is None or case_id in case_ids:
                raise ScenarioValidationError(f"case {index} id must be unique lowercase snake_case")
            case_ids.add(case_id)
            _ = self._required_string(case, "kind", f"case {case_id}")
            steps = case.get("steps")
            if not isinstance(steps, list) or not steps or not all(
                isinstance(step, str) and step.strip() for step in steps
            ):
                raise ScenarioValidationError(f"case {case_id} steps must be a non-empty string list")
            expect = case.get("expect")
            if not isinstance(expect, dict):
                raise ScenarioValidationError(f"case {case_id} expect must be a flat mapping")
            for key, value in expect.items():
                if not isinstance(key, str) or not self._scalar(value):
                    raise ScenarioValidationError(f"case {case_id} expect must contain scalar keys and values")
        return ScenarioSpec(scenario_id, self._signature(scenario))

    @staticmethod
    def _required_string(mapping, key: str, label: str) -> str:
        value = mapping.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ScenarioValidationError(f"{label} {key} must be a non-empty string")
        return value

    @staticmethod
    def _scalar(value) -> bool:
        return value is None or isinstance(value, str | int | float | bool)

    @staticmethod
    def _signature(scenario) -> str:
        normalized = {
            key: value
            for key, value in scenario.items()
            if key not in IGNORED_SIGNATURE_FIELDS
        }
        normalized["cases"] = [
            {
                key: value
                for key, value in case.items()
                if key not in IGNORED_SIGNATURE_FIELDS
            }
            for case in sorted(scenario["cases"], key=lambda case: case["id"])
        ]
        canonical = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    """Register one repair YAML and emit only its non-sensitive merge result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--repo-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = ScenarioRegistry(arguments.repo_root).register(arguments.source)
    except ScenarioValidationError as error:
        print(f"scenario rejected: {error}", file=sys.stderr)
        return 2
    print(f"scenario {result.status}: {result.path.name} signature={result.signature[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
