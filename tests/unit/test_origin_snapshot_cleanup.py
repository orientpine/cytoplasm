from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_LIBRARY = _REPO / "automation" / "origin_snapshot.sh"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _mirror(tmp_path: Path) -> tuple[Path, str]:
    mirror = tmp_path / "mirror"
    origin = tmp_path / "origin.git"
    mirror.mkdir()
    _git("init", "--bare", str(origin), cwd=tmp_path)
    _git("init", "-b", "main", cwd=mirror)
    _git("config", "user.email", "snapshot-test@example.invalid", cwd=mirror)
    _git("config", "user.name", "Snapshot Test", cwd=mirror)
    (mirror / "tracked.txt").write_text("snapshot\n", encoding="utf-8")
    _git("add", "tracked.txt", cwd=mirror)
    _git("commit", "-m", "fixture", cwd=mirror)
    _git("remote", "add", "origin", str(origin), cwd=mirror)
    _git("push", "-u", "origin", "main", cwd=mirror)
    return mirror, _git("rev-parse", "HEAD", cwd=mirror)


def _environment(tmp_path: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["TMPDIR"] = str(tmp_path / "tmp")
    Path(environment["TMPDIR"]).mkdir()
    return environment


def _shell(body: str, *, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", "-c", body), check=False, capture_output=True, text=True, env=environment
    )


def _source() -> str:
    return f"source {shlex.quote(str(_LIBRARY))}"


def _age(path: Path) -> None:
    old = time.time() - (7 * 60 * 60)
    os.utime(path, (old, old))


def test_old_unregistered_snapshot_is_pruned_but_fresh_snapshot_survives(tmp_path: Path) -> None:
    mirror, _ = _mirror(tmp_path)
    environment = _environment(tmp_path)
    stale = Path(environment["TMPDIR"]) / "autophagy-snapshot.stale"
    fresh = Path(environment["TMPDIR"]) / "autophagy-snapshot.fresh"
    stale.mkdir()
    fresh.mkdir()
    _age(stale)

    result = _shell(
        f"{_source()}; _origin_snapshot_prune_stale {shlex.quote(str(mirror))}",
        environment=environment,
    )

    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    assert fresh.is_dir()


def test_worktree_enumeration_failure_deletes_nothing(tmp_path: Path) -> None:
    mirror, _ = _mirror(tmp_path)
    environment = _environment(tmp_path)
    stale = Path(environment["TMPDIR"]) / "autophagy-snapshot.stale"
    stale.mkdir()
    _age(stale)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"worktree list --porcelain"* ]]; then exit 41; fi\n'
        'exec /usr/bin/git "$@"\n',
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"

    result = _shell(
        f"{_source()}; _origin_snapshot_prune_stale {shlex.quote(str(mirror))}",
        environment=environment,
    )

    assert result.returncode == 0, result.stderr
    assert stale.is_dir()


def test_registered_old_worktree_survives_pruning(tmp_path: Path) -> None:
    mirror, _ = _mirror(tmp_path)
    environment = _environment(tmp_path)
    parent = Path(environment["TMPDIR"]) / "autophagy-snapshot.registered"
    _git("worktree", "add", "--detach", str(parent / "tree"), "HEAD", cwd=mirror)
    _age(parent)

    result = _shell(
        f"{_source()}; _origin_snapshot_prune_stale {shlex.quote(str(mirror))}",
        environment=environment,
    )

    assert result.returncode == 0, result.stderr
    assert (parent / "tree").is_dir()


def test_real_flock_competition_prunes_a_snapshot_once(tmp_path: Path) -> None:
    mirror, _ = _mirror(tmp_path)
    environment = _environment(tmp_path)
    stale = Path(environment["TMPDIR"]) / "autophagy-snapshot.stale"
    stale.mkdir()
    _age(stale)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "rm.calls"
    fake_rm = fake_bin / "rm"
    fake_rm.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> {shlex.quote(str(calls))}\n'
        'sleep 0.2\nexec /bin/rm "$@"\n',
        encoding="utf-8",
    )
    fake_rm.chmod(0o755)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    body = f"{_source()}; _origin_snapshot_prune_stale {shlex.quote(str(mirror))}"

    first = subprocess.Popen(("bash", "-c", body), env=environment)
    second = subprocess.Popen(("bash", "-c", body), env=environment)
    assert first.wait(timeout=5) == 0
    assert second.wait(timeout=5) == 0
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 1


def test_caller_exit_and_return_traps_survive_snapshot_run(tmp_path: Path) -> None:
    mirror, sha = _mirror(tmp_path)
    environment = _environment(tmp_path)
    marker = tmp_path / "caller-traps"
    quoted_marker = shlex.quote(str(marker))
    body = (
        f"trap 'printf EXIT >> {quoted_marker}' EXIT; "
        f"trap 'printf RETURN >> {quoted_marker}' RETURN; "
        "before_exit=$(trap -p EXIT); before_return=$(trap -p RETURN); "
        f"{_source()}; "
        f"origin_snapshot_run {shlex.quote(str(mirror))} {sha} true; "
        "[[ \"$before_exit\" == \"$(trap -p EXIT)\" ]]; "
        "[[ \"$before_return\" == \"$(trap -p RETURN)\" ]]"
    )

    result = _shell(body, environment=environment)

    assert result.returncode == 0, result.stderr
    assert "EXIT" in marker.read_text(encoding="utf-8")


def test_consecutive_calls_clean_only_their_own_parent(tmp_path: Path) -> None:
    mirror, sha = _mirror(tmp_path)
    environment = _environment(tmp_path)
    observed = tmp_path / "observed"
    command = f"bash -c 'printf \"%s\\n\" \"$AUTOPHAGY_SNAPSHOT_DIR\" >> {shlex.quote(str(observed))}'"
    body = (
        f"{_source()}; "
        f"origin_snapshot_run {shlex.quote(str(mirror))} {sha} {command}; "
        f"origin_snapshot_run {shlex.quote(str(mirror))} {sha} {command}"
    )

    result = _shell(body, environment=environment)

    assert result.returncode == 0, result.stderr
    parents = [Path(line).parent for line in observed.read_text(encoding="utf-8").splitlines()]
    assert len(parents) == 2
    assert parents[0] != parents[1]
    assert all(not parent.exists() for parent in parents)
