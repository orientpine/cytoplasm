#!/usr/bin/env python3
"""Deterministic, hash-bound security review for skill deployment.

``review`` records an immutable-in-practice JSONL verdict after inspecting a
skill tree.  It normally executes the scenario with an empty environment and
a DUMMY secret.  The deploy pipeline instead supplies its already-successful
peer-sandbox output with ``--scenario-output-file`` so untrusted code is never
executed a second time on the agent account.  ``check`` fails closed unless the
newest verdict for a skill is PASS for the requested content digest.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Sequence
from typing import Final

_DEFAULT_VERDICTS_PATH: Final = "~/.hermes/skill-gate/review-verdicts.jsonl"
_REQUIRED_CHECKS: Final = ("frontmatter", "scenario", "secret_scan", "content_digest")
_SECRET_PATTERNS: Final = (
    re.compile(rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{6,}(?![A-Za-z0-9_-])"),
    re.compile(rb"\bgh[po]_[A-Za-z0-9]{8,}\b"),
    re.compile(rb"\b(?:Bot|Bearer)[ \t]+(?=[A-Za-z0-9._~+/=-]*[0-9.])[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE),
    re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
)
_SKILL_NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")
_VERDICT_LINE: Final = re.compile(
    "".join(
        (
            r'^\{"checks": \{"content_digest": (?P<content_digest>true|false), ',
            r'"frontmatter": (?P<frontmatter>true|false), "scenario": (?P<scenario>true|false), ',
            r'"secret_scan": (?P<secret_scan>true|false)\}, "hash": ',
            r'"(?P<digest>[0-9a-f]{64}|unavailable)", "skill": "(?P<skill>[a-z0-9][a-z0-9-]{1,40})", ',
            r'"timestamp": "[^"]+", "verdict": "(?P<verdict>PASS|FAIL)"\}$',
        )
    )
)


@dataclass(frozen=True, slots=True)
class StoredVerdict:
    """The validated fields needed to enforce a persisted review verdict."""

    skill: str
    digest: str
    verdict: str
    checks_pass: bool


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Validated inputs for recording a deterministic review result."""

    skill: str
    skill_dir: Path
    digest: str
    scenario_output: Path | None


@dataclass(frozen=True, slots=True)
class CheckRequest:
    """Validated inputs for checking a persisted review result."""

    skill: str
    digest: str


def verdicts_path() -> Path:
    """Return the per-account review ledger, honoring the test-only override."""
    return Path(os.environ.get("REVIEW_VERDICTS_PATH", _DEFAULT_VERDICTS_PATH)).expanduser()


def _skill_files(skill_dir: Path) -> tuple[Path, ...]:
    if not skill_dir.is_dir():
        return ()
    files = (
        path
        for path in skill_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
        and "__pycache__" not in path.relative_to(skill_dir).parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    return tuple(sorted(files, key=lambda path: os.fsencode(path.relative_to(skill_dir).as_posix())))


def skill_digest(skill_dir: Path) -> str:
    """Return the deploy-skill.sh-compatible content digest for a skill tree."""
    digest = hashlib.sha256()
    for path in _skill_files(skill_dir):
        relative = f"./{path.relative_to(skill_dir).as_posix()}".encode("utf-8")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"  ")
        digest.update(relative)
        digest.update(b"\n")
    return digest.hexdigest()


def _frontmatter_passes(skill_dir: Path, skill: str) -> bool:
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return False
    parts = text.split("---\n", maxsplit=2)
    if not text.startswith("---\n") or len(parts) < 3:
        return False
    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, value = line.split(":", maxsplit=1)
            fields[key.strip()] = value.strip().strip('"')
    return fields.get("name") == skill and len(fields.get("description", "")) >= 10


