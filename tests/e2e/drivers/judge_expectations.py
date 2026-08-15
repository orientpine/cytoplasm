"""Generic E2E judge: scenario YAML `expect` blocks vs actuator observations.

Usage:
    python3 judge_expectations.py <scenario.yaml> <observations.json> [report.md]

Contract (reusable by every wave's scenario driver):
  * the scenario file declares `cases:` — a list of maps with `id` and a flat
    `expect` map (scalars only: int/bool/null/string);
  * observations.json maps case id -> flat observation map;
  * a case PASSES iff every expect key exists in the observations and matches
    by exact equality. Any mismatch (including a missing case) is reported
    with the offending step key — that is the failure-isolation surface.

Exit code: 0 = every case passed, 1 = at least one mismatch.

Dependency-free on purpose (agent gateway venvs lack PyYAML): parses a strict
YAML subset — 2-space indents, `key: value` maps, `- ` list items, full-line
comments, no block scalars/anchors/inline collections.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _scalar(raw: str) -> object:
    text = raw.strip()
    if text in {"null", "~"}:
        return None
    if text in {"true", "false"}:
        return text == "true"
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: list[str], start: int, indent: int) -> tuple[object, int]:
    """Parse one mapping or list block starting at `start` with `indent`."""
    is_list = lines[start].lstrip().startswith("- ")
    result: object = [] if is_list else {}
    index = start
    while index < len(lines):
        line = lines[index]
        if _indent(line) < indent:
            break
        if _indent(line) > indent:
            raise ValueError(f"unexpected indent at line {index + 1}: {line!r}")
        body = line.strip()
        if is_list:
            if not body.startswith("- "):
                break
            item = body[2:]
            quoted = item.startswith(('"', "'"))
            if not quoted and (item.endswith(":") or ": " in item):
                # list of maps: re-parse the item as the first key of a map
                lines[index] = " " * (indent + 2) + item
                value, index = _parse_block(lines, index, indent + 2)
                result.append(value)
            else:
                result.append(_scalar(item))
                index += 1
        else:
            key, _, rest = body.partition(":")
            if rest.strip():
                result[key.strip()] = _scalar(rest)
                index += 1
            else:
                index += 1
                if index < len(lines) and _indent(lines[index]) > indent:
                    value, index = _parse_block(lines, index, _indent(lines[index]))
                else:
                    value = None
                result[key.strip()] = value
    return result, index


def parse_yaml_subset(text: str) -> dict:
    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    parsed, _ = _parse_block(lines, 0, 0)
    if not isinstance(parsed, dict):
        raise ValueError("scenario root must be a mapping")
    return parsed


def judge(scenario: dict, observations: dict) -> tuple[bool, list[str]]:
    lines: list[str] = []
    all_pass = True
    for case in scenario.get("cases", []):
        case_id = str(case.get("id"))
        expect = case.get("expect") or {}
        observed = observations.get(case_id)
        if observed is None:
            all_pass = False
            lines.append(f"FAIL {case_id}: no observations emitted for this case")
            continue
        mismatches = [
            f"    step `{key}`: expected {expected!r}, observed {observed.get(key)!r}"
            for key, expected in expect.items()
            if observed.get(key) != expected
        ]
        if mismatches:
            all_pass = False
            lines.append(f"FAIL {case_id} ({case.get('kind', '?')}) — isolated failing step(s):")
            lines.extend(mismatches)
        else:
            lines.append(f"PASS {case_id} ({case.get('kind', '?')}) — {len(expect)} observables matched")
    return all_pass, lines


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    scenario = parse_yaml_subset(Path(argv[1]).read_text(encoding="utf-8"))
    observations = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    ok, lines = judge(scenario, observations)
    verdict = "BANK-CASE-VERDICT: ALL PASS" if ok else "BANK-CASE-VERDICT: FAILURES PRESENT"
    output = "\n".join([f"scenario: {scenario.get('id')}", *lines, verdict])
    print(output)
    if len(argv) > 3:
        Path(argv[3]).write_text(output + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
