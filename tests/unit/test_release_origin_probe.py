from __future__ import annotations

import os
import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_PROBE = _REPO / "automation" / "checkout_mirror_probe.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _checkout(tmp_path: Path) -> tuple[Path, str]:
    origin = tmp_path / "origin.git"
    checkout = tmp_path / "checkout"
    _ = subprocess.run(
        ("git", "init", "--bare", "--initial-branch=main", str(origin)),
        check=True,
        capture_output=True,
        text=True,
    )
    checkout.mkdir()
    _git(checkout, "init", "--initial-branch=main")
    _git(checkout, "config", "user.email", "probe@test.local")
    _git(checkout, "config", "user.name", "probe")
    _git(checkout, "config", "commit.gpgsign", "false")
    (checkout / "payload").write_text("one\n", encoding="utf-8")
    _git(checkout, "add", "payload")
    _git(checkout, "commit", "-m", "initial")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-u", "origin", "main")
    return checkout, _git(checkout, "rev-parse", "HEAD")


def _advance_origin(tmp_path: Path) -> str:
    mover = tmp_path / "mover"
    _ = subprocess.run(
        ("git", "clone", "-b", "main", str(tmp_path / "origin.git"), str(mover)),
        check=True,
        capture_output=True,
        text=True,
    )
    _git(mover, "config", "user.email", "mover@test.local")
    _git(mover, "config", "user.name", "mover")
    _git(mover, "config", "commit.gpgsign", "false")
    (mover / "payload").write_text("two\n", encoding="utf-8")
    _git(mover, "commit", "-am", "advance")
    _git(mover, "push", "origin", "main")
    return _git(mover, "rev-parse", "HEAD")


def _release_pointer(tmp_path: Path, sha: str) -> Path:
    generation = tmp_path / "releases" / sha
    generation.mkdir(parents=True)
    pointer = tmp_path / "current"
    pointer.symlink_to(generation, target_is_directory=True)
    return pointer


def _run(
    checkout: Path,
    release: Path,
    *,
    enforce: bool = False,
    mirror_reported_stale: bool = False,
) -> subprocess.CompletedProcess[str]:
    script = (
        f'source "{_PROBE}"; '
        f'RUNTIME_RELEASE_CURRENT="{release}" '
        f'HEALTHCHECK_RECONCILE_STATE="{release}.state" '
        f'RELEASE_STALE_PROBE_ENFORCE={int(enforce)} '
        "checkout_release_origin_grade "
        f'"{checkout}" {int(mirror_reported_stale)}'
    )
    return subprocess.run(
        ("bash", "-c", script),
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ),
    )


def test_release_equal_to_origin_passes(tmp_path: Path) -> None:
    checkout, sha = _checkout(tmp_path)

    result = _run(checkout, _release_pointer(tmp_path, sha))

    assert result.returncode == 0, result.stdout + result.stderr


def test_stale_release_warns_until_owner_enables_enforcement(tmp_path: Path) -> None:
    checkout, stale_sha = _checkout(tmp_path)
    _ = _advance_origin(tmp_path)

    result = _run(checkout, _release_pointer(tmp_path, stale_sha))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE-STALE-WARN" in result.stderr


def test_stale_release_fails_when_enforcement_is_enabled(tmp_path: Path) -> None:
    checkout, stale_sha = _checkout(tmp_path)
    _ = _advance_origin(tmp_path)

    result = _run(checkout, _release_pointer(tmp_path, stale_sha), enforce=True)

    assert result.returncode != 0
    assert "release-stale" in result.stderr


def test_unreachable_origin_degrades_to_behind_unknown(tmp_path: Path) -> None:
    checkout, sha = _checkout(tmp_path)
    _git(checkout, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    result = _run(checkout, _release_pointer(tmp_path, sha), enforce=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "BEHIND-UNKNOWN" in result.stderr


def test_existing_mirror_stale_failure_suppresses_a_second_ticket(tmp_path: Path) -> None:
    checkout, stale_sha = _checkout(tmp_path)
    _ = _advance_origin(tmp_path)

    result = _run(
        checkout,
        _release_pointer(tmp_path, stale_sha),
        enforce=True,
        mirror_reported_stale=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELEASE-STALE-SUPPRESSED-WARN" in result.stderr


def test_release_probe_never_fetches(tmp_path: Path) -> None:
    result = subprocess.run(
        ("bash", "-c", f'source "{_PROBE}"; declare -f checkout_release_origin_grade'),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "git fetch" not in result.stdout
