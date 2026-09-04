"""Contracts for the write clone's mutual exclusion, pack hygiene, and blob-less fetch.

The partial-clone case is proven against a **real** temporary bare repository rather
than a fake runner: whether ``remote.origin.partialclonefilter`` actually keeps a blob
off the disk is a property of git's transport negotiation, not of our argv strings, and
a mock would happily confirm a conversion that git ignores.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from automation.obsidian_write import ObsidianWriteError, clone_lock

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "obsidian-write-test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "obsidian-write-test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    # Keep the developer's own git config out of the fixture repositories.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(*args: str, cwd: Path, extra_env: dict[str, str] | None = None) -> str:
    environment = {**os.environ, **_GIT_IDENTITY, **(extra_env or {})}
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return result.stdout.strip()


def _git_status(*args: str, cwd: Path, extra_env: dict[str, str] | None = None) -> int:
    environment = {**os.environ, **_GIT_IDENTITY, **(extra_env or {})}
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    ).returncode


def test_lock_path_is_a_sibling_of_the_clone_directory(tmp_path: Path) -> None:
    # Given / When
    path = clone_lock.lock_path(tmp_path / "obsidian-write")

    # Then: a lock inside the clone would be in reach of `git reset --hard`/`git clean`,
    # and it must exist before the very first clone creates the directory.
    assert path == tmp_path / "obsidian-write.lock"


def test_hold_refuses_a_second_writer_with_a_retryable_error(tmp_path: Path) -> None:
    # Given: plaud_sync holds the clone when memory_relocate's tick arrives.
    clone_dir = tmp_path / "obsidian-write"
    _ = clone_dir.mkdir()

    # When / Then
    with clone_lock.hold(clone_dir):
        with pytest.raises(ObsidianWriteError, match="another writer") as captured:
            with clone_lock.hold(clone_dir):
                pytest.fail("the second writer must not enter the clone")
    assert captured.value.retryable is True


def test_hold_releases_the_clone_for_the_next_tick(tmp_path: Path) -> None:
    # Given
    clone_dir = tmp_path / "obsidian-write"
    _ = clone_dir.mkdir()

    # When
    with clone_lock.hold(clone_dir):
        pass

    # Then: yielding is not a failure, so the next cron tick must be able to take over.
    with clone_lock.hold(clone_dir):
        pass


def test_hold_treats_an_unopenable_lock_as_held(tmp_path: Path) -> None:
    # Given: an unreadable lock is indistinguishable from a held one.
    if os.geteuid() == 0:
        pytest.skip("root bypasses the directory permission that makes the lock unopenable")
    parent = tmp_path / "locked-parent"
    _ = parent.mkdir()
    clone_dir = parent / "obsidian-write"
    _ = parent.chmod(0o500)
    try:
        # When / Then
        with pytest.raises(ObsidianWriteError, match="another writer") as captured:
            with clone_lock.hold(clone_dir):
                pytest.fail("an unopenable lock must never be read as free")
    finally:
        _ = parent.chmod(0o700)
    assert captured.value.retryable is True


def test_purge_stale_tmp_packs_removes_dead_fetch_temporaries_and_reports_them(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the residue of a fetch killed by the timeout (field data: 230 files, 176 GB).
    clone_dir = tmp_path / "obsidian-write"
    pack_dir = clone_dir / ".git" / "objects" / "pack"
    _ = pack_dir.mkdir(parents=True)
    stale = pack_dir / "tmp_pack_9WkQ2x"
    _ = stale.write_bytes(b"\0" * 4096)
    keeper = pack_dir / "pack-cafebabe.pack"
    _ = keeper.write_bytes(b"real pack")

    # When
    removed = clone_lock.purge_stale_tmp_packs(clone_dir)

    # Then
    assert removed == (stale,)
    assert not stale.exists()
    assert keeper.exists(), "a completed pack must never be mistaken for fetch residue"
    stderr = capsys.readouterr().err
    assert "tmp_pack_9WkQ2x" in stderr
    assert "4096" in stderr


def test_purge_stale_tmp_packs_tolerates_a_clone_that_does_not_exist_yet(tmp_path: Path) -> None:
    # Given / When / Then: the purge runs before the first clone, too.
    assert clone_lock.purge_stale_tmp_packs(tmp_path / "absent") == ()


@pytest.fixture
def origin_with_unfetched_blob(tmp_path: Path) -> tuple[Path, Path, str]:
    """A full clone plus an origin commit whose blob that clone has never seen."""
    origin = tmp_path / "origin.git"
    _ = _git("init", "--quiet", "--bare", str(origin), cwd=tmp_path)
    # GitHub enables filtering server-side; a fixture bare repo must opt in explicitly.
    _ = _git("config", "uploadpack.allowfilter", "true", cwd=origin)

    work = tmp_path / "work"
    _ = work.mkdir()
    _ = _git("init", "--quiet", "--initial-branch", "main", cwd=work)
    _ = (work / "a.txt").write_text("first note\n", encoding="utf-8")
    _ = _git("add", "a.txt", cwd=work)
    _ = _git("commit", "--quiet", "-m", "first", cwd=work)
    _ = _git("remote", "add", "origin", str(origin), cwd=work)
    _ = _git("push", "--quiet", "origin", "main", cwd=work)

    full_clone = tmp_path / "obsidian-write"
    _ = _git("clone", "--quiet", "--branch", "main", "--", str(origin), str(full_clone), cwd=tmp_path)

    _ = (work / "b.txt").write_text("a blob the clone never checked out\n", encoding="utf-8")
    _ = _git("add", "b.txt", cwd=work)
    _ = _git("commit", "--quiet", "-m", "second", cwd=work)
    _ = _git("push", "--quiet", "origin", "main", cwd=work)
    blob = _git("rev-parse", "HEAD:b.txt", cwd=work)
    return origin, full_clone, blob


def test_conversion_makes_a_full_clone_fetch_without_downloading_new_blobs(
    origin_with_unfetched_blob: tuple[Path, Path, str],
) -> None:
    # Given
    _origin, full_clone, blob = origin_with_unfetched_blob

    def step(argv: tuple[str, ...], _step: str, /) -> str:
        return _git(*argv[1:], cwd=full_clone)

    # When: the existing full clone is converted, then fetches the new commit.
    converted = clone_lock.ensure_blobless_fetch(step)
    _ = _git("fetch", "--quiet", "origin", "main", cwd=full_clone)

    # Then: the commit arrived but its blob did not.
    assert converted is True
    assert _git("rev-parse", "origin/main:b.txt", cwd=full_clone) == blob
    missing = _git("rev-list", "--objects", "--missing=print", "origin/main", cwd=full_clone)
    assert f"?{blob}" in missing.splitlines()
    assert (
        _git_status("cat-file", "-e", blob, cwd=full_clone, extra_env={"GIT_NO_LAZY_FETCH": "1"}) != 0
    ), "the blob must still be absent locally"

    # And: it materialises only when the working tree actually needs it.
    _ = _git("checkout", "origin/main", "--", "b.txt", cwd=full_clone)
    assert (
        _git_status("cat-file", "-e", blob, cwd=full_clone, extra_env={"GIT_NO_LAZY_FETCH": "1"}) == 0
    )


def test_conversion_is_idempotent_on_an_already_blobless_clone(
    origin_with_unfetched_blob: tuple[Path, Path, str],
) -> None:
    # Given
    _origin, full_clone, _blob = origin_with_unfetched_blob
    issued: list[tuple[str, ...]] = []

    def step(argv: tuple[str, ...], _step: str, /) -> str:
        issued.append(argv)
        return _git(*argv[1:], cwd=full_clone)

    _ = clone_lock.ensure_blobless_fetch(step)
    issued.clear()

    # When
    converted = clone_lock.ensure_blobless_fetch(step)

    # Then: a converted clone is probed once and left alone.
    assert converted is False
    assert [argv[1:3] for argv in issued] == [("config", "--default")]
