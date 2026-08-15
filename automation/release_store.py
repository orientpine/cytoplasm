#!/usr/bin/env python3
"""Immutable release store for the deploy runtime root (DG-3).

Install a by-value copy of ONE verified origin/main tree under
``<store-root>/autophagy-agent-releases/<sha>/`` and atomically flip
``<store-root>/autophagy-agent-current`` to it (``--store-root`` is the PARENT of
the release namespace; default ``/srv``). The release is made root-owned and
read-only so ``peer_attest``'s tamper guard (mode & 0o022) passes; runtime reads
``autophagy-agent-current``; the resident deploy checkout is a drift mirror only.

Mirrors the proven ``skill_store.py`` idioms (stdin tar.gz transport, unsafe
member rejection, ``_make_read_only``, atomic symlink flip). The privileged
``install`` verb runs as root on the node; the tests exercise it under a fake
store root without root.

Usage:
  release_store.py install --sha <hex> --store-root <parent>   # tar.gz on stdin
  release_store.py current --verify <hex> --store-root <parent>
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Final, Protocol, cast


_SHA_RE: Final = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_ARCHIVE_BYTES: Final = 64 * 1024 * 1024
_MAX_MEMBERS: Final = 20000
_ORIGIN_SHA_MARKER: Final = ".origin-sha"
# `--store-root` is the PARENT of the release namespace; the child basenames are
# fixed domain contract so runtime_root.py / provision / systemd units all agree.
_RELEASES_BASENAME: Final = "autophagy-agent-releases"
_CURRENT_BASENAME: Final = "autophagy-agent-current"
_RETAIN_GENERATIONS: Final = 5


class ReleaseStoreError(RuntimeError):
    """A release could not be safely installed or activated."""


class _CliArgs(Protocol):
    command: str
    sha: str
    failed_sha: str
    store_root: Path | None
    git_root: Path | None


def _member_parts(member: tarfile.TarInfo) -> tuple[str, ...]:
    # `tar -C <dir> ... .` emits a top-level "." (or "./") directory member. It is
    # the archive root itself, not a real entry — treat it as a harmless no-op so a
    # by-value snapshot install (converge-release-runtime.sh) is accepted.
    if member.name in (".", "./"):
        return ()
    path = Path(member.name)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        raise ReleaseStoreError(f"unsafe archive path: {member.name}")
    if not (member.isdir() or member.isreg()):
        raise ReleaseStoreError("archive may contain regular files and directories only")
    return parts


def _extract(archive: bytes, staging: Path) -> None:
    total = len(archive)
    if total > _MAX_ARCHIVE_BYTES:
        raise ReleaseStoreError(f"archive exceeds {_MAX_ARCHIVE_BYTES} bytes")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) > _MAX_MEMBERS:
                raise ReleaseStoreError(f"archive exceeds {_MAX_MEMBERS} members")
            parsed = tuple((member, _member_parts(member)) for member in members)
            if not parsed:
                raise ReleaseStoreError("archive is empty")
            for member, parts in parsed:
                if not parts:  # the "." self-directory member — nothing to extract
                    continue
                destination = staging.joinpath(*parts)
                if member.isdir():
                    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise ReleaseStoreError(f"archive member is unreadable: {member.name}")
                with source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
                destination.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (OSError, tarfile.TarError) as error:
        raise ReleaseStoreError(f"archive extraction failed: {error}") from error


def _make_read_only(root: Path) -> None:
    for path in sorted((root, *root.rglob("*")), key=lambda p: len(p.parts), reverse=True):
        mode = path.stat().st_mode
        path.chmod(0o555 if path.is_dir() or mode & 0o111 else 0o444)


def activate_release(current: Path, release: Path) -> None:
    if current.exists() and not current.is_symlink():
        raise ReleaseStoreError(f"current entry is not a managed symlink: {current}")
    temporary = current.parent / f".current.{uuid.uuid4().hex}"
    temporary.symlink_to(release, target_is_directory=True)
    os.replace(temporary, current)


def _layout(store_root: Path) -> tuple[Path, Path]:
    return store_root / _RELEASES_BASENAME, store_root / _CURRENT_BASENAME


def _prune_releases(releases: Path, protected: frozenset[Path]) -> None:
    generations = sorted(
        (
            path
            for path in releases.iterdir()
            if _SHA_RE.fullmatch(path.name) and path.is_dir() and not path.is_symlink()
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    retained = set(generations[:_RETAIN_GENERATIONS]) | set(protected)
    for obsolete in generations:
        if obsolete in retained:
            continue
        _remove_tree(obsolete)


def _remove_tree(root: Path) -> None:
    for directory in (root, *(path for path in root.rglob("*") if path.is_dir())):
        directory.chmod(0o755)
    shutil.rmtree(root)


def _verify_release_tree(release: Path, sha: str, git_root: Path) -> None:
    verifier = Path(__file__).with_name("release_provenance.py")
    result = subprocess.run(
        (
            sys.executable,
            "-I",
            str(verifier),
            "--release",
            str(release),
            "--git-root",
            str(git_root),
            "--reference",
            sha,
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        reason = result.stderr.strip() or "release provenance verifier failed without a reason"
        raise ReleaseStoreError(reason)


def _install(sha: str, store_root: Path, archive: bytes, *, git_root: Path | None = None) -> Path:
    if not _SHA_RE.match(sha):
        raise ReleaseStoreError(f"sha is not a hex commit id: {sha}")
    releases, current = _layout(store_root)
    release = releases / sha
    prior = current.resolve() if current.is_symlink() else None
    if release.is_dir():
        if git_root is not None:
            _verify_release_tree(release, sha, git_root)
        _prune_releases(releases, frozenset(path for path in (release, prior) if path is not None))
        activate_release(current, release)  # idempotent: verify-by-presence, re-flip
        return release
    releases.mkdir(mode=0o755, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=releases))
    candidate = staging / sha
    try:
        candidate.mkdir()
        _extract(archive, candidate)
        _ = (candidate / _ORIGIN_SHA_MARKER).write_text(f"{sha}\n", encoding="utf-8")
        _make_read_only(candidate)
        if git_root is not None:
            _verify_release_tree(candidate, sha, git_root)
        candidate.chmod(0o755)
        os.replace(candidate, release)
        release.chmod(0o555)
        staging.rmdir()
    except (OSError, ReleaseStoreError):
        if staging.exists():
            _remove_tree(staging)
        raise
    _prune_releases(releases, frozenset(path for path in (release, prior) if path is not None))
    activate_release(current, release)
    return release


def rollback_current(store_root: Path, *, failed_sha: str, prior_sha: str) -> Path:
    if _SHA_RE.fullmatch(failed_sha) is None or _SHA_RE.fullmatch(prior_sha) is None:
        raise ReleaseStoreError("rollback shas must be hex commit ids")
    releases, current = _layout(store_root)
    if not current.is_symlink():
        raise ReleaseStoreError("current is not a managed symlink")
    if not current.exists():
        raise ReleaseStoreError("current release is unreadable")
    active = current.resolve(strict=True)
    prior = releases / prior_sha
    if active == prior.resolve(strict=False):
        return prior
    if active != (releases / failed_sha).resolve(strict=False):
        raise ReleaseStoreError(f"current no longer points at failed release {failed_sha}")
    if not prior.is_dir() or prior.is_symlink():
        raise ReleaseStoreError(f"prior release is unavailable: {prior_sha}")
    try:
        marker = (prior / _ORIGIN_SHA_MARKER).read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ReleaseStoreError(f"prior release marker is unreadable: {prior_sha}") from error
    if marker != prior_sha:
        raise ReleaseStoreError(f"prior release marker does not match {prior_sha}")
    activate_release(current, prior)
    return prior


def _verify_current(sha: str, store_root: Path) -> None:
    _releases, current = _layout(store_root)
    if not current.is_symlink():
        raise ReleaseStoreError("current is not a symlink")
    if current.resolve().name != sha:
        raise ReleaseStoreError(f"current does not point at {sha}")
    marker = (current / _ORIGIN_SHA_MARKER).read_text(encoding="utf-8").strip()
    if marker != sha:
        raise ReleaseStoreError(f"current/.origin-sha is {marker}, expected {sha}")


def _configured_store_root() -> Path:
    module_root = Path(__file__).resolve().parent
    import_root = (
        module_root
        if (module_root / "automation" / "node_config.py").is_file()
        else module_root.parent
    )
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))
    from automation.node_config import load_node_config
    return load_node_config().service_root


def _require_root_for_default_store(store_root: Path) -> None:
    if store_root == _configured_store_root() and os.geteuid() != 0:
        raise ReleaseStoreError("privileged release install must run as root")


def main() -> int:
    parser = argparse.ArgumentParser(prog="release-store")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    _ = install.add_argument("--sha", required=True)
    _ = install.add_argument("--store-root", type=Path)
    _ = install.add_argument("--git-root", type=Path)
    verify = commands.add_parser("current")
    _ = verify.add_argument("--verify", required=True, dest="sha")
    _ = verify.add_argument("--store-root", type=Path)
    rollback = commands.add_parser("rollback")
    _ = rollback.add_argument("--failed-sha", required=True)
    _ = rollback.add_argument("--sha", required=True)
    _ = rollback.add_argument("--store-root", type=Path)
    args = cast(_CliArgs, cast(object, parser.parse_args()))
    store_root = args.store_root if args.store_root is not None else _configured_store_root()
    if args.command == "install":
        _require_root_for_default_store(store_root)
        if store_root == _configured_store_root() and args.git_root is None:
            raise ReleaseStoreError("privileged release install requires --git-root")
        release = _install(args.sha, store_root, sys.stdin.buffer.read(), git_root=args.git_root)
        print(f"RELEASE-STORE-OK installed={release} current->{args.sha}")
        return 0
    if args.command == "rollback":
        _require_root_for_default_store(store_root)
        release = rollback_current(store_root, failed_sha=args.failed_sha, prior_sha=args.sha)
        print(f"RELEASE-STORE-OK rolled-back={release}")
        return 0
    _verify_current(args.sha, store_root)
    print(f"RELEASE-STORE-OK current=={args.sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseStoreError as error:
        print(f"RELEASE-STORE-BLOCK: {error}", file=sys.stderr)
        raise SystemExit(2)
