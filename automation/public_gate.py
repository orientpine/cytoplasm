"""Leak gate for a public development repository, run at commit, push and PR time.

Until the cut-over to public-first development, nothing reached the public repository
except through ``automation/public_export.sh``, which scanned a finished snapshot. Once
development happens in the public repository itself, every pushed branch, commit message
and PR body is published the moment it is pushed, so the same checks have to run before
the push, not at release time. This module is that check. It deliberately reuses the
topology rules of :mod:`automation.public_export_redaction` instead of keeping a copy.

Checks:
  * forbidden paths  - session evidence (``.omo``) and wave QA (``docs/qa``) never enter
                       the public tree, not even through ``git add -f``.
  * topology         - tailnet addresses and production node host names
                       (``public_export_redaction._TOPOLOGY_RULES``).
  * denylist         - private literals (people, hosts, ids, project names) kept in the
                       private ops repository, matched against ADDED lines and messages
                       only, so values that are already public do not block unrelated edits.

Findings name the file, line and rule, never the matched value: CI logs of a public
repository are public, and printing the value would publish what the gate refused.

Modes are decided by the tree: a checkout that still tracks ``.omo`` and has the export
manifest is the private source, where excluded paths are never published, so they are
skipped and commit messages are not checked (private history is never exported).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.public_export_redaction import _TOPOLOGY_RULES

FORBIDDEN_PREFIXES: Final = (".omo", "docs/qa")
MANIFEST: Final = "configs/public-export-manifest.txt"
DENYLIST_ENV: Final = "PUBLIC_GATE_DENYLIST"
OPS_REPO_KEY: Final = "autophagy.opsRepo"
DENYLIST_RELPATH: Final = "leak/denylist.txt"
EXIT_FINDINGS: Final = 1
EXIT_CONFIG: Final = 3


class GateConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Finding:
    where: str
    rule: str

    def render(self) -> str:
        return f"{self.where}: {self.rule}"


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args), capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise GateConfigError(f"git {args[0]} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def forbidden(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in FORBIDDEN_PREFIXES)


def load_denylist(text: str) -> tuple[str, ...]:
    entries = (line.strip() for line in text.splitlines())
    return tuple(entry for entry in entries if entry and not entry.startswith("#"))


def _pattern(entry: str) -> re.Pattern[str]:
    if entry.isascii():
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(entry) + r"(?![A-Za-z0-9])", re.I)
    return re.compile(re.escape(entry))


def resolve_denylist(repo: Path, explicit: str | None) -> tuple[str, ...] | None:
    """Return the entries, None when no denylist is configured, or raise when unreadable."""
    source = explicit or os.environ.get(DENYLIST_ENV)
    if not source:
        ops = _git(repo, "config", "--get", OPS_REPO_KEY, check=False).strip()
        if ops:
            source = str(Path(ops).expanduser() / DENYLIST_RELPATH)
    if not source:
        return None
    try:
        return load_denylist(Path(source).read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise GateConfigError(f"denylist is configured but unreadable ({error.__class__.__name__})") from error


def private_source_exclusions(repo: Path, rev: str) -> tuple[str, ...] | None:
    tracks_omo = _git(repo, "ls-tree", "--name-only", rev, "--", ".omo", check=False).strip()
    manifest = _git(repo, "show", f"{rev}:{MANIFEST}", check=False)
    if not tracks_omo or not manifest:
        return None
    return tuple(line for line in manifest.splitlines() if line and not line.startswith("#"))


def excluded(path: str, exclusions: tuple[str, ...]) -> bool:
    return any(path == entry or (entry.endswith("/") and path.startswith(entry)) for entry in exclusions)


def scan_text(where: str, text: str, denylist: tuple[str, ...] | None, *, topology: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        if topology:
            findings.extend(
                Finding(f"{where}:{number}", f"topology ({label})")
                for label, pattern in _TOPOLOGY_RULES
                if pattern.search(line)
            )
        if denylist:
            findings.extend(
                Finding(f"{where}:{number}", f"denylist entry #{index}")
                for index, entry in enumerate(denylist, 1)
                if _pattern(entry).search(line)
            )
    return findings


def added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """Map each file of a unified diff (-U0) to its added lines with new line numbers."""
    result: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    number = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:]
            current = target[2:] if target.startswith("b/") else None
            continue
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            number = int(match.group(1)) if match else 0
            continue
        if current is not None and raw.startswith("+"):
            result.setdefault(current, []).append((number, raw[1:]))
            number += 1
    return result


def check_changes(
    repo: Path,
    *,
    paths: list[str],
    diff: str,
    exclusions: tuple[str, ...] | None,
    denylist: tuple[str, ...] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    published = [p for p in paths if exclusions is None or not excluded(p, exclusions)]
    if exclusions is None:
        findings.extend(Finding(p, "forbidden path (session evidence / QA stays in the ops repo)") for p in paths if forbidden(p))
    for path, lines in added_lines(diff).items():
        if path not in published:
            continue
        for number, line in lines:
            findings.extend(
                Finding(f"{path}:{number}", finding.rule)
                for finding in scan_text(path, line, denylist)
            )
    return findings


def cmd_staged(repo: Path, denylist: tuple[str, ...] | None) -> list[Finding]:
    paths = [p for p in _git(repo, "diff", "--cached", "--name-only", "-z").split("\0") if p]
    diff = _git(repo, "diff", "--cached", "-U0", "--no-color", "--no-ext-diff")
    exclusions = private_source_exclusions(repo, "HEAD") if _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip() else None
    return check_changes(repo, paths=paths, diff=diff, exclusions=exclusions, denylist=denylist)


def cmd_message(text: str, denylist: tuple[str, ...] | None, repo: Path) -> list[Finding]:
    if _git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).strip() and private_source_exclusions(repo, "HEAD") is not None:
        return []
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    return scan_text("commit message", body, denylist)


def cmd_range(repo: Path, base: str, head: str, denylist: tuple[str, ...] | None, extra_text: str | None) -> list[Finding]:
    merge_base = _git(repo, "merge-base", base, head).strip()
    paths = [p for p in _git(repo, "diff", "--name-only", "-z", merge_base, head).split("\0") if p]
    diff = _git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", merge_base, head)
    exclusions = private_source_exclusions(repo, head)
    findings = check_changes(repo, paths=paths, diff=diff, exclusions=exclusions, denylist=denylist)
    if exclusions is None:
        log = _git(repo, "log", "--format=%H%x00%B%x01", f"{merge_base}..{head}")
        for record in filter(None, (item.strip("\n") for item in log.split("\x01"))):
            sha, _, body = record.partition("\0")
            findings.extend(scan_text(f"commit {sha[:12]} message", body, denylist))
        if extra_text:
            findings.extend(scan_text("PR title/body", extra_text, denylist))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="public_gate", description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--denylist", default=None, help=f"path; else ${DENYLIST_ENV}; else git config {OPS_REPO_KEY}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("staged", help="pre-commit: staged paths and added lines")
    message = sub.add_parser("message", help="commit-msg: the message file")
    message.add_argument("path")
    span = sub.add_parser("range", help="push / CI: BASE...HEAD changes and commit messages")
    span.add_argument("base")
    span.add_argument("head")
    span.add_argument("--text-env", default=None, help="environment variable holding PR title/body")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        denylist = resolve_denylist(repo, args.denylist)
        if args.command == "staged":
            findings = cmd_staged(repo, denylist)
        elif args.command == "message":
            findings = cmd_message(Path(args.path).read_text(encoding="utf-8"), denylist, repo)
        else:
            extra = os.environ.get(args.text_env, "") if args.text_env else None
            findings = cmd_range(repo, args.base, args.head, denylist, extra)
    except (GateConfigError, OSError) as error:
        print(f"[public-gate] PUBLIC-GATE-CONFIG: {error}", file=sys.stderr)
        return EXIT_CONFIG
    if denylist is None:
        print("[public-gate] PUBLIC-GATE-DENYLIST-UNSET: private-literal check skipped; path and topology checks ran", file=sys.stderr)
    if findings:
        print("[public-gate] REFUSED — this change would publish private data:", file=sys.stderr)
        for finding in dict.fromkeys(findings):
            print(f"  {finding.render()}", file=sys.stderr)
        return EXIT_FINDINGS
    print(f"[public-gate] PASS ({args.command})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