def _scenario_passes(skill_dir: Path, output_file: Path | None) -> bool:
    resolved_skill_dir = skill_dir.resolve()
    scenario = resolved_skill_dir / "scripts" / "scenario.sh"
    if not scenario.is_file() or scenario.is_symlink():
        return False
    if output_file is not None:
        try:
            return "SCENARIO-PASS" in output_file.read_text(encoding="utf-8")
        except OSError:
            return False
    with tempfile.TemporaryDirectory(prefix="skill-review-") as home:
        environment = {
            "HOME": home,
            "PATH": "/usr/bin:/bin",
            "AUTOPHAGY_DEMO_SECRET": "DUMMY-skill-review",
        }
        try:
            completed = subprocess.run(
                ["bash", str(scenario)],
                cwd=resolved_skill_dir,
                env=environment,
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
    return completed.returncode == 0 and "SCENARIO-PASS" in completed.stdout


def _secret_scan_passes(skill_dir: Path) -> bool:
    for path in _skill_files(skill_dir):
        try:
            contents = path.read_bytes()
        except OSError:
            return False
        if any(pattern.search(contents) is not None for pattern in _SECRET_PATTERNS):
            return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(path: Path, record: dict[str, str | dict[str, bool]]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    path.chmod(0o600)


def _record_review(skill: str, skill_dir: Path, expected_digest: str, scenario_output: Path | None) -> int:
    try:
        actual_digest = skill_digest(skill_dir)
    except OSError:
        actual_digest = "unavailable"
    checks = {
        "frontmatter": _frontmatter_passes(skill_dir, skill),
        "scenario": _scenario_passes(skill_dir, scenario_output),
        "secret_scan": _secret_scan_passes(skill_dir),
        "content_digest": actual_digest == expected_digest,
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    _append_jsonl(
        verdicts_path(),
        {
            "skill": skill,
            "hash": actual_digest,
            "verdict": verdict,
            "checks": checks,
            "timestamp": _utc_now(),
        },
    )
    print(f"REVIEW-{verdict} skill={skill} sha256={actual_digest}")
    return 0 if verdict == "PASS" else 1


def _parse_verdict(line: str) -> StoredVerdict | None:
    matched = _VERDICT_LINE.fullmatch(line)
    if matched is None:
        return None
    checks_pass = all(matched.group(name) == "true" for name in _REQUIRED_CHECKS)
    return StoredVerdict(matched.group("skill"), matched.group("digest"), matched.group("verdict"), checks_pass)


def _newest_verdict(path: Path, skill: str) -> StoredVerdict | None:
    try:
        file_mode = stat.S_IMODE(path.stat().st_mode)
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if file_mode != 0o600:
        return None
    newest: StoredVerdict | None = None
    for line in lines:
        if not line:
            continue
        record = _parse_verdict(line)
        if record is None:
            return None
        if record.skill == skill:
            newest = record
    return newest


def _check(skill: str, digest: str) -> int:
    record = _newest_verdict(verdicts_path(), skill)
    if record is None or record.verdict != "PASS" or not record.checks_pass or record.digest != digest:
        print(f"REVIEW-BLOCKED skill={skill} sha256={digest}", file=sys.stderr)
        return 1
    print(f"REVIEW-PASS skill={skill} sha256={digest}")
    return 0


def _options(tokens: Sequence[str]) -> tuple[tuple[str, str], ...] | None:
    if len(tokens) % 2 != 0:
        return None
    pairs = tuple(zip(tokens[::2], tokens[1::2], strict=True))
    if any(not name.startswith("--") for name, _ in pairs) or len({name for name, _ in pairs}) != len(pairs):
        return None
    return pairs


def _option(pairs: tuple[tuple[str, str], ...], name: str) -> str | None:
    for actual_name, value in pairs:
        if actual_name == name:
            return value
    return None


def _parse_cli(argv: Sequence[str]) -> ReviewRequest | CheckRequest | None:
    if not argv:
        return None
    pairs = _options(argv[1:])
    if pairs is None:
        return None
    skill = _option(pairs, "--skill")
    digest = _option(pairs, "--hash")
    if skill is None or digest is None or _SKILL_NAME.fullmatch(skill) is None or _DIGEST.fullmatch(digest) is None:
        return None
    match argv[0]:
        case "review":
            skill_dir = _option(pairs, "--skill-dir")
            scenario = _option(pairs, "--scenario-output-file")
            if skill_dir is None or any(name not in {"--skill", "--hash", "--skill-dir", "--scenario-output-file"} for name, _ in pairs):
                return None
            return ReviewRequest(skill, Path(skill_dir), digest, Path(scenario) if scenario else None)
        case "check":
            if any(name not in {"--skill", "--hash"} for name, _ in pairs):
                return None
            return CheckRequest(skill, digest)
        case _:
            return None


def main(argv: Sequence[str] | None = None) -> int:
    """Run the review recorder or the fail-closed hash-bound verdict check."""
    request = _parse_cli(sys.argv[1:] if argv is None else argv)
    match request:
        case ReviewRequest(skill=skill, skill_dir=skill_dir, digest=digest, scenario_output=scenario_output):
            return _record_review(skill, skill_dir, digest, scenario_output)
        case CheckRequest(skill=skill, digest=digest):
            return _check(skill, digest)
        case None:
            print("usage: skill_review.py review|check --skill NAME --hash DIGEST", file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
