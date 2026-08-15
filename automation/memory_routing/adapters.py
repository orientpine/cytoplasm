"""Memory storage adapters (B2): one thin writer per classified target.

Each adapter takes the classifier's :class:`MemoryRoute` verdict plus an injected
target descriptor and reports one of four outcomes. The adapters own no policy of
their own: routing belongs to ``classifier.py`` and owner approval belongs to the
existing wiki gate, which ``write_wiki`` reuses by spawning ``wiki_cli.py draft``
as a child process with an explicit ``env=`` (watcher/cron rule b-2).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, Protocol, TypeAlias

from .classifier import MemoryRoute, MemoryTarget

AdapterOutcome: TypeAlias = Literal["success", "rejected", "duplicate", "retryable_failure"]

_CHILD_TIMEOUT_SECONDS: Final = 60
_WIKI_RC_UNCONFIRMED: Final = 1
_WIKI_RC_SCHEMA: Final = 2
# wiki_store requires `authority` whenever kind is decision|principle|preference.
# An owner-stated memory is followed by default, never `strict`.
_WIKI_AUTHORITY: Final = "default"
_BULLET_RE: Final = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_DRAFT_ID_RE: Final = re.compile(r"\bid=(\S+)")
_TRIM_CHARS: Final = " .,!?:;~。！？"


@dataclass(frozen=True, slots=True)
class AdapterResult:
    outcome: AdapterOutcome
    detail: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        env: Mapping[str, str],
    ) -> CommandResult: ...


def run_command(argv: tuple[str, ...], *, env: Mapping[str, str]) -> CommandResult:
    """Spawn a child with an explicit env — never the shell, never inherited creds."""
    completed = subprocess.run(  # noqa: S603 - argv is built from typed fields only
        list(argv),
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
        timeout=_CHILD_TIMEOUT_SECONDS,
    )
    return CommandResult(returncode=completed.returncode, stdout=completed.stdout)


@dataclass(frozen=True, slots=True)
class MemoryWrite:
    """One classified memory request ready to be persisted."""

    route: MemoryRoute
    title: str
    body: str
    tags: tuple[str, ...] = ()
    approved_sensitive: bool = False


@dataclass(frozen=True, slots=True)
class WikiTarget:
    cli_path: Path
    env: Mapping[str, str]
    channel_id: str = "dm"
    pending_keys: frozenset[str] = frozenset()
    runner: CommandRunner = run_command


@dataclass(frozen=True, slots=True)
class MemoryMdTarget:
    path: Path
    max_chars: int = 200


@dataclass(frozen=True, slots=True)
class SkillTarget:
    directory: Path
    guarded_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class TasksTarget:
    path: Path
    expires_at: str = ""
    guarded_paths: tuple[Path, ...] = ()


def dedupe_key(text: str) -> str:
    """Comparison key: bullets, whitespace, case and trailing punctuation ignored.

    Every adapter keys on the normalised ``MemoryWrite.body`` — the fact, the
    procedure or the temporary state — so a caller holding a ledger of pending
    approvals derives the same key without knowing which adapter will run.
    """
    lines = (_BULLET_RE.sub("", line) for line in text.splitlines())
    joined = " ".join(" ".join(line.split()) for line in lines if line.strip())
    return joined.casefold().strip(_TRIM_CHARS)


def write_wiki(write: MemoryWrite, target: WikiTarget) -> AdapterResult:
    """Draft a wiki note through the existing owner-gated CLI (drafts only)."""
    blocked = _precheck(write, "wiki")
    if blocked is not None:
        return blocked
    key = dedupe_key(write.body)
    if key in target.pending_keys:
        return AdapterResult("duplicate", "an identical wiki draft already awaits approval")
    argv = (
        sys.executable,
        str(target.cli_path),
        "draft",
        "--title",
        write.title,
        "--tags",
        ",".join(write.tags),
        "--body",
        write.body,
        "--channel-id",
        target.channel_id,
        "--kind",
        _twin_kind(write.route),
        "--authority",
        _WIKI_AUTHORITY,
        "--provenance",
        "stated",
        "--status",
        "active",
    )
    try:
        result = target.runner(argv, env=target.env)
    except (OSError, subprocess.SubprocessError) as exc:
        return _io_failure("wiki-cli", exc)
    return _wiki_outcome(result)


def write_memory_md(write: MemoryWrite, target: MemoryMdTarget) -> AdapterResult:
    """Append one short, stable fact — deduplicated, never a second copy."""
    blocked = _precheck(write, "memory_md")
    if blocked is not None:
        return blocked
    fact = " ".join(write.body.split())
    if not fact:
        return AdapterResult("rejected", "MEMORY.md needs a non-empty fact")
    if len(fact) > target.max_chars:
        return AdapterResult("rejected", f"fact is {len(fact)} chars, too long to stay stable")
    key = dedupe_key(fact)
    try:
        if key in _memory_md_keys(target.path):
            return AdapterResult("duplicate", "MEMORY.md already records this fact")
        _append_line(target.path, f"- {fact}")
    except OSError as exc:
        return _io_failure("memory-md", exc)
    return AdapterResult("success", f"appended one fact ({len(fact)} chars)")


def write_skill(write: MemoryWrite, target: SkillTarget) -> AdapterResult:
    """Record a reusable procedure — never duplicated into MEMORY.md."""
    blocked = _precheck(write, "skill")
    if blocked is not None:
        return blocked
    key = dedupe_key(write.body)
    if not key:
        return AdapterResult("rejected", "a procedure needs a non-empty body")
    path = target.directory / f"{_digest(key)}.json"
    collision = _collides(path, target.guarded_paths)
    if collision is not None:
        return AdapterResult("rejected", f"procedure must not be stored in {collision.name}")
    record = {
        "title": write.title,
        "tags": list(write.tags),
        "body": write.body,
        "reason": write.route.reason,
        "key": key,
    }
    try:
        if path.exists():
            return AdapterResult("duplicate", "this procedure is already recorded")
        target.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return _io_failure("skill-proposal", exc)
    return AdapterResult("success", f"recorded procedure {path.name}")


def write_tasks(write: MemoryWrite, target: TasksTarget) -> AdapterResult:
    """Record temporary state with an expiry — refuses any permanent store."""
    blocked = _precheck(write, "tasks")
    if blocked is not None:
        return blocked
    if not target.expires_at:
        return AdapterResult("rejected", "temporary state needs an expiry")
    collision = _collides(target.path, target.guarded_paths)
    if collision is not None:
        return AdapterResult("rejected", f"temporary state must not reach {collision.name}")
    state = " ".join(write.body.split())
    if not state:
        return AdapterResult("rejected", "temporary state needs a non-empty body")
    key = dedupe_key(state)
    record = {"key": key, "title": write.title, "state": state, "expires_at": target.expires_at}
    try:
        if key in _tasks_keys(target.path):
            return AdapterResult("duplicate", "this temporary state is already tracked")
        _append_line(target.path, json.dumps(record, ensure_ascii=False))
    except (OSError, ValueError) as exc:
        return _io_failure("tasks", exc)
    return AdapterResult("success", f"tracked until {target.expires_at}")


def _precheck(write: MemoryWrite, target: MemoryTarget) -> AdapterResult | None:
    """Deterministic guards shared by every adapter (fail-closed)."""
    route = write.route
    if route.needs_sensitive_approval and not write.approved_sensitive:
        return AdapterResult("rejected", f"sensitive content needs approval ({target})")
    if route.never_persist and target != "tasks":
        return AdapterResult("rejected", f"never-persist state must not reach {target}")
    if target != route.canonical and target not in route.co_write:
        return AdapterResult("rejected", f"route '{route.reason}' did not select {target}")
    return None


def _wiki_outcome(result: CommandResult) -> AdapterResult:
    if result.returncode == 0:
        match = _DRAFT_ID_RE.search(result.stdout)
        if match is None:
            return AdapterResult("retryable_failure", "wiki-cli reported no draft id")
        return AdapterResult("success", f"draft={match.group(1)} awaiting owner approval")
    if result.returncode == _WIKI_RC_SCHEMA:
        return AdapterResult("rejected", "wiki rejected the frontmatter schema (rc=2)")
    if result.returncode == _WIKI_RC_UNCONFIRMED:
        return AdapterResult("rejected", "wiki owner confirmation absent (rc=1)")
    return AdapterResult("retryable_failure", f"wiki-cli rc={result.returncode}")


def _twin_kind(route: MemoryRoute) -> str:
    return "preference" if route.reason == "stable-global-preference" else "note"


def _digest(key: str) -> str:
    return sha256(key.encode("utf-8")).hexdigest()[:16]


def _collides(path: Path, guarded: tuple[Path, ...]) -> Path | None:
    for other in guarded:
        if path == other or other in path.parents or path.parent in (other, *other.parents):
            return other
    return None


def _memory_md_keys(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    lines = path.read_text(encoding="utf-8").splitlines()
    keys = (dedupe_key(line) for line in lines if not line.lstrip().startswith("#"))
    return frozenset(key for key in keys if key)


def _tasks_keys(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    records = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    return frozenset(str(record["key"]) for record in records if "key" in record)


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    separator = "\n" if existing and not existing.endswith("\n") else ""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{separator}{line}\n")


def _io_failure(scope: str, exc: Exception) -> AdapterResult:
    return AdapterResult("retryable_failure", f"{scope}: {type(exc).__name__}")
