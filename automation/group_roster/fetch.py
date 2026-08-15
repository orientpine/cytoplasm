"""Signed roster refresh from the managed feed's fixed roster branch."""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from automation.git_tag_signature import (
    DetachedSignatureInvocation,
    DetachedSignatureRequest,
    DetachedSignatureRunner,
    verify_detached_signature,
)
from automation.managed_sync.fetch import GitRunner

from .parser import parse_roster
from .schema import Roster
from .validator import RosterError

ROSTER_REF: Final = "refs/heads/roster"
ROSTER_SIGNATURE_NAMESPACE: Final = "autophagy-roster"

_ROSTER_PATH: Final = "roster/roster.yaml"
_SIGNATURE_PATH: Final = "roster/roster.yaml.sig"
_GIT_TIMEOUT_SECONDS: Final = 120.0
_SIGNATURE_TIMEOUT_SECONDS: Final = 30.0
# Resource limits for the PRE-AUTHENTICATION archive read (security audit
# 2026-08-15). Anyone who controls refs/heads/roster on the managed feed decides
# what `git archive` produces, and the extraction below runs on every subscriber's
# tick BEFORE the signature is checked — so an unbounded read is a pre-auth memory
# exhaustion primitive. Bounds follow automation/managed_skills/submission_archive.py.
# A roster is a small YAML document and its detached signature; these ceilings are
# orders of magnitude above any legitimate one.
_MAX_ARCHIVE_BYTES: Final = 4 * 1024 * 1024
_MAX_MEMBER_BYTES: Final = 1024 * 1024
_MAX_ARCHIVE_MEMBERS: Final = 64


@dataclass(frozen=True, slots=True)
class RosterFetchConfig:
    """Trusted inputs and local destination for one roster refresh."""

    mirror_dir: Path
    roster_path: Path
    allowed_signers: Path
    expected_principal: str


@dataclass(frozen=True, slots=True)
class RosterFetchResult:
    updated: bool


@dataclass(frozen=True, slots=True)
class RosterFetchError(Exception):
    reason: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}"


