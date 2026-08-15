"""Obsidian vault mirror source (DT-A1, pure logic).

Scans a read-only mirror of the owner's Obsidian vault through the shared
markdown pipeline (``scan_directory``). Obsidian-specific syntax (callouts
``>[!info]``, ``%%todoist%%`` comments, ``=dateformat()`` inline
expressions) is deliberately ingested as plain text — no parsing. Noise
directories (plugin state, backups, limbo, drawings) are expanded into
concrete exclude paths so the present-keys set exactly mirrors the current
vault files and drives deletion sync.
"""

from __future__ import annotations

import fnmatch
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from automation.git_remote_url import GitRemoteUrlError, validate_remote_url

from ..config import ObsidianSourceConfig
from ..documents import Chunk, LogicalDocument
from ..sensitivity import SensitivityRules, SensitivityRulesError, classify, load_rules
from .files import scan_directory

_GIT_TIMEOUT_SECONDS: Final = 120.0
_GIT_CLONE_TIMEOUT_SECONDS: Final = 3600.0  # initial clone of a large vault (659MB+)


class _GitRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


class ObsidianSyncError(Exception):
    """Git mirror synchronization failed; retry later without scanning stale content."""


@dataclass(frozen=True, slots=True)
class SyncResult:
    action: str
    mirror_dir: Path


@dataclass(frozen=True, slots=True)
class _GitInvocation:
    args: tuple[str, ...]
    cwd: Path | None
    timeout: float = _GIT_TIMEOUT_SECONDS


