"""Mutual exclusion, pack hygiene, and blob-less fetch setup for the shared write clone.

Two no-agent watchers -- ``plaud_sync`` and ``memory_relocate`` -- call ``write_note``
against the *same* clone directory from independent cron ticks, so their
fetch → upsert → commit → push spans can interleave inside one working tree. The
``git reset --hard`` of one tick then discards the note the other tick has just staged,
and an approved note disappears with no error raised anywhere.

The lock lives *beside* the clone rather than inside it: everything under ``clone_dir``
is within reach of ``git reset --hard`` and ``git clean``, and the lock has to exist
before the very first clone creates that directory at all.

Following ``automation/pipeline_lock.py`` and rule (n) of
``docs/guide/watcher-cron-설계규약.md``, the loser **yields** instead of waiting: blocking
a cron tick behind a fetch that may run for minutes only stacks ticks up behind each
other. Yielding is not a failure, so it surfaces as a *retryable* error and the next
tick picks the note up.
"""

from __future__ import annotations

import fcntl
import sys
from pathlib import Path
from typing import Final, Protocol, TextIO

from .config import ObsidianWriteError

LOCK_SUFFIX: Final = ".lock"
BLOB_FILTER: Final = "blob:none"
_FILTER_KEY: Final = "remote.origin.partialclonefilter"
_PROMISOR_KEY: Final = "remote.origin.promisor"
_BUSY: Final = "Obsidian write skipped: another writer holds the clone"


class GitStep(Protocol):
    """Runs one git argv inside the write clone and returns its stdout."""

    def __call__(self, argv: tuple[str, ...], step: str, /) -> str: ...


def lock_path(clone_dir: Path) -> Path:
    """The one file every writer of this clone contends on."""
    return clone_dir.parent / (clone_dir.name + LOCK_SUFFIX)


class _CloneLock:
    """Releases the clone lock when the guarded write span ends.

    Deliberately *not* ``@contextlib.contextmanager``: contextlib re-raises a body
    exception through the generator and then assigns ``exc.__traceback__``, but
    ``ObsidianWriteError`` is a frozen ``slots=True`` dataclass whose generated
    ``__setattr__`` answers that assignment with ``TypeError``. A generator-based lock
    would therefore replace every genuine write failure raised inside the span with an
    unrelated TypeError -- push refusals included.
    """

    __slots__ = ("_handle",)

    def __init__(self, handle: TextIO) -> None:
        self._handle: TextIO = handle

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exception: object) -> None:
        self._handle.close()


def hold(clone_dir: Path) -> _CloneLock:
    """Take the clone's exclusive lock for one write span, or refuse without waiting.

    A lock that cannot even be opened is treated as held: an unreadable lock is
    indistinguishable from a taken one, and guessing wrong is exactly the interleaving
    this module exists to prevent.
    """
    path = lock_path(clone_dir)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = path.open("w", encoding="utf-8")
    except OSError as error:
        raise ObsidianWriteError(_BUSY, True) from error
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise ObsidianWriteError(_BUSY, True) from error
    return _CloneLock(handle)


def purge_stale_tmp_packs(clone_dir: Path) -> tuple[Path, ...]:
    """Delete fetch temporaries orphaned by killed fetches, reporting each removal.

    ``git fetch`` streams an incoming pack into ``.git/objects/pack/tmp_pack_*`` and
    renames it only once the transfer completes. A fetch killed by our own subprocess
    timeout never reaches that rename and never cleans up after itself, so on
    2026-09-02 this clone held 230 such corpses -- 176 GB, one ~770 MB file per tick
    for a month. git prunes them only during a ``gc`` this clone never runs, and only
    after ``gc.pruneExpire``. Purging is safe precisely because the caller holds the
    lock: no fetch of ours can own a tmp pack at that moment.
    """
    pack_dir = clone_dir / ".git" / "objects" / "pack"
    removed: list[Path] = []
    for candidate in sorted(pack_dir.glob("tmp_pack_*")):
        try:
            size = candidate.stat().st_size
            candidate.unlink()
        except OSError as error:
            print(f"obsidian-write: could not remove {candidate}: {error}", file=sys.stderr)
            continue
        removed.append(candidate)
        print(
            f"obsidian-write: removed stale fetch pack {candidate} ({size} bytes)",
            file=sys.stderr,
        )
    return tuple(removed)


def ensure_blobless_fetch(step: GitStep) -> bool:
    """Convert an existing full clone so later fetches carry commits and trees, no blobs.

    ``git clone --filter=blob:none`` only helps clones created with it; a clone made
    before this change keeps downloading every blob of every fetched commit forever,
    which is the ~770 MB per tick. Writing the two keys git itself sets for a partial
    clone converts the directory in place -- no re-clone, no working tree lost.

    The probe uses ``--default ''`` so an unset key returns an empty string instead of
    exit code 1, which keeps the already-converted case at one read and zero writes.
    Returns whether this call performed the conversion.
    """
    current = step(("git", "config", "--default", "", "--get", _FILTER_KEY), "read clone filter")
    if current.strip() == BLOB_FILTER:
        return False
    _ = step(("git", "config", _PROMISOR_KEY, "true"), "mark origin a promisor remote")
    _ = step(("git", "config", _FILTER_KEY, BLOB_FILTER), "enable blob-less fetches")
    return True
