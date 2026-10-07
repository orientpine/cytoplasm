"""RCB todo 9: `deploy_tree_swap` never lets a reader see a missing path or a half tree.

The fake `run_agent` runs each of the three remote calls in a local shell under a temp
HOME and can park between calls on FIFOs, so concurrent readers are driven by
handshakes, never by sleeps. A new file because the FS3-pinned deploy tests stay frozen.
"""
from __future__ import annotations

import json
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "automation/deploy_tree.sh"
_DEST = ".hermes/runtime"
_NEW = {"a.py": "new-a\n", "nested/b.py": "new-b\n"}
_OLD = {"a.py": "old-a\n", "nested/b.py": "old-b\n"}
_READER = """
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
print(str(root), flush=True)
assert sys.stdin.readline() == 'go\\n'
print(json.dumps({str(p.relative_to(root)): p.read_text()
                  for p in root.rglob('*') if p.is_file()}), flush=True)
"""


def _put(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _tree(root: Path) -> dict[str, str]:
    assert root.is_dir(), root
    return {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for p in root.rglob("*") if p.is_file()
    }


def _shell(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", "-c", script), env=env, cwd=_REPO,
        capture_output=True, text=True, check=False, timeout=15,
    )


@dataclass(frozen=True)
class Rig:
    root: Path
    repo: Path
    home: Path
    env: dict[str, str]

    @property
    def dest(self) -> Path:
        return self.home / _DEST

    def command(self, *options: str, paths: tuple[str, ...] = ()) -> str:
        args = shlex.join((*options, str(self.repo / "pkg"), _DEST, *paths))
        return (
            f"source {shlex.quote(str(self.root / 'agent.sh'))}; "
            f"source {shlex.quote(str(_HELPER))}; "
            f"repo_root={shlex.quote(str(self.repo))}; deploy_tree_swap {args}"
        )

    def run(self, *options: str, paths: tuple[str, ...] = (),
            fault: str = "") -> subprocess.CompletedProcess[str]:
        (self.root / "calls").write_text("0\n", encoding="utf-8")
        return _shell(self.command(*options, paths=paths), {**self.env, "FAULT": fault})

    def seed(self, *, link: bool = False) -> Path:
        target = self.home / f"{_DEST}.d/earlier" if link else self.dest
        _put(target, _OLD)
        if link:
            self.dest.symlink_to("runtime.d/earlier")
        return target


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    repo, home, bin_dir = (tmp_path / p for p in ("repo", "home", "bin"))
    home.mkdir()
    bin_dir.mkdir()
    _put(repo / "pkg", {
        **_NEW, "cron/watch.py": "watch\n", "nested/cron/keep.py": "keep\n",
        "config.txt": "config\n",
    })
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    subprocess.run(("git", "-C", str(repo), "add", "."), check=True)
    _put(repo / "pkg", {"untracked.py": "not shipped\n"})
    (tmp_path / "calls").write_text("0\n", encoding="utf-8")
    _put(tmp_path, {"agent.sh": r'''
run_agent() {
    local n result rc=0
    read -r n < "$RIG/calls"
    n=$((n + 1)); printf '%s\n' "$n" > "$RIG/calls"
    if [[ "$FAULT" == drop && "$n" == 1 ]]; then cat >/dev/null; return 0; fi
    if [[ " ${CUT:-} " == *" skip$n "* ]]; then cat >/dev/null; return 255; fi
    if [[ " ${CUT:-} " == *" cut$n "* ]]; then HOME="$NODE_HOME" bash -c "$1" >/dev/null; return 255; fi
    result=$(HOME="$NODE_HOME" bash -c "$1") || rc=$?
    if [[ "$FAULT" == taken && "$n" == 1 && "$rc" == 0 ]]; then
        local stage pid
        for stage in "$NODE_HOME/.hermes/runtime.staging."*; do pid=${stage##*.}; done
        mkdir "$NODE_HOME/.hermes/runtime.old.20010101T000000Z-$pid"
        printf 'foreign\n' > "$NODE_HOME/.hermes/runtime.old.20010101T000000Z-$pid/owned"
    fi
    if [[ "${PAUSE:-0}" == 1 && "$n" -le 3 ]]; then
        printf '%s\n' "$n" > "$RIG/arrived"
        read -r _ < "$RIG/release"
    fi
    if [[ "$FAULT" == stage && "$n" == 1 || "$FAULT" == verify && "$n" == 3 ]]; then
        printf 'misleading-success\n'
    else
        printf '%s\n' "$result"
    fi
    return "$rc"
}
'''})
    real_python = shlex.quote(sys.executable)
    _put(bin_dir, {
        "date": "#!/bin/bash\nprintf '20010101T000000Z\\n'\n",
        "python3": (
            "#!/bin/bash\nprogram=$(cat)\n"
            'if [[ "$FAULT" == exchange && "$program" == *renameat2* ]]; then exit 1; fi\n'
            # The exchange happens, then the node shell dies before the backup rename.
            f'if [[ "$FAULT" == kill-after-exchange && "$2" == exchange ]]; then\n'
            f'  {real_python} "$@" <<< "$program"; kill -9 "$PPID"; exit 1\nfi\n'
            # The syscall happens, then the reporter dies: a real exchange reported as failed.
            f'if [[ "$FAULT" == exchange-after && "$2" == exchange ]]; then\n'
            f'  {real_python} "$@" <<< "$program"; exit 1\nfi\n'
            f'exec {real_python} "$@" <<< "$program"\n'
        ),
        # Only the remote form `readlink -- <link>` fails; deploy_archive_stream's -f stays real.
        "readlink": (
            '#!/bin/bash\nif [[ "$FAULT" == readlink && "$1" == -- ]]; then exit 1; fi\n'
            f'exec {shlex.quote(shutil.which("readlink") or "readlink")} "$@"\n'
        ),
        "realpath": (
            '#!/bin/bash\nif [[ "$FAULT" == realpath ]]; then exit 1; fi\n'
            f'exec {shlex.quote(shutil.which("realpath") or "realpath")} "$@"\n'
        ),
        "mv": (
            "#!/bin/bash\n"
            # The node shell dies after `ln -s` created the link temp, before the flip.
            'if [[ "$FAULT" == linkkill && "$*" == *".link."* ]]; then kill -9 "$PPID"; exit 1; fi\n'
            "/bin/mv \"$@\" || exit $?\n"
            'if [[ "$FAULT" == prepublish && "$*" == *"/.staging."* ]]; then\n'
            '  target="${@: -1}"; printf "changed\\n" > "$target/a.py"\nfi\n'
        ),
    })
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    return Rig(tmp_path, repo, home, {
        **os.environ, "HOME": str(home), "NODE_HOME": str(home), "RIG": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAULT": "",
    })


_SHIPPED = {**_NEW, "nested/cron/keep.py": "keep\n"}


def _line(stream: int) -> bytes:
    assert select.select([stream], [], [], 10)[0], "handshake deadline expired"
    return os.read(stream, 4096)


@contextmanager
def _paused(rig: Rig, *, link: bool, fault: str = "") -> Iterator[
    tuple[subprocess.Popen[str], int, int]
]:
    for name in ("arrived", "release"):
        os.mkfifo(rig.root / name)
    with (
        os.fdopen(os.open(rig.root / "arrived", os.O_RDWR), "rb", buffering=0) as arrived,
        os.fdopen(os.open(rig.root / "release", os.O_RDWR), "wb", buffering=0) as release,
        subprocess.Popen(
            ("bash", "-c", rig.command(*(["--link"] if link else []))),
            env={**rig.env, "FAULT": fault, "PAUSE": "1"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        ) as process,
    ):
        try:
            yield process, arrived.fileno(), release.fileno()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=10)
            for name in ("arrived", "release"):
                (rig.root / name).unlink()


def test_a_directory_swap_exchanges_atomically_and_keeps_the_previous_tree(rig: Rig) -> None:
    rig.seed()
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert not rig.dest.is_symlink()
    assert _tree(rig.dest) == _SHIPPED
    backups = list(rig.dest.parent.glob("runtime.old.*"))
    assert len(backups) == 1 and _tree(backups[0]) == _OLD
    assert (rig.root / "calls").read_text() == "3\n"
    assert rig.dest.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in rig.dest.rglob("*.py"))