def _run_git(
    runner: _GitRunner,
    invocation: _GitInvocation,
    environment: dict[str, str],
) -> None:
    args = list(invocation.args)
    try:
        result = runner(
            args,
            cwd=invocation.cwd,
            env=environment,
            timeout=invocation.timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ObsidianSyncError(f"git step failed: {' '.join(args)}: {error}") from error
    if result.returncode != 0:
        raise ObsidianSyncError(f"git step returned {result.returncode}: {' '.join(args)}")


def mirror_is_healthy(mirror_dir: Path, runner: _GitRunner = subprocess.run) -> bool:
    """True iff the mirror has a ``.git`` AND a resolvable HEAD (checkout completed).

    A ``.git`` left behind by an interrupted clone has no commits — treating it
    as a last-good mirror would fetch forever / scan emptiness. Never raises.
    """
    if not (mirror_dir / ".git").exists():
        return False
    try:
        result = runner(
            ["git", "-C", str(mirror_dir), "rev-parse", "--verify", "HEAD"],
            cwd=None,
            env=dict(os.environ),
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def sync_mirror(
    config: ObsidianSourceConfig,
    runner: _GitRunner = subprocess.run,
) -> SyncResult:
    environment = dict(os.environ)
    # git runs GIT_SSH_COMMAND through a shell and ssh_key_path comes from the
    # agent-writable ~/.hermes/rag-ingest/config.json, so the path is shell-quoted
    # (shlex.join precedent: automation/install/apply.py). Ordinary key paths are
    # left byte-identical.
    environment["GIT_SSH_COMMAND"] = shlex.join(
        ("ssh", "-i", str(config.ssh_key_path), "-o", "IdentitiesOnly=yes")
    )
    if mirror_is_healthy(config.mirror_dir, runner):
        _run_git(
            runner,
            _GitInvocation(("git", "fetch"), config.mirror_dir),
            environment,
        )
        _run_git(
            runner,
            _GitInvocation(
                ("git", "reset", "--hard", f"origin/{config.branch}"),
                config.mirror_dir,
            ),
            environment,
        )
        # SI-5 hardening is idempotent here: an interrupted clone may have left
        # the push URL enabled, so re-disable it on every fetch pass too.
        _run_git(
            runner,
            _GitInvocation(
                ("git", "remote", "set-url", "--push", "origin", "DISABLED"),
                config.mirror_dir,
            ),
            environment,
        )
        return SyncResult(action="fetched", mirror_dir=config.mirror_dir)

    if (config.mirror_dir / ".git").exists():
        # Partial clone (no HEAD): the mirror is a rebuildable cache, never user
        # data — remove it and self-heal via a fresh clone.
        shutil.rmtree(config.mirror_dir)

    config.mirror_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    config.mirror_dir.chmod(0o700)
    try:
        repo_url = validate_remote_url(config.repo_url, label="obsidian repo_url")
    except GitRemoteUrlError as error:
        raise ObsidianSyncError(str(error)) from error
    _run_git(
        runner,
        _GitInvocation(
            # `--` keeps a dash-leading URL out of git's option namespace.
            ("git", "clone", "--branch", config.branch, "--", repo_url, str(config.mirror_dir)),
            None,
            timeout=_GIT_CLONE_TIMEOUT_SECONDS,
        ),
        environment,
    )
    _run_git(
        runner,
        _GitInvocation(
            ("git", "remote", "set-url", "--push", "origin", "DISABLED"),
            config.mirror_dir,
        ),
        environment,
    )
    return SyncResult(action="cloned", mirror_dir=config.mirror_dir)

DEFAULT_EXCLUDE_NAMES: tuple[str, ...] = (
    ".obsidian",
    ".omo",
    ".sisyphus",
    ".omo-backup*",
    "999_limbo",
    "Excalidraw",
    ".claude",
    ".cursor",
    ".playwright-mcp",
)


def _expand_exclude_dirs(mirror_dir: Path, exclude_names: tuple[str, ...]) -> tuple[Path, ...]:
    """Expand exclusion patterns (glob-capable ``*`` suffixes) to concrete dirs."""
    if not mirror_dir.is_dir():
        return ()
    matches: set[Path] = set()
    for current, dir_names, _file_names in os.walk(mirror_dir):
        kept: list[str] = []
        for dir_name in dir_names:
            if any(fnmatch.fnmatchcase(dir_name, pattern) for pattern in exclude_names):
                matches.add(Path(current) / dir_name)
            else:
                kept.append(dir_name)
        dir_names[:] = kept  # os.walk prune protocol: skip excluded trees
    return tuple(sorted(matches))


def _with_chunk_metadata(document: LogicalDocument, extra: dict[str, str]) -> LogicalDocument:
    chunks = tuple(
        Chunk(
            source=chunk.source,
            content=chunk.content,
            metadata={**chunk.metadata, **extra},
        )
        for chunk in document.chunks
    )
    return LogicalDocument(
        source_key=document.source_key,
        chunks=chunks,
        cursor_updates=dict(document.cursor_updates),
    )


def _with_folder(document: LogicalDocument, folder: str) -> LogicalDocument:
    return _with_chunk_metadata(document, {"folder": folder})


def _with_sensitivity(document: LogicalDocument, sensitivity: str) -> LogicalDocument:
    return _with_chunk_metadata(document, {"sensitivity": sensitivity})


def _load_sensitivity_rules(path: Path) -> SensitivityRules:
    try:
        return load_rules(path)
    except (OSError, SensitivityRulesError) as error:
        raise ObsidianSyncError(f"sensitivity rules unavailable: {path}: {error}") from error


def scan_obsidian(
    mirror_dir: Path,
    exclude_names: tuple[str, ...],
    perspective: dict[str, str],
    max_chunk_chars: int,
    sensitivity_rules_path: Path | None = None,
) -> tuple[list[LogicalDocument], set[str]]:
    """Return (documents, present source keys) for the Obsidian mirror.

    Thin deterministic wrapper over ``scan_directory`` — source_key =
    ``obsidian:<relpath>``, ``path`` metadata comes from the shared scanner.
    Adds per-document ``folder`` metadata (top-level PARA dir; omitted for
    vault-root notes). Point ids stay (source, content)-derived, so the
    folder enrichment never perturbs idempotent upserts.
    """
    sensitivity_rules = (
        _load_sensitivity_rules(sensitivity_rules_path)
        if sensitivity_rules_path is not None
        else None
    )
    documents, present_keys = scan_directory(
        root=mirror_dir,
        prefix="obsidian",
        source_type="obsidian",
        perspective=perspective,
        max_chunk_chars=max_chunk_chars,
        exclude_dirs=_expand_exclude_dirs(mirror_dir, exclude_names),
    )
    enriched: list[LogicalDocument] = []
    for document in documents:
        relative = document.source_key.removeprefix("obsidian:")
        parts = relative.split("/")
        folder_document = _with_folder(document, parts[0]) if len(parts) > 1 else document
        if sensitivity_rules is None:
            enriched.append(folder_document)
            continue
        full_text = "\n\n".join(chunk.content for chunk in folder_document.chunks)
        tags = classify(full_text, sensitivity_rules)
        enriched.append(
            _with_sensitivity(folder_document, "patent-sensitive")
            if "patent-sensitive" in tags
            else folder_document
        )
    return enriched, present_keys
