"""Progressive resource limits for submission archive extraction (F5 residual A).

``extract_archive`` used to call ``archive.getmembers()`` before checking the
member count or the byte cap.  On a ``r:*`` gzip stream that walk decompresses
every member payload, so a small gzip declaring huge member sizes cost a full
decompression *before* any cap could reject it.

Each archive below ends with a GNU long-name header whose continuation block is
unreadable.  ``tarfile`` propagates that as a ``ReadError`` for any reader that
walks that far, which makes the two behaviours distinguishable by error message
rather than by wall-clock timing: a reader that enumerates the whole archive
first reports "cannot be opened", while a reader that stops at the first
breached cap reports the cap it breached and never touches the tail.

Measured on the byte-cap fixture: a ~24 KiB gzip declaring ~24 MiB of members.
"""

from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import pytest

from automation.managed_skills.submission_archive import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    _copy_within,
    extract_archive,
    write_tarball,
)
from automation.managed_skills.submission_errors import SubmissionArtifactError

def _unreadable_tail() -> bytes:
    """A GNU long-name header whose continuation block cannot be parsed.

    A merely corrupt 512-byte block is NOT enough: ``TarFile.next`` swallows an
    ``InvalidHeaderError`` at a non-zero offset and reports end-of-archive. Only
    a bad header *following* a long-name marker raises ``SubsequentHeaderError``,
    which propagates — that is what makes this tail an unmissable tripwire.
    """
    marker = tarfile.TarInfo("././@LongLink")
    marker.type = tarfile.GNUTYPE_LONGNAME
    marker.mode = 0o644
    marker.size = tarfile.BLOCKSIZE
    marker.mtime = 0
    padding = b"x" * tarfile.BLOCKSIZE
    return marker.tobuf(tarfile.GNU_FORMAT) + padding + b"\xff" * tarfile.BLOCKSIZE


def _file_header(name: str, size: int) -> bytes:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = 0o644
    info.size = size
    info.mtime = 0
    return info.tobuf(tarfile.GNU_FORMAT)


def _write_gzip(path: Path, raw: bytes) -> None:
    with path.open("wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as compressed:
            _ = compressed.write(raw)


def _oversized_then_corrupt(path: Path) -> None:
    """One member declaring more than the byte cap, then an unreadable header."""
    declared = MAX_ARCHIVE_BYTES + tarfile.BLOCKSIZE
    payload = b"\0" * declared
    _write_gzip(path, _file_header("bomb/big.bin", declared) + payload + _unreadable_tail())


def _too_many_then_corrupt(path: Path) -> None:
    """One member past the member cap, then an unreadable header."""
    blocks = b"".join(
        _file_header(f"bomb/{index}.txt", 0) for index in range(MAX_ARCHIVE_MEMBERS + 1)
    )
    _write_gzip(path, blocks + _unreadable_tail())


def test_byte_cap_aborts_before_the_archive_is_enumerated(tmp_path: Path) -> None:
    # Given: a small gzip whose first member declares more than the byte cap,
    # followed by a header no full enumeration could survive.
    archive = tmp_path / "bomb.tar.gz"
    _oversized_then_corrupt(archive)
    destination = tmp_path / "out"
    destination.mkdir()

    # When / Then: the cap is reported, proving the tail was never decompressed.
    with pytest.raises(SubmissionArtifactError, match="exceeds the size limit"):
        extract_archive(archive, destination)


def test_member_cap_aborts_before_the_archive_is_enumerated(tmp_path: Path) -> None:
    # Given: a gzip carrying one member more than the cap allows, then a header
    # no full enumeration could survive.
    archive = tmp_path / "bomb.tar.gz"
    _too_many_then_corrupt(archive)
    destination = tmp_path / "out"
    destination.mkdir()

    # When / Then: the member cap is reported, not a read failure on the tail.
    with pytest.raises(SubmissionArtifactError, match="too many members"):
        extract_archive(archive, destination)


def test_a_hostile_archive_never_writes_more_than_its_declared_member_size(
    tmp_path: Path,
) -> None:
    # Given: a member stream that yields more bytes than its header declared.
    target = tmp_path / "member.bin"

    # When / Then: the bounded copy refuses rather than filling the disk.
    with pytest.raises(SubmissionArtifactError, match="outgrew its declared size"):
        _copy_within(io.BytesIO(b"a" * 4096), target, 16)


def test_a_legitimate_submission_tarball_still_round_trips(tmp_path: Path) -> None:
    # Given: a personal skill packaged by this module's own writer.
    source = tmp_path / "personal"
    (source / "scripts").mkdir(parents=True)
    _ = (source / "SKILL.md").write_text("---\nname: personal\n---\nbody\n", encoding="utf-8")
    _ = (source / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    archive = tmp_path / "submission.tar.gz"
    write_tarball(source, "managed-personal", archive)
    destination = tmp_path / "out"
    destination.mkdir()

    # When: it is extracted under the progressive limits.
    extract_archive(archive, destination)

    # Then: every regular file arrives intact with no false-positive rejection.
    root = destination / "managed-personal"
    assert root.is_dir()
    assert (root / "SKILL.md").read_text(encoding="utf-8") == "---\nname: personal\n---\nbody\n"
    assert (root / "scripts" / "run.py").read_text(encoding="utf-8") == "print('ok')\n"