def test_the_standing_probe_reads_a_directory_swapped_tree(rig: Rig) -> None:
    assert rig.run().returncode == 0
    probe = shlex.quote(str(_REPO / "automation/runtime_package_probe.sh"))
    # The source profile excludes the untracked file independently via the selected list.
    files = ",".join(_SHIPPED)
    result = _shell(
        f"source {probe}; runtime_package_local_snapshot '{rig.dest}' > '{rig.root}/actual'; "
        f"runtime_package_local_snapshot '{rig.repo}/pkg' python '{files}' > '{rig.root}/want'; "
        f"diff -u '{rig.root}/want' '{rig.root}/actual'", rig.env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len((rig.root / "actual").read_text().splitlines()) == len(_SHIPPED) + 1


def test_link_layout_creates_the_link_and_flips_generations(rig: Rig) -> None:
    assert rig.run("--link").returncode == 0
    assert rig.dest.is_symlink()
    previous = rig.dest.resolve()
    assert rig.run("--link").returncode == 0
    assert rig.dest.is_symlink() and rig.dest.resolve() != previous
    assert _tree(previous) == _tree(rig.dest) == _SHIPPED
    assert len(list((rig.home / f"{_DEST}.d").iterdir())) == 2


def test_default_selection_is_tracked_python_without_top_level_cron(rig: Rig) -> None:
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert _tree(rig.dest) == _SHIPPED


def test_prefix_places_the_files_under_a_subdirectory(rig: Rig) -> None:
    result = rig.run("--prefix", "package", paths=("a.py", "config.txt"))
    assert result.returncode == 0, result.stderr
    assert _tree(rig.dest) == {"package/a.py": "new-a\n", "package/config.txt": "config\n"}


@pytest.mark.parametrize("fault", ["stage", "drop"])
def test_failed_staging_verification_leaves_the_active_tree_untouched(rig: Rig, fault: str) -> None:
    rig.seed()
    result = rig.run(fault=fault)
    assert result.returncode == 5
    assert _tree(rig.dest) == _OLD
    assert sorted(p.name for p in rig.dest.parent.iterdir()) == ["runtime"]
    assert rig.run().returncode == 0


@pytest.mark.parametrize("link", [False, True])
def test_the_destination_always_resolves_during_a_deploy(rig: Rig, link: bool) -> None:
    rig.seed(link=link)
    with _paused(rig, link=link) as (process, arrived, release):
        for step, expected in ((1, _OLD), (2, _SHIPPED), (3, _SHIPPED)):
            assert _line(arrived) == f"{step}\n".encode()
            read = subprocess.run(
                (sys.executable, "-c", _READER, str(rig.dest)), input="go\n",
                capture_output=True, text=True, check=True, timeout=10, env=rig.env,
            )
            assert json.loads(read.stdout.splitlines()[1]) == expected
            os.write(release, b"go\n")
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stdout + stderr


def _pinned_reader(rig: Rig, *, rollback: bool) -> None:
    rig.seed(link=True)
    with _paused(rig, link=True, fault="verify" if rollback else "") as (
        deploy, arrived, release,
    ):
        assert _line(arrived) == b"1\n"
        if rollback:
            os.write(release, b"go\n")
            assert _line(arrived) == b"2\n"
        with subprocess.Popen(
            (sys.executable, "-c", _READER, str(rig.dest)), env=rig.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as reader:
            try:
                assert reader.stdout is not None
                pinned = Path(_line(reader.stdout.fileno()).decode().strip())
                os.write(release, b"go\n")
                if not rollback:
                    assert _line(arrived) == b"2\n"
                    os.write(release, b"go\n")
                assert _line(arrived) == b"3\n"
                os.write(release, b"go\n")
                deploy.communicate(timeout=10)
                assert deploy.returncode == (5 if rollback else 0)
                stdout, stderr = reader.communicate(b"go\n", timeout=10)
                assert reader.returncode == 0, stderr
                assert json.loads(stdout) == (_SHIPPED if rollback else _OLD)
                assert _tree(pinned) == (_SHIPPED if rollback else _OLD)
            finally:
                if reader.poll() is None:
                    reader.kill()
                reader.communicate(timeout=10)


def test_a_reader_that_resolved_the_link_keeps_one_generation_across_a_flip(rig: Rig) -> None:
    _pinned_reader(rig, rollback=False)


def test_link_rollback_keeps_a_published_generation_for_a_pinned_reader(rig: Rig) -> None:
    _pinned_reader(rig, rollback=True)


def _act_between_switch_and_verify(
    rig: Rig, *, link: bool, action: Callable[[Path], None],
) -> tuple[int, str]:
    rig.seed(link=link)
    with _paused(rig, link=link) as (process, arrived, release):
        for step in (1, 2, 3):
            assert _line(arrived) == f"{step}\n".encode()
            if step == 2:
                action(rig.dest.resolve())
            os.write(release, b"go\n")
        stdout, stderr = process.communicate(timeout=10)
        return process.returncode, stdout + stderr


def _import_from(root: Path) -> None:
    """A live importer writes bytecode into the tree it just loaded (a.py fails at exec)."""
    program = "import sys\nsys.path.insert(0, sys.argv[1])\ntry:\n    import a\nexcept NameError:\n    pass\n"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    subprocess.run((sys.executable, "-c", program, str(root)), check=True, env=env, timeout=10)
    assert (root / "__pycache__").is_dir()


@pytest.mark.parametrize("link", [False, True])
def test_an_import_between_switch_and_verify_does_not_roll_back(rig: Rig, link: bool) -> None:
    returncode, output = _act_between_switch_and_verify(rig, link=link, action=_import_from)
    assert returncode == 0, output
    assert {name: (rig.dest / name).read_text() for name in _SHIPPED} == _SHIPPED
    assert (rig.dest.resolve() / "__pycache__").is_dir()
    assert rig.dest.is_symlink() == link
    if link:
        assert rig.dest.resolve().name != "earlier"


@pytest.mark.parametrize("link", [False, True])
def test_a_stray_file_between_switch_and_verify_still_rolls_back(rig: Rig, link: bool) -> None:
    returncode, _ = _act_between_switch_and_verify(
        rig, link=link, action=lambda root: (root / "extra.py").write_text("stray\n"),
    )
    assert returncode == 5
    assert _tree(rig.dest) == _OLD


def test_a_held_lock_blocks_the_swap_without_changes(rig: Rig) -> None:
    rig.seed()
    lock = rig.home / ".hermes/watch.lock"
    lock.write_text("do not truncate\n")
    for name in ("locked", "unlock"):
        os.mkfifo(rig.root / name)
    with (
        os.fdopen(os.open(rig.root / "locked", os.O_RDWR), "rb", buffering=0) as ready,
        os.fdopen(os.open(rig.root / "unlock", os.O_RDWR), "wb", buffering=0) as release,
        subprocess.Popen(
            ("bash", "-c", f'exec 9>>"{lock}"; flock 9; printf "ready\\n" > '
             f'"{rig.root}/locked"; read -r _ < "{rig.root}/unlock"'),
            env=rig.env, start_new_session=True,
        ) as holder,
    ):
        try:
            assert _line(ready.fileno()) == b"ready\n"
            start = time.monotonic()
            result = _shell(
                rig.command("--lock", ".hermes/watch.lock"),
                {**rig.env, "DEPLOY_TREE_LOCK_WAIT": "1"},
            )
            assert result.returncode == 6, result.stderr
            assert time.monotonic() - start < 8
            assert _tree(rig.dest) == _OLD
            assert lock.read_text() == "do not truncate\n"
        finally:
            os.write(release.fileno(), b"go\n")
            holder.wait(timeout=10)
            for name in ("locked", "unlock"):
                (rig.root / name).unlink()


def test_the_lock_file_is_never_truncated(rig: Rig) -> None:
    _put(rig.home, {".hermes/watch.lock": "keep\n"})
    result = rig.run("--lock", ".hermes/watch.lock")
    assert result.returncode == 0, result.stderr
    assert (rig.home / ".hermes/watch.lock").read_text() == "keep\n"


def test_an_impossible_exchange_changes_nothing(rig: Rig) -> None:
    rig.seed()
    result = rig.run(fault="exchange")
    assert result.returncode == 5
    assert _tree(rig.dest) == _OLD
    assert list(rig.dest.parent.iterdir()) == [rig.dest]


def test_stale_extra_files_do_not_survive_a_swap(rig: Rig) -> None:
    rig.seed()
    _put(rig.dest, {"stale.py": "stale\n", "__pycache__/old.pyc": "bytecode\n"})
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert _tree(rig.dest) == _SHIPPED
    assert _tree(next(rig.dest.parent.glob("runtime.old.*")))["stale.py"] == "stale\n"


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_link_layout_refuses_an_existing_directory(rig: Rig, kind: str) -> None:
    if kind == "directory":
        rig.seed()
    else:
        _put(rig.home, {_DEST: "foreign\n"})
    result = rig.run("--link")
    assert result.returncode == 5
    assert not rig.dest.is_symlink()
    assert (_tree(rig.dest) if kind == "directory" else rig.dest.read_text()) == (
        _OLD if kind == "directory" else "foreign\n"
    )
    assert list(rig.dest.parent.iterdir()) == [rig.dest]


@pytest.mark.parametrize("link", [False, True])
@pytest.mark.parametrize("initial", [False, True])
def test_a_post_switch_mismatch_rolls_back_and_removes_only_this_runs_tree(
    rig: Rig, link: bool, initial: bool,
) -> None:
    previous = None if initial else rig.seed(link=link)
    _put(rig.home, {
        ".hermes/runtime.old/foreign": "keep\n",
        ".hermes/runtime.old.previous/foreign": "keep previous\n",
        ".hermes/runtime.d/foreign/foreign": "keep generation\n",
    })
    before = _tree(rig.home)
    result = rig.run(*(["--link"] if link else []), fault="verify")
    assert result.returncode == 5
    assert not rig.dest.exists() if initial else _tree(rig.dest) == _OLD
    if link:
        assert initial or rig.dest.resolve() == previous
        generations = list((rig.home / ".hermes/runtime.d").glob("20010101T000000Z-*"))
        assert len(generations) == 1 and _tree(generations[0]) == _SHIPPED
        assert str(generations[0]) in result.stderr
        after = _tree(rig.home)
        for relative, value in before.items():
            assert after[relative] == value
    else:
        assert _tree(rig.home) == before


@pytest.mark.parametrize("link", [False, True])
def test_earlier_backups_and_foreign_directories_are_never_touched(rig: Rig, link: bool) -> None:
    rig.seed(link=link)
    foreign = {
        ".hermes/runtime.old/foreign": "keep\n",
        ".hermes/runtime.old.previous/foreign": "keep previous\n",
        ".hermes/runtime.d/foreign/foreign": "keep generation\n",
    }
    _put(rig.home, foreign)
    for _ in range(2):
        result = rig.run(*(["--link"] if link else []))
        assert result.returncode == 0, result.stderr
        for name, value in foreign.items():
            assert (rig.home / name).read_text() == value
    if link:
        assert len(list((rig.home / ".hermes/runtime.d").iterdir())) == 4
    else:
        assert len(list(rig.dest.parent.glob("runtime.old.*"))) == 3


@pytest.mark.parametrize("dest", [
    ".hermes/regression_bank_runtime", ".hermes/rag_ingest_runtime", ".hermes",
])
def test_link_layout_refuses_a_tree_the_standing_probe_reads(rig: Rig, dest: str) -> None:
    result = _shell(rig.command("--link").replace(f" {_DEST}", f" {dest}"), rig.env)
    assert result.returncode == 5
    assert list(rig.home.iterdir()) == []


@pytest.mark.parametrize("link", [False, True])
def test_two_deploys_from_one_shell_get_distinct_names(rig: Rig, link: bool) -> None:
    """The stub clock freezes the stamp, so only a per-call pid keeps the names apart."""
    rig.seed(link=link)
    once = rig.command(*(["--link"] if link else []))
    result = _shell(f"{once} && {once.rsplit('; ', 1)[1]}", rig.env)
    assert result.returncode == 0, result.stderr
    assert _tree(rig.dest) == _SHIPPED
    if link:
        assert len(list((rig.home / f"{_DEST}.d").iterdir())) == 3
    else:
        assert len(list(rig.dest.parent.glob("runtime.old.*"))) == 2


def test_a_pre_publish_mismatch_never_changes_the_link(rig: Rig) -> None:
    previous = rig.seed(link=True)
    result = rig.run("--link", fault="prepublish")
    assert result.returncode == 5
    assert rig.dest.resolve() == previous
    assert _tree(previous) == _OLD
    assert list(previous.parent.iterdir()) == [previous]
    assert rig.run("--link").returncode == 0


def test_a_taken_backup_name_is_refused_without_changes(rig: Rig) -> None:
    rig.seed()
    result = rig.run(fault="taken")
    assert result.returncode == 5
    assert _tree(rig.dest) == _OLD
    backups = list(rig.dest.parent.glob("runtime.old.*"))
    assert len(backups) == 1 and _tree(backups[0]) == {"owned": "foreign\n"}
    assert not list(rig.dest.parent.glob("runtime.staging.*"))


@pytest.mark.parametrize("arguments", [
    "missing .hermes/runtime", "pkg /absolute", "pkg ../escape", "pkg .hermes/../escape",
    "--prefix ../escape pkg .hermes/runtime", "--lock /absolute pkg .hermes/runtime",
    "pkg .hermes/runtime ../escape", "pkg .hermes/runtime missing.py",
])
def test_malformed_inputs_are_refused_before_remote_writes(rig: Rig, arguments: str) -> None:
    command = (
        f"source '{rig.root}/agent.sh'; source '{_HELPER}'; repo_root='{rig.repo}'; "
        f"cd '{rig.repo}'; deploy_tree_swap {arguments}"
    )
    result = _shell(command, rig.env)
    assert result.returncode == 5
    assert list(rig.home.iterdir()) == []


@pytest.mark.parametrize("link", [False, True])
@pytest.mark.parametrize("step", [1, 2, 3])
def test_interruption_between_calls_preserves_a_complete_tree_and_can_resume(
    rig: Rig, link: bool, step: int,
) -> None:
    rig.seed(link=link)
    with _paused(rig, link=link) as (process, arrived, release):
        for current in range(1, step + 1):
            assert _line(arrived) == f"{current}\n".encode()
            if current != step:
                os.write(release, b"go\n")
        # An uncatchable disconnect cannot finish cleanup, but must not damage the active tree.
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=10)
        assert _tree(rig.dest) == (_OLD if step == 1 else _SHIPPED)
    result = rig.run(*(["--link"] if link else []))
    assert result.returncode == 0, result.stderr
    assert _tree(rig.dest) == _SHIPPED


def _leftovers(rig: Rig) -> list[str]:
    return sorted(
        str(p.relative_to(rig.home)) for p in rig.home.rglob("*")
        if ".staging." in p.name or ".link." in p.name
    )


@pytest.mark.parametrize("link", [False, True])
@pytest.mark.parametrize("cut", ["cut1", "skip2", "cut3"])
def test_a_dropped_connection_returns_the_helpers_code_under_the_callers_errexit(
    rig: Rig, link: bool, cut: str,
) -> None:
    # Deployers source the helper under `set -euo pipefail`. ssh reports 255 after the
    # remote finished its step (cutN: staging extracted, verify snapshot taken) or before
    # the remote ran at all (skip2: the switch call, todo 15's case). The helper must
    # still clean up / roll back and return its own code, exactly as it does for a caller
    # without errexit (RCB todo 45, seen in todo 24's QA as rc 255).
    earlier = rig.seed(link=link)
    outcomes = []
    for errexit in ("set -euo pipefail; ", ""):
        (rig.root / "calls").write_text("0\n", encoding="utf-8")
        result = _shell(
            errexit + rig.command(*(["--link"] if link else [])),
            {**rig.env, "CUT": cut},
        )
        outcomes.append((result.returncode, _tree(rig.dest), _leftovers(rig)))
        assert result.returncode == 5, (errexit, result.stderr)
        assert _tree(rig.dest) == _OLD
        assert _leftovers(rig) == []
        if link:
            assert rig.dest.resolve() == earlier.resolve()
        else:
            assert not rig.dest.is_symlink()
            assert sorted(p.name for p in rig.dest.parent.iterdir()) == ["runtime"]
    assert outcomes[0] == outcomes[1]


# RCB todo 48: failure paths that misreported the state or lost the previous tree / link.


@pytest.mark.parametrize("link", [False, True])
def test_a_switch_call_cut_after_the_remote_switched_reports_state_unknown(
    rig: Rig, link: bool,
) -> None:
    # The remote switched, then ssh reported 255: the active path holds this run's tree and
    # the verify step never ran, so "unchanged or restored" (rc 5) would be false.
    rig.seed(link=link)
    (rig.root / "calls").write_text("0\n", encoding="utf-8")
    result = _shell(rig.command(*(["--link"] if link else [])), {**rig.env, "CUT": "cut2"})
    assert result.returncode == 7, result.stderr
    assert _tree(rig.dest) == _SHIPPED
    assert _leftovers(rig) == []


@pytest.mark.parametrize("link", [False, True])
@pytest.mark.parametrize(("cut", "active"), [("skip4", _SHIPPED), ("cut4", _OLD)])
def test_a_dropped_rollback_call_reports_state_unknown(
    rig: Rig, link: bool, cut: str, active: dict[str, str],
) -> None:
    # The verify step saw a mismatch, then the rollback call was cut before (skip4) or after
    # (cut4) the remote ran it: the helper cannot know which tree is active.
    rig.seed(link=link)
    (rig.root / "calls").write_text("0\n", encoding="utf-8")
    result = _shell(
        rig.command(*(["--link"] if link else [])), {**rig.env, "FAULT": "verify", "CUT": cut},
    )
    assert result.returncode == 7, result.stderr
    assert _tree(rig.dest) == active


def test_an_exchange_reported_as_failed_keeps_the_previous_tree(rig: Rig) -> None:
    # renameat2 swapped the trees but its reporter failed: the staging name now holds the
    # PREVIOUS tree, which must not be deleted as if it were this run's staging.
    rig.seed()
    result = rig.run(fault="exchange-after")
    assert result.returncode == 7, result.stderr
    kept = [p for p in rig.dest.parent.iterdir() if p.name != "runtime"]
    assert [_tree(p) for p in kept] == [_OLD]
    assert _tree(rig.dest) == _SHIPPED


def test_an_unreadable_link_is_never_switched(rig: Rig) -> None:
    # Without the previous target a later rollback would remove the link instead of
    # restoring it, so a readlink failure must stop before the flip.
    earlier = rig.seed(link=True)
    result = rig.run("--link", fault="readlink")
    assert result.returncode == 5, result.stderr
    assert rig.dest.is_symlink() and rig.dest.resolve() == earlier.resolve()
    assert sorted(p.name for p in (rig.home / f"{_DEST}.d").iterdir()) == ["earlier"]
    assert _leftovers(rig) == []


def test_a_failed_realpath_refuses_the_repo_boundary_check(rig: Rig) -> None:
    # Two failed realpath calls used to compare "" with "" and pass the boundary check.
    result = rig.run(fault="realpath")
    assert result.returncode == 5
    assert list(rig.home.iterdir()) == []


def test_a_switch_killed_after_the_link_temp_leaves_no_link_temp(rig: Rig) -> None:
    # The node died between `ln -s` and the flip: the link is unchanged, so the discard call
    # reports nothing switched and also removes this run's link temp.
    earlier = rig.seed(link=True)
    result = rig.run("--link", fault="linkkill")
    assert result.returncode == 5, result.stderr
    assert rig.dest.resolve() == earlier.resolve()
    assert _leftovers(rig) == []


def test_an_identical_redeploy_cut_before_the_backup_rename_keeps_the_displaced_tree(
    rig: Rig,
) -> None:
    # The previous tree has the same bytes as this run's tree. After the exchange it sits at
    # the staging name; only its identity (not its content) tells it apart, and it must stay.
    _put(rig.dest, _SHIPPED)
    result = rig.run(fault="kill-after-exchange")
    assert result.returncode == 7, result.stderr
    assert _tree(rig.dest) == _SHIPPED
    kept = [p for p in rig.dest.parent.iterdir() if p.name != "runtime"]
    assert [_tree(p) for p in kept] == [_SHIPPED]
