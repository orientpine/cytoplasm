#!/usr/bin/env python3
"""Privileged immutable skill release store operations."""
# Codified decision 7: this root-deployable helper stays ONE import-free file
# (<= ~320 lines) — a sanctioned, documented exception to the 250-LOC ceiling.

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Protocol


STORE_ROOT: Final = Path("/srv/autophagy-skills")
# Mirrored BY VALUE from automation.managed_skills.manifest (single-file helper
# must stay import-free); a unit drift-guard test asserts cross-module equality.
MANAGED_PREFIX: Final = "managed-"
MAX_SKILL_NAME: Final = 41
_SKILL_NAME: Final = re.compile(rf"\A[a-z0-9][a-z0-9-]{{1,{MAX_SKILL_NAME - 1}}}\Z")
_PUBLISHER_NAME: Final = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")
_DIGEST: Final = re.compile(r"\A[0-9a-f]{64}\Z")
_MAX_ARCHIVE_BYTES: Final = 64 * 1024 * 1024
_MAX_MEMBERS: Final = 10_000


class SkillStoreError(RuntimeError):
    """A candidate cannot be published into the privileged skill store."""


@dataclass(frozen=True, slots=True)
class InstallRequest:
    """One hash-bound skill archive publication request."""

    archive: Path
    store_root: Path
    skill: str
    expected_digest: str


@dataclass(frozen=True, slots=True)
class InstallCommand:
    skill: str
    digest: str

    def execute(self) -> str:
        archive = _stdin_archive(STORE_ROOT)
        try:
            release = install_archive(InstallRequest(archive, STORE_ROOT, self.skill, self.digest))
        finally:
            archive.unlink(missing_ok=True)
        return f"INSTALLED skill={self.skill} sha256={self.digest} release={release}"


@dataclass(frozen=True, slots=True)
class InstallManagedCommand:
    publisher: str
    skill: str
    digest: str

    def execute(self) -> str:
        archive = _stdin_archive(STORE_ROOT)
        try:
            request = InstallRequest(archive, STORE_ROOT, self.skill, self.digest)
            release = install_managed_archive(request, self.publisher)
        finally:
            archive.unlink(missing_ok=True)
        return (
            f"INSTALLED-MANAGED publisher={self.publisher} skill={self.skill}"
            f" sha256={self.digest} release={release}"
        )


@dataclass(frozen=True, slots=True)
class RemoveCommand:
    skill: str

    def execute(self) -> str:
        removed = remove_skill(STORE_ROOT, self.skill)
        return f"REMOVED skill={self.skill} present={removed}"


class Command(Protocol):
    def execute(self) -> str: ...


def install_archive(request: InstallRequest) -> Path:
    """Publish a verified base-namespace archive and return its immutable release path."""
    _validate_request(request)
    if request.skill.startswith(MANAGED_PREFIX):
        raise SkillStoreError(f"managed namespace is publisher-fed: {request.skill}")
    managed_twin = request.store_root / "live" / f"{MANAGED_PREFIX}{request.skill}"
    if managed_twin.is_symlink() or managed_twin.exists():
        raise SkillStoreError(f"skill collides with managed live entry: {managed_twin.name}")
    return _publish_release(request, request.store_root / "releases" / request.skill)


def install_managed_archive(request: InstallRequest, publisher: str) -> Path:
    """Publish a publisher-fed managed archive and return its immutable release path."""
    _validate_request(request)
    _validate_managed_names(publisher, request.skill)
    base_live = request.store_root / "live" / request.skill.removeprefix(MANAGED_PREFIX)
    if base_live.is_symlink() or base_live.exists():
        raise SkillStoreError(f"managed skill collides with base live entry: {base_live.name}")
    releases = request.store_root / "managed-releases" / publisher / request.skill
    return _publish_release(request, releases)


def _publish_release(request: InstallRequest, releases: Path) -> Path:
    staging_root = request.store_root / ".staging"
    live = request.store_root / "live"
    for directory in (staging_root, releases, live):
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{request.skill}-", dir=staging_root))
    try:
        candidate = _extract_archive(request.archive, staging, request.skill)
        actual_digest = _skill_digest(candidate)
        if actual_digest != request.expected_digest:
            raise SkillStoreError(
                f"digest mismatch: expected {request.expected_digest}, got {actual_digest}"
            )
        release = releases / request.expected_digest
        if release.exists():
            if _skill_digest(release) != request.expected_digest:
                raise SkillStoreError(f"existing release is corrupt: {release}")
            shutil.rmtree(candidate)
        else:
            os.replace(candidate, release)
            _make_read_only(release)
        _publish_live_link(live, request.skill, release)
        return release
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remove_skill(store_root: Path, skill: str) -> bool:
    """Remove a skill from the live index while retaining immutable releases."""
    _validate_skill_name(skill)
    live = store_root / "live" / skill
    if not live.is_symlink():
        if live.exists():
            raise SkillStoreError(f"live entry is not a managed symlink: {live}")
        return False
    live.unlink()
    return True


