"""The reconciler also carries the observation mirror forward — but only ever safely.

DG-5 demoted ``/srv/autophagy-agents`` from prod to an observation post, and the
2-minute reconcile timer converges the *release* by building a detached snapshot
worktree: it fetches ``refs/remotes/origin/main`` and never moves the mirror's HEAD.
So every landing left the mirror behind and nothing ever brought it forward — only a
human running ``land.sh`` did, and branch work reaches main by PR, which never runs it.
Measured on <primary-node>: repeated ``mirror-behind`` healthcheck failures in one window.

Two properties make this write safe to put in an unattended timer:

* it fast-forwards ONLY on the ``mirror-behind`` verdict, and that verdict comes from
  the same ``checkout_mirror_probe.sh`` the healthcheck and ``land.sh`` already share.
  A dirty or ahead mirror holds work that exists nowhere else (2026-07-27 선례), so a
  reconciler that "fixed" it would be the destructive repair the guidance forbids.
* it runs only once prod has actually REACHED origin/main. While the release is stale
  the mirror's own lag is the healthcheck's evidence of it, and erasing that evidence
  would make a broken convergence look healthy.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from automation.deploy_reconcile_cli import (
    MIRROR_IN_SYNC,
    MIRROR_PROD_STALE,
    MIRROR_PULLED,
    sync_mirror,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    ).stdout.strip()


def _mirror(tmp_path: Path) -> Path:
    """A deploy checkout in its healthy shape: HEAD == origin/main, nothing modified."""
    origin = tmp_path / "origin.git"
    _ = subprocess.run(
        ("git", "init", "--bare", "--initial-branch=main", str(origin)),
        check=True, capture_output=True, text=True,
    )
    checkout = tmp_path / "autophagy-agents"
    checkout.mkdir()
    _ = _git(checkout, "init", "--initial-branch=main")
    _ = _git(checkout, "config", "user.email", "ops@test.local")
    _ = _git(checkout, "config", "user.name", "ops")
    _ = _git(checkout, "config", "commit.gpgsign", "false")
    (checkout / "SKILL.md").write_text("version: 1.5.3\n", encoding="utf-8")
    _ = _git(checkout, "add", "SKILL.md")
    _ = _git(checkout, "commit", "-m", "deployed state")
    _ = _git(checkout, "remote", "add", "origin", str(origin))
    _ = _git(checkout, "push", "-u", "origin", "main")
    return checkout


def _advance_origin(tmp_path: Path) -> str:
    """Move the bare origin one commit ahead. Returns the new origin/main sha."""
    mover = tmp_path / "mover"
    _ = subprocess.run(
        ("git", "clone", "-b", "main", str(tmp_path / "origin.git"), str(mover)),
        check=True, capture_output=True, text=True,
    )
    _ = _git(mover, "config", "user.email", "mover@test.local")
    _ = _git(mover, "config", "user.name", "mover")
    _ = _git(mover, "config", "commit.gpgsign", "false")
    (mover / "SKILL.md").write_text("version: 1.6.0\n", encoding="utf-8")
    _ = _git(mover, "commit", "-am", "a commit only origin has")
    _ = _git(mover, "push", "origin", "main")
    return _git(mover, "rev-parse", "HEAD")


def _release_pointer(tmp_path: Path, sha: str) -> Path:
    """The live release pointer, named by the generation prod is running."""
    generation = tmp_path / "releases" / sha
    generation.mkdir(parents=True, exist_ok=True)
    pointer = tmp_path / "current"
    if pointer.is_symlink():
        pointer.unlink()
    pointer.symlink_to(generation, target_is_directory=True)
    return pointer


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def test_a_behind_mirror_is_fast_forwarded_once_prod_is_at_origin(tmp_path: Path) -> None:
    mirror = _mirror(tmp_path)
    origin_sha = _advance_origin(tmp_path)

    outcome = sync_mirror(
        origin_sha, mirror=mirror, pointer=_release_pointer(tmp_path, origin_sha)
    )

    assert outcome == MIRROR_PULLED, outcome
    assert _head(mirror) == origin_sha


def test_an_already_current_mirror_touches_nothing_and_asks_nothing(tmp_path: Path) -> None:
    """The steady state must cost no network: this is a 2-minute timer.

    Origin is pointed at a path that does not exist, so any ``ls-remote`` or ``pull``
    would fail loudly. Returning in-sync proves neither was reached.
    """
    mirror = _mirror(tmp_path)
    head = _head(mirror)
    _ = _git(mirror, "remote", "set-url", "origin", str(tmp_path / "gone.git"))

    outcome = sync_mirror(head, mirror=mirror, pointer=_release_pointer(tmp_path, head))

    assert outcome == MIRROR_IN_SYNC
    assert _head(mirror) == head


def test_a_dirty_mirror_is_never_touched(tmp_path: Path) -> None:
    """An edit made in prod exists nowhere else — the reconciler must not resolve it."""
    mirror = _mirror(tmp_path)
    origin_sha = _advance_origin(tmp_path)
    (mirror / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
    before = _head(mirror)

    outcome = sync_mirror(
        origin_sha, mirror=mirror, pointer=_release_pointer(tmp_path, origin_sha)
    )

    assert "mirror-dirty" in outcome, outcome
    assert _head(mirror) == before
    assert (mirror / "SKILL.md").read_text(encoding="utf-8") == "version: 1.5.5\n"


def test_a_mirror_holding_its_own_commit_is_never_touched(tmp_path: Path) -> None:
    """The exact 2026-07-27 shape. Stranded commits are recovered, never overrun."""
    mirror = _mirror(tmp_path)
    origin_sha = _advance_origin(tmp_path)
    (mirror / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
    _ = _git(mirror, "commit", "-am", "learned in prod, never pushed")
    stranded = _head(mirror)

    outcome = sync_mirror(
        origin_sha, mirror=mirror, pointer=_release_pointer(tmp_path, origin_sha)
    )

    assert "mirror-ahead" in outcome, outcome
    assert _head(mirror) == stranded


def test_the_mirror_is_left_behind_while_prod_is_stale(tmp_path: Path) -> None:
    """A behind mirror is the healthcheck's evidence that the release never landed.

    Fast-forwarding it here would make a failed convergence look like a healthy node.
    """
    mirror = _mirror(tmp_path)
    stale = _head(mirror)
    origin_sha = _advance_origin(tmp_path)

    outcome = sync_mirror(
        origin_sha, mirror=mirror, pointer=_release_pointer(tmp_path, stale)
    )

    assert outcome.startswith(MIRROR_PROD_STALE), outcome
    assert _head(mirror) == stale


def test_a_missing_release_pointer_leaves_the_mirror_alone(tmp_path: Path) -> None:
    """No release installed means the mirror IS prod — this timer does not touch prod."""
    mirror = _mirror(tmp_path)
    stale = _head(mirror)
    origin_sha = _advance_origin(tmp_path)

    outcome = sync_mirror(origin_sha, mirror=mirror, pointer=tmp_path / "no-release")

    assert outcome.startswith(MIRROR_PROD_STALE), outcome
    assert _head(mirror) == stale


def test_a_broken_mirror_path_reports_instead_of_raising(tmp_path: Path) -> None:
    """Best effort: nothing about the observation post may end the reconciliation tick."""
    sha = "a" * 40
    outcome = sync_mirror(
        sha, mirror=tmp_path / "not-a-checkout", pointer=_release_pointer(tmp_path, sha)
    )

    assert isinstance(outcome, str) and outcome