@dataclass(frozen=True, slots=True)
class _Git:
    runner: GitRunner
    environment: dict[str, str]

    def archive(self, mirror: Path, output: Path) -> None:
        args = (
            "git",
            "-C",
            str(mirror),
            "archive",
            "--format=tar",
            "--output",
            str(output),
            ROSTER_REF,
            _ROSTER_PATH,
            _SIGNATURE_PATH,
        )
        try:
            result = self.runner(
                list(args),
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RosterFetchError("ROSTER-ARCHIVE", f"git archive invocation failed: {error}") from error
        if result.returncode != 0:
            raise RosterFetchError(
                "ROSTER-ARCHIVE",
                f"git archive rejected roster artifacts: {result.stderr.strip()}",
            )


def _archive_member(archive: tarfile.TarFile, name: str) -> bytes:
    try:
        member = archive.getmember(name)
    except KeyError as error:
        raise RosterFetchError("ROSTER-ARCHIVE", f"archive is missing {name}") from error
    if not member.isfile():
        raise RosterFetchError("ROSTER-ARCHIVE", f"archive member is not a regular file: {name}")
    if member.size > _MAX_MEMBER_BYTES:
        raise RosterFetchError(
            "ROSTER-ARCHIVE",
            f"archive member exceeds the size limit: {name}",
        )
    stream = archive.extractfile(member)
    if stream is None:
        raise RosterFetchError("ROSTER-ARCHIVE", f"archive member cannot be read: {name}")
    try:
        # Bounded read: the declared size above is attacker-supplied metadata, so
        # the read itself is capped and an over-long body is rejected rather than
        # silently truncated.
        payload = stream.read(_MAX_MEMBER_BYTES + 1)
    except OSError as error:
        raise RosterFetchError("ROSTER-ARCHIVE", f"archive member cannot be read: {name}") from error
    finally:
        stream.close()
    if len(payload) > _MAX_MEMBER_BYTES:
        raise RosterFetchError(
            "ROSTER-ARCHIVE",
            f"archive member exceeds the size limit: {name}",
        )
    return payload


def _read_archive(path: Path) -> tuple[bytes, bytes]:
    try:
        archive_bytes = path.stat().st_size
    except OSError as error:
        raise RosterFetchError("ROSTER-ARCHIVE", f"roster archive cannot be read: {path}") from error
    if archive_bytes > _MAX_ARCHIVE_BYTES:
        raise RosterFetchError(
            "ROSTER-ARCHIVE",
            f"roster archive exceeds the size limit: {archive_bytes} bytes",
        )
    try:
        with tarfile.open(path) as archive:
            if len(archive.getnames()) > _MAX_ARCHIVE_MEMBERS:
                raise RosterFetchError(
                    "ROSTER-ARCHIVE",
                    "roster archive contains too many members",
                )
            return _archive_member(archive, _ROSTER_PATH), _archive_member(archive, _SIGNATURE_PATH)
    except (OSError, tarfile.TarError) as error:
        raise RosterFetchError("ROSTER-ARCHIVE", f"roster archive cannot be opened: {error}") from error


def _extract_roster(
    mirror: Path,
    runner: GitRunner,
    environment: dict[str, str],
) -> tuple[bytes, bytes]:
    try:
        with tempfile.TemporaryDirectory(prefix="roster-fetch-") as directory:
            archive_path = Path(directory) / "roster.tar"
            _Git(runner, environment).archive(mirror, archive_path)
            return _read_archive(archive_path)
    except OSError as error:
        raise RosterFetchError("ROSTER-ARCHIVE", f"temporary extraction failed: {error}") from error


def _allowed_signers(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise RosterFetchError("ROSTER-TRUST", f"allowed signers cannot be read: {path}") from error


def _parse(roster_bytes: bytes) -> Roster:
    try:
        text = roster_bytes.decode("utf-8")
    except UnicodeError as error:
        raise RosterFetchError("ROSTER-PARSE", "roster is not valid UTF-8") from error
    try:
        return parse_roster(text, source=f"{ROSTER_REF}:{_ROSTER_PATH}")
    except RosterError as error:
        raise RosterFetchError("ROSTER-PARSE", f"roster parse failed: {error}") from error


def _current_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RosterFetchError("ROSTER-INSTALL", f"current roster cannot be read: {path}") from error


def _installed_revision(current: bytes | None, path: Path) -> int | None:
    """The order the locally installed roster claims, or None when it claims none."""
    if current is None:
        return None
    try:
        return parse_roster(current.decode("utf-8"), source=str(path)).revision
    except (UnicodeError, RosterError):
        # A local roster that cannot be parsed carries no order to compare against.
        # Refusing forever instead would strand the installation with no recovery
        # but hand-deleting the very file this guard exists to protect.
        return None


def _refuse_rollback(installed: int | None, incoming: int | None) -> None:
    """Freshness, not just authorship (security audit 2026-08-15).

    The roster is the group's only revocation mechanism, and the mirror fetches its
    branch with a force refspec, so a feed host that cannot sign anything can still
    rewind the branch and re-serve a genuinely-signed earlier roster. Every existing
    check passes on that payload because it IS authentic; only its age is wrong.

    Strictly-greater matches `managed_sync.state.record_verified`, the same rule this
    project already relies on for signed managed-skill releases.
    """
    if installed is None:
        return
    if incoming is None:
        raise RosterFetchError(
            "ROSTER-ROLLBACK",
            f"roster declares no revision but revision {installed} is installed",
        )
    if incoming <= installed:
        raise RosterFetchError(
            "ROSTER-ROLLBACK",
            f"roster revision {incoming} does not advance installed revision {installed}",
        )


def _install(path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise RosterFetchError(
                    "ROSTER-INSTALL",
                    f"roster install failed: {error}; temporary cleanup failed: {cleanup_error}",
                ) from error
        raise RosterFetchError("ROSTER-INSTALL", f"roster install failed: {error}") from error


def refresh_roster(
    config: RosterFetchConfig,
    git_runner: GitRunner = subprocess.run,
    signature_runner: DetachedSignatureRunner = subprocess.run,
) -> RosterFetchResult:
    """Verify the fetched branch tip and atomically install only a valid non-empty roster."""
    environment = dict(os.environ)
    roster_bytes, signature = _extract_roster(config.mirror_dir, git_runner, environment)
    if not roster_bytes:
        raise RosterFetchError("ROSTER-EMPTY", "empty roster is never a valid replacement")
    if not verify_detached_signature(
        DetachedSignatureInvocation(signature_runner, environment, _SIGNATURE_TIMEOUT_SECONDS),
        DetachedSignatureRequest(
            message=roster_bytes,
            signature=signature,
            allowed_signers=_allowed_signers(config.allowed_signers),
            expected_principal=config.expected_principal,
            namespace=ROSTER_SIGNATURE_NAMESPACE,
        ),
    ):
        raise RosterFetchError("ROSTER-SIGNATURE", "detached signature verification failed")
    incoming = _parse(roster_bytes)
    current = _current_bytes(config.roster_path)
    if current == roster_bytes:
        return RosterFetchResult(updated=False)
    _refuse_rollback(_installed_revision(current, config.roster_path), incoming.revision)
    _install(config.roster_path, roster_bytes)
    return RosterFetchResult(updated=True)