def _validate_skill_name(skill: str) -> None:
    if _SKILL_NAME.fullmatch(skill) is None:
        raise SkillStoreError(f"invalid skill name: {skill}")


def _validate_managed_names(publisher: str, skill: str) -> None:
    if _PUBLISHER_NAME.fullmatch(publisher) is None:
        raise SkillStoreError(f"invalid publisher name: {publisher}")
    if not skill.startswith(MANAGED_PREFIX):
        raise SkillStoreError(f"managed skill name must start with {MANAGED_PREFIX}: {skill}")


def _skill_digest(skill_dir: Path) -> str:
    files = (
        path
        for path in skill_dir.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.relative_to(skill_dir).parts
        and path.suffix not in {".pyc", ".pyo"}
    )
    ordered = sorted(files, key=lambda path: os.fsencode(path.relative_to(skill_dir).as_posix()))
    digest = hashlib.sha256()
    for path in ordered:
        relative = f"./{path.relative_to(skill_dir).as_posix()}".encode()
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"  ")
        digest.update(relative)
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_request(request: InstallRequest) -> None:
    _validate_skill_name(request.skill)
    if _DIGEST.fullmatch(request.expected_digest) is None:
        raise SkillStoreError("invalid expected digest")
    try:
        archive_size = request.archive.stat().st_size
    except OSError as error:
        raise SkillStoreError(f"archive is unreadable: {request.archive}") from error
    if archive_size > _MAX_ARCHIVE_BYTES:
        raise SkillStoreError(f"archive exceeds {_MAX_ARCHIVE_BYTES} bytes")


def _member_parts(member: tarfile.TarInfo, skill: str) -> tuple[str, ...]:
    path = Path(member.name)
    parts = path.parts
    if path.is_absolute() or not parts or parts[0] != skill or any(part in ("", ".", "..") for part in parts):
        raise SkillStoreError(f"unsafe archive path: {member.name}")
    if not (member.isdir() or member.isreg()):
        raise SkillStoreError("archive may contain regular files and directories only")
    return parts


def _extract_archive(archive_path: Path, staging: Path, skill: str) -> Path:
    candidate = staging / skill
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > _MAX_MEMBERS:
                raise SkillStoreError(f"archive exceeds {_MAX_MEMBERS} members")
            parsed = tuple((member, _member_parts(member, skill)) for member in members)
            if not parsed or not any(parts == (skill,) and member.isdir() for member, parts in parsed):
                raise SkillStoreError("archive lacks the skill root directory")
            for member, parts in parsed:
                destination = staging.joinpath(*parts)
                if member.isdir():
                    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise SkillStoreError(f"archive member is unreadable: {member.name}")
                with source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
                destination.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (OSError, tarfile.TarError) as error:
        raise SkillStoreError(f"archive extraction failed: {error}") from error
    return candidate


def _make_read_only(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        mode = path.stat().st_mode
        path.chmod(0o555 if path.is_dir() or mode & 0o111 else 0o444)


def _publish_live_link(live: Path, skill: str, release: Path) -> None:
    destination = live / skill
    if destination.exists() and not destination.is_symlink():
        raise SkillStoreError(f"live entry is not a managed symlink: {destination}")
    temporary = live / f".{skill}.{uuid.uuid4().hex}"
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, destination)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise SkillStoreError("privileged helper must run as root")


def _stdin_archive(store_root: Path) -> Path:
    incoming = store_root / ".incoming"
    incoming.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix="skill-", suffix=".tar.gz", dir=incoming)
    path = Path(raw_path)
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as target:
            while chunk := sys.stdin.buffer.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_ARCHIVE_BYTES:
                    raise SkillStoreError(f"archive exceeds {_MAX_ARCHIVE_BYTES} bytes")
                _ = target.write(chunk)
        return path
    except (OSError, SkillStoreError):
        path.unlink(missing_ok=True)
        raise


def _parse_command(argv: Sequence[str]) -> Command:
    values = tuple(argv)
    if len(values) == 5 and values[:2] == ("install", "--skill") and values[3] == "--hash":
        return InstallCommand(values[2], values[4])
    if len(values) == 5 and values[:2] == ("install", "--hash") and values[3] == "--skill":
        return InstallCommand(values[4], values[2])
    if (
        len(values) == 7
        and values[:2] == ("install-managed", "--publisher")
        and values[3] == "--skill"
        and values[5] == "--hash"
    ):
        _validate_skill_name(values[4])
        _validate_managed_names(values[2], values[4])
        return InstallManagedCommand(values[2], values[4], values[6])
    if len(values) == 3 and values[:2] == ("remove", "--skill"):
        return RemoveCommand(values[2])
    usage = (
        "usage: autophagy-install-skill install --skill NAME --hash SHA256"
        " | install-managed --publisher NAME --skill NAME --hash SHA256 | remove --skill NAME"
    )
    raise SkillStoreError(usage)


def main() -> int:
    """Run the root-only install/remove command boundary."""
    try:
        _require_root()
        command = _parse_command(sys.argv[1:])
        print(command.execute())
    except SkillStoreError as error:
        print(f"SKILL-STORE-BLOCK: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
