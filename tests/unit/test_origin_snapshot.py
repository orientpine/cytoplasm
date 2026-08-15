"""SHA-pinned origin snapshot: deploy source is a fresh worktree at a verified
origin/main SHA, NOT the resident /srv/autophagy-agents checkout.

WHY (2026-07-31): a parallel session's uncommitted edits in the resident deploy
checkout block ``git pull --ff-only``, so already-merged code cannot deploy.
``origin_snapshot_run`` sidesteps the resident working tree entirely: it fetches
origin, pins an EXPECTED sha, materializes exactly that commit in an ephemeral
``git worktree add --detach`` tree, runs a command inside it, and cleans up —
preserving the command's exit code. A dirty or ahead resident checkout can no
longer block or contaminate a deploy.

The primitive is shell, so it is exercised as shell: a bare origin plus a
resident "mirror" clone built in ``tmp_path`` — no node, no network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SNAPSHOT = _REPO / "automation" / "origin_snapshot.sh"
_PROVENANCE = _REPO / "automation" / "deploy_provenance.sh"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    )


def _init_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "snap@test.local")
    _git(repo, "config", "user.name", "snap")
    _git(repo, "config", "commit.gpgsign", "false")


def _origin_and_mirror(tmp_path: Path) -> tuple[Path, Path, str]:
    """A bare origin with commit A, and a resident mirror clone of it. Returns sha_A."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ("git", "init", "--bare", "--initial-branch=main", str(origin)),
        check=True, capture_output=True, text=True,
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    _init_identity(seed)
    (seed / "content.txt").write_text("A\n", encoding="utf-8")
    _git(seed, "add", "content.txt")
    _git(seed, "commit", "-m", "commit A")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    sha_a = _git(seed, "rev-parse", "HEAD").stdout.strip()

    mirror = tmp_path / "mirror"
    subprocess.run(
        ("git", "clone", str(origin), str(mirror)), check=True, capture_output=True, text=True
    )
    _init_identity(mirror)
    return origin, mirror, sha_a


def _run_snapshot(
    tmp_path: Path, mirror: Path, expected_sha: str, command: str
) -> subprocess.CompletedProcess[str]:
    """Source the primitive and invoke origin_snapshot_run with a shell command."""
    script = (
        f'source "{_SNAPSHOT}"\n'
        f'origin_snapshot_run "{mirror}" "{expected_sha}" bash -c {_q(command)}\n'
    )
    return subprocess.run(
        ("bash", "-c", script),
        cwd=str(tmp_path), capture_output=True, text=True, check=False,
    )


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _push_second_commit(tmp_path: Path, origin: Path) -> str:
    """A THIRD clone pushes commit B to origin, moving main past sha_A."""
    third = tmp_path / "third"
    subprocess.run(
        ("git", "clone", str(origin), str(third)), check=True, capture_output=True, text=True
    )
    _init_identity(third)
    (third / "content.txt").write_text("B\n", encoding="utf-8")
    _git(third, "add", "content.txt")
    _git(third, "commit", "-m", "commit B")
    _git(third, "push", "origin", "main")
    return _git(third, "rev-parse", "HEAD").stdout.strip()


def _worktree_count(mirror: Path) -> int:
    out = _git(mirror, "worktree", "list", "--porcelain").stdout
    return out.count("worktree ")


# 1.1 happy path + provenance-ref invariant ---------------------------------

def test_snapshot_runs_command_at_pinned_sha(tmp_path: Path) -> None:
    _origin, mirror, sha_a = _origin_and_mirror(tmp_path)
    result = _run_snapshot(
        tmp_path, mirror, sha_a, "git rev-parse HEAD > $OLDPWD/seen"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "seen").read_text(encoding="utf-8").strip() == sha_a
    # The ephemeral worktree must be gone; only the mirror's own worktree remains.
    assert _worktree_count(mirror) == 1


def test_deploy_provenance_ref_default_unchanged() -> None:
    # The snapshot must NOT tempt anyone to pin the provenance guard to the
    # snapshot sha — it stays an origin/main race detector.
    text = _PROVENANCE.read_text(encoding="utf-8")
    assert "DEPLOY_PROVENANCE_REF:-origin/main" in text


# 1.3 remote-sha race: fail BEFORE running the command -----------------------

def test_remote_sha_race_fails_before_command(tmp_path: Path) -> None:
    origin, mirror, sha_a = _origin_and_mirror(tmp_path)
    _push_second_commit(tmp_path, origin)  # origin/main now past sha_a
    result = _run_snapshot(
        tmp_path, mirror, sha_a, "echo ran > $OLDPWD/ran"
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "SNAPSHOT-BLOCK" in result.stderr
    assert not (tmp_path / "ran").exists()  # command must never have run


# 1.4 resident-ahead commit must not be materialized -------------------------

def test_resident_ahead_commit_is_not_materialized(tmp_path: Path) -> None:
    _origin, mirror, sha_a = _origin_and_mirror(tmp_path)
    # Simulate the 2026-07-27 fault: a commit made INSIDE the resident checkout.
    (mirror / "resident_only.txt").write_text("only here\n", encoding="utf-8")
    _git(mirror, "add", "resident_only.txt")
    _git(mirror, "commit", "-m", "resident-only commit")
    result = _run_snapshot(
        tmp_path, mirror, sha_a,
        "test -e resident_only.txt && echo LEAKED > $OLDPWD/leak || true",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "leak").exists()  # snapshot is sha_a, not resident HEAD


# 1.5 cleanup on command failure, exit code preserved ------------------------

def test_snapshot_cleanup_on_command_failure(tmp_path: Path) -> None:
    _origin, mirror, sha_a = _origin_and_mirror(tmp_path)
    result = _run_snapshot(tmp_path, mirror, sha_a, "exit 7")
    assert result.returncode == 7, result.stdout + result.stderr
    assert _worktree_count(mirror) == 1  # ephemeral worktree cleaned up


# 1.6 two concurrent runs get distinct paths, both clean ---------------------

def test_concurrent_runs_get_distinct_paths_and_both_clean(tmp_path: Path) -> None:
    _origin, mirror, sha_a = _origin_and_mirror(tmp_path)
    journal = tmp_path / "paths.log"
    command = f'echo "$AUTOPHAGY_SNAPSHOT_DIR" >> {_q(str(journal))}; sleep 0.5'
    script = (
        f'source "{_SNAPSHOT}"\n'
        f'origin_snapshot_run "{mirror}" "{sha_a}" bash -c {_q(command)} &\n'
        f'origin_snapshot_run "{mirror}" "{sha_a}" bash -c {_q(command)} &\n'
        "wait\n"
    )
    result = subprocess.run(
        ("bash", "-c", script), cwd=str(tmp_path),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    paths = [line for line in journal.read_text(encoding="utf-8").splitlines() if line]
    assert len(paths) == 2 and paths[0] != paths[1]  # distinct snapshot dirs
    assert _worktree_count(mirror) == 1  # both ephemeral trees cleaned up


# 1.7 the whole point: a dirty resident mirror does not block ----------------

def test_dirty_resident_mirror_does_not_block_snapshot(tmp_path: Path) -> None:
    _origin, mirror, sha_a = _origin_and_mirror(tmp_path)
    # A parallel session's uncommitted edit in the resident checkout.
    (mirror / "content.txt").write_text("uncommitted parallel edit\n", encoding="utf-8")
    result = _run_snapshot(
        tmp_path, mirror, sha_a, "cat content.txt > $OLDPWD/got"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "got").read_text(encoding="utf-8") == "A\n"  # committed bytes
