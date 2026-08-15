from __future__ import annotations

import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_INVENTORY = _REPO / "automation" / "mirror_writer_inventory.sh"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True, text=True)


def _fixture(tmp_path: Path) -> Path:
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    _git("init", cwd=mirror)
    objects = mirror / ".git" / "objects" / "fixture"
    objects.mkdir(parents=True)
    (objects / "agent-one").write_text("one\n", encoding="utf-8")
    (objects / "agent-two").write_text("two\n", encoding="utf-8")
    (objects / "ops-one").write_text("ops\n", encoding="utf-8")
    return mirror


def _fake_stat(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    stat = fake_bin / "stat"
    stat.write_text(
        "#!/usr/bin/env bash\n"
        'path="${!#}"\n'
        'case "${path##*/}" in\n'
        "  agent-*) printf 'agent\\n' ;;\n"
        "  ops-*) printf 'ops\\n' ;;\n"
        "  *) exec /usr/bin/stat \"$@\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stat.chmod(0o755)
    return fake_bin


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_fixture_git_tree_is_counted_by_file_owner_without_mutation(tmp_path: Path) -> None:
    mirror = _fixture(tmp_path)
    before = _snapshot(mirror)
    environment = dict(os.environ)
    environment["PATH"] = f"{_fake_stat(tmp_path)}{os.pathsep}{environment['PATH']}"

    result = subprocess.run(
        ("bash", str(_INVENTORY), str(mirror)),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    rows = set(result.stdout.splitlines())
    assert "agent\t2" in rows
    assert "ops\t1" in rows
    assert _snapshot(mirror) == before


def test_non_git_directory_is_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        ("bash", str(_INVENTORY), str(tmp_path)),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
