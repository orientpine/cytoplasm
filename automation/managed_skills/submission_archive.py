"""Deterministic tar creation and traversal-safe extraction for submissions."""

from __future__ import annotations

import gzip
import tarfile
from pathlib import Path, PurePosixPath
from typing import IO, Final

from automation.managed_skills.submission_errors import SubmissionArtifactError

MAX_ARCHIVE_BYTES: Final = 24 * 1024 * 1024
MAX_ARCHIVE_MEMBERS: Final = 1024
_COPY_CHUNK: Final = 256 * 1024


def _safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise SubmissionArtifactError(f"submission archive has an unsafe path: {name!r}")
    return path


def _copy_within(stream: IO[bytes], target: Path, limit: int) -> None:
    """Copy a member in bounded chunks, refusing one that outgrows its header."""
    written = 0
    with target.open("wb") as output:
        while chunk := stream.read(_COPY_CHUNK):
            written += len(chunk)
            if written > limit:
                raise SubmissionArtifactError("submission archive member outgrew its declared size")
            _ = output.write(chunk)


def extract_archive(archive_path: Path, destination: Path) -> None:
    """Extract regular files and directories only, within explicit resource limits.

    Every limit is applied **progressively**, one member at a time. A ``r:*``
    stream is decompressed while its headers are walked, so enumerating the
    whole archive up front (``getmembers()``) would decompress every payload
    before any cap could reject it: a small gzip declaring huge member sizes
    would cost a full decompression to refuse. Reading member by member and
    aborting on the first member that breaches a cap bounds the work done for a
    hostile archive to roughly one cap's worth of output.
    """
    total = 0
    members = 0
    seen: set[PurePosixPath] = set()
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive:
                members += 1
                if members > MAX_ARCHIVE_MEMBERS:
                    raise SubmissionArtifactError("submission archive contains too many members")
                relative = _safe_member_path(member.name)
                if relative in seen:
                    raise SubmissionArtifactError(f"submission archive repeats a path: {relative}")
                seen.add(relative)
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(mode=member.mode & 0o777, parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise SubmissionArtifactError(
                        f"submission archive member is not a regular file: {relative}"
                    )
                total += member.size
                if total > MAX_ARCHIVE_BYTES:
                    raise SubmissionArtifactError("submission archive exceeds the size limit")
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise SubmissionArtifactError(f"submission archive member is unreadable: {relative}")
                with stream:
                    _copy_within(stream, target, member.size)
                target.chmod(member.mode & 0o777)
    except (OSError, tarfile.TarError) as error:
        raise SubmissionArtifactError(f"submission archive cannot be opened: {archive_path}") from error


def _tar_info(name: str, mode: int, *, directory: bool, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = mode & 0o777
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def write_tarball(source: Path, skill: str, destination: Path) -> None:
    """Write a byte-stable gzip tar whose sole root is the managed skill name."""
    try:
        with destination.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    archive.addfile(_tar_info(f"{skill}/", 0o755, directory=True))
                    paths = sorted(
                        source.rglob("*"),
                        key=lambda item: item.relative_to(source).as_posix(),
                    )
                    for path in paths:
                        if path.is_symlink():
                            raise SubmissionArtifactError("personal skill contains a symlink")
                        relative = path.relative_to(source).as_posix()
                        name = f"{skill}/{relative}"
                        mode = path.stat().st_mode
                        if path.is_dir():
                            archive.addfile(_tar_info(f"{name}/", mode, directory=True))
                        elif path.is_file():
                            info = _tar_info(name, mode, directory=False, size=path.stat().st_size)
                            with path.open("rb") as handle:
                                archive.addfile(info, handle)
                        else:
                            raise SubmissionArtifactError(
                                f"personal skill has an unsupported entry: {relative}"
                            )
    except OSError as error:
        raise SubmissionArtifactError(f"cannot create submission tarball: {destination}") from error
