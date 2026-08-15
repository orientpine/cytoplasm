"""Managed feed mirror fetcher — read-only git over a pre-approved remote.

Syncs release tags into one local mirror and exposes an independent fetch for
the fixed ``refs/heads/roster`` transport branch in that same mirror. A missing
roster branch cannot fail skill delivery. Roster verification and installation
stay in ``automation.group_roster.fetch`` rather than this transport primitive.
Fail-closed: any git failure raises :class:`ManagedFetchError` with the git stderr. The remote URL comes
ONLY from the injected config (pre-approved remote — never a CLI or function
argument), the mirror's push URL is disabled on every sync pass (obsidian
SI-5 precedent — never write back to a read-only feed), and fetch NEVER
prunes local tags (decision 16: upstream tag deletion is not revocation).
Every subprocess call carries an explicit ``env=`` (watcher-cron 규약 b-2).
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from automation.git_remote_url import GitRemoteUrlError, validate_remote_url

_GIT_TIMEOUT_SECONDS: Final = 120.0
_GIT_CLONE_TIMEOUT_SECONDS: Final = 600.0
_TAG_SEQUENCE_PATTERN: Final = re.compile(r"v([1-9]\d*)\Z")
_TAG_FETCH_REFSPEC: Final = "+refs/tags/*:refs/tags/*"
_ROSTER_FETCH_REFSPEC: Final = "+refs/heads/roster:refs/heads/roster"
_LOGGER: Final = logging.getLogger(__name__)


class GitRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


class RosterRemoteConfig(Protocol):
    """Read-only access needed to refresh the roster ref in an existing mirror."""

    @property
    def mirror_dir(self) -> Path: ...
    @property
    def ssh_key_path(self) -> Path: ...


class FetchConfig(RosterRemoteConfig, Protocol):
    """Structural contract for the sync config (MS-S6 builds the concrete object)."""

    @property
    def remote_url(self) -> str: ...


class ManagedFetchError(Exception):
    """Read-only sync of the managed-skills mirror failed; retry next tick."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    mirror_dir: Path
    fetched: bool
    cloned: bool


@dataclass(frozen=True, slots=True)
class ReleaseTag:
    skill: str
    sequence: int
    tag_name: str


@dataclass(frozen=True, slots=True)
class _Git:
    runner: GitRunner
    environment: dict[str, str]

    def run(
        self,
        args: tuple[str, ...],
        timeout: float = _GIT_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self.runner(
                list(args),
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ManagedFetchError(f"git step failed: {' '.join(args)}: {error}") from error
        if result.returncode != 0:
            raise ManagedFetchError(
                f"git step returned {result.returncode}: {' '.join(args)}: {result.stderr}"
            )
        return result


def _ssh_environment(ssh_key_path: Path) -> dict[str, str]:
    """Build GIT_SSH_COMMAND with a shell-quoted key path.

    git runs GIT_SSH_COMMAND through a shell, and ``ssh_key_path`` comes from the
    agent-writable ``~/.hermes/managed-sync/config.json``, so raw interpolation was
    arbitrary command execution. ``shlex.join`` matches the existing precedent in
    ``automation/install/apply.py`` and leaves ordinary key paths byte-identical.
    """
    environment = dict(os.environ)
    environment["GIT_SSH_COMMAND"] = shlex.join(
        ("ssh", "-i", str(ssh_key_path), "-o", "IdentitiesOnly=yes")
    )
    return environment


def _disable_push_args(mirror_dir: Path) -> tuple[str, ...]:
    return ("git", "-C", str(mirror_dir), "remote", "set-url", "--push", "origin", "DISABLED")


def _fetch_args(mirror_dir: Path, refspec: str) -> tuple[str, ...]:
    return ("git", "-C", str(mirror_dir), "fetch", "origin", refspec)


def sync_remote(config: FetchConfig, runner: GitRunner = subprocess.run) -> FetchResult:
    """Clone or fetch managed-skill tags without depending on roster availability."""
    git = _Git(runner=runner, environment=_ssh_environment(config.ssh_key_path))
    mirror_dir = config.mirror_dir
    if (mirror_dir / ".git").is_dir():
        # No --prune: decision 16 — upstream tag deletion is NOT revocation.
        _ = git.run(_fetch_args(mirror_dir, _TAG_FETCH_REFSPEC))
        # SI-5 hardening is idempotent here: an interrupted clone may have left
        # the push URL enabled, so re-disable it on every fetch pass too.
        _ = git.run(_disable_push_args(mirror_dir))
        return FetchResult(mirror_dir=mirror_dir, fetched=True, cloned=False)

    mirror_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mirror_dir.parent.chmod(0o700)
    try:
        remote_url = validate_remote_url(config.remote_url, label="managed feed remote_url")
    except GitRemoteUrlError as error:
        raise ManagedFetchError(str(error)) from error
    # `--` keeps a dash-leading URL out of git's option namespace.
    _ = git.run(
        ("git", "clone", "--", remote_url, str(mirror_dir)),
        timeout=_GIT_CLONE_TIMEOUT_SECONDS,
    )
    _ = git.run(_disable_push_args(mirror_dir))
    _ = git.run(_fetch_args(mirror_dir, _TAG_FETCH_REFSPEC))
    return FetchResult(mirror_dir=mirror_dir, fetched=False, cloned=True)


def sync_roster_ref(
    config: RosterRemoteConfig,
    runner: GitRunner = subprocess.run,
) -> None:
    """Fetch only the fixed roster ref into the existing managed feed mirror."""
    git = _Git(runner=runner, environment=_ssh_environment(config.ssh_key_path))
    _ = git.run(_fetch_args(config.mirror_dir, _ROSTER_FETCH_REFSPEC))


def _parse_release_tag(skill: str, tag_name: str) -> ReleaseTag | None:
    prefix = f"{skill}/"
    if not tag_name.startswith(prefix):
        return None
    match = _TAG_SEQUENCE_PATTERN.fullmatch(tag_name.removeprefix(prefix))
    if match is None:
        return None
    return ReleaseTag(skill=skill, sequence=int(match.group(1)), tag_name=tag_name)


def list_release_tags(
    mirror: Path,
    skill: str,
    runner: GitRunner = subprocess.run,
) -> tuple[ReleaseTag, ...]:
    """Return ``<skill>/v<seq>`` release tags, sequence-sorted ascending (numeric).

    Malformed tag names are skipped (and reported once) — a malformed tag is
    not fatal; only the git invocation failing is.
    """
    git = _Git(runner=runner, environment=dict(os.environ))
    result = git.run(("git", "-C", str(mirror), "tag", "--list", f"{skill}/v*"))
    tags: list[ReleaseTag] = []
    malformed = 0
    for line in result.stdout.splitlines():
        tag_name = line.strip()
        if not tag_name:
            continue
        tag = _parse_release_tag(skill, tag_name)
        if tag is None:
            malformed += 1
            continue
        tags.append(tag)
    if malformed:
        _LOGGER.warning("skipped %d malformed release tag(s) for skill %s", malformed, skill)
    return tuple(sorted(tags, key=lambda tag: tag.sequence))
