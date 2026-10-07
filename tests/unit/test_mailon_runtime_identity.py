"""mailon 런타임의 신원은 빌드가 남긴 manifest 로 읽고, 방향은 git 이 있는 루트에서 판정한다.

빌드(`mailon_runtime_release.sh`)는 이제 `releases/<src>-<req>` 에 `runtime-manifest.json` 을
남긴다. 디렉터리 이름만 보면 그 런타임은 언제나 vendor digest 와 달라 보이고, 운영 노드의
릴리스 트리에는 `.git` 이 없어 방향은 언제나 unknown 이었다(2026-09-30 노드 실측). manifest 가
없는 옛 런타임은 지금처럼 디렉터리 이름으로 판정한다 — FS3 가 고정한 픽스처가 그 모양이다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from automation.deploy_declarations import parse_declaration_file

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "mail" / "scripts"
_DRIFT = _SCRIPTS / "mailon_runtime_drift.sh"
_DIGEST_HELPER = _SCRIPTS / "mailon_vendor_digest.sh"
_REQUIREMENTS = "pyotp==2.9.0\n"


def _digest(tree: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", f'. "{_DIGEST_HELPER}"; mailon_vendor_digest "{tree}"'],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return result.stdout.strip()


def _req_digest(text: str) -> str:
    result = subprocess.run(
        ["bash", "-c", "sha256sum | cut -c1-16"], input=text,
        capture_output=True, text=True, check=True, timeout=60,
    )
    return result.stdout.strip()


def _vendor(root: Path, body: str = "x = 1\n") -> Path:
    tree = root / "skills" / "mail" / "vendor" / "mailon"
    tree.mkdir(parents=True)
    (tree / "main.py").write_text(body, encoding="utf-8")
    (tree / "config.py").write_text("PROJECT_ROOT = None\n", encoding="utf-8")
    (tree.parent / "requirements.txt").write_text(_REQUIREMENTS, encoding="utf-8")
    return tree


def _built_runtime(root: Path, vendor: Path, *, req: str | None = None) -> Path:
    src = _digest(vendor)
    req = req or _req_digest(_REQUIREMENTS)
    release = root / "releases" / f"{src}-{req}"
    release.mkdir(parents=True)
    shutil.copytree(vendor, release / "mailon")
    manifest = {"src_digest": src, "req_digest": req, "vendor_source": "skills/mail/vendor"}
    (release / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (root / "current").symlink_to(release)
    return release


def _named_runtime(root: Path, name: str) -> Path:
    release = root / "releases" / name
    release.mkdir(parents=True)
    (root / "current").symlink_to(release)
    return release


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, timeout=60,
    )
    return result.stdout.strip()


def _commit(root: Path, message: str) -> None:
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", message)


def _mirror_with_history(tmp_path: Path) -> tuple[Path, str, str]:
    mirror = tmp_path / "mirror"
    vendor = _vendor(mirror, body="x = 1\n")
    _git(mirror, "init", "-q")
    _commit(mirror, "old")
    old = _digest(vendor)
    (vendor / "main.py").write_text("x = 2\n", encoding="utf-8")
    _commit(mirror, "new")
    return mirror, old, _digest(vendor)


def _run(release_root: Path, runtime_root: Path, git_roots: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_DRIFT)],
        capture_output=True, text=True, check=False, timeout=60,
        env={
            "PATH": "/usr/bin:/bin", "HOME": str(runtime_root.parent),
            "AUTOPHAGY_REPO_ROOT": str(release_root),
            "MAILON_RUNTIME_ROOT": str(runtime_root),
            "MAILON_DRIFT_GIT_ROOTS": git_roots,
        },
    )


def _fields(stdout: str) -> dict[str, str]:
    return dict(token.split("=", 1) for token in stdout.split() if "=" in token)


def test_release_tree_without_git_resolves_direction_from_the_mirror(tmp_path: Path) -> None:
    # Given: the node shape — the release tree has no .git, the mirror has the history.
    mirror, old, new = _mirror_with_history(tmp_path)
    release_root = tmp_path / "release"
    _vendor(release_root, body="x = 2\n")
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    _named_runtime(runtime, old)

    result = _run(release_root, runtime, f"{release_root}:{mirror}")

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.split()[0] == "DRIFT"
    assert "direction=unknown" not in result.stdout
    assert "skills/mail/deploy.sh" in result.stdout
    assert _fields(result.stdout)["runtime"] == old
    assert _fields(result.stdout)["repo"] == new


def test_a_manifest_runtime_with_matching_digests_is_quiet(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    _built_runtime(runtime, vendor)

    result = _run(release_root, runtime, str(tmp_path / "nowhere"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.split()[0] == "OK"


def test_requirements_only_drift_is_reported(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    _built_runtime(runtime, vendor, req="0123456789abcdef")

    result = _run(release_root, runtime, str(tmp_path / "nowhere"))

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.split()[0] == "DRIFT"
    fields = _fields(result.stdout)
    assert fields["requirements"] == f"0123456789abcdef/{_req_digest(_REQUIREMENTS)}"
    assert fields["runtime"] == fields["repo"] == _digest(vendor)


def test_matching_manifest_does_not_hide_corrupt_contents(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    release = _built_runtime(runtime, vendor)
    (release / "mailon" / "main.py").write_text("x = 666\n", encoding="utf-8")

    result = _run(release_root, runtime, str(tmp_path / "nowhere"))

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.split()[0] == "DRIFT"
    assert _fields(result.stdout)["corrupt"] == "1"
    assert _fields(result.stdout)["runtime"] == _digest(vendor)


def test_a_manifest_without_its_mailon_tree_is_corrupt(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    release = _built_runtime(runtime, vendor)
    shutil.rmtree(release / "mailon")

    result = _run(release_root, runtime, str(tmp_path / "nowhere"))

    assert result.returncode == 1, result.stdout + result.stderr
    assert _fields(result.stdout)["corrupt"] == "1"


def test_an_unreadable_manifest_is_unknown_not_ok(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    release = _built_runtime(runtime, vendor)
    (release / "runtime-manifest.json").write_text('{"src_digest": "nothex"}\n', encoding="utf-8")

    result = _run(release_root, runtime, str(tmp_path / "nowhere"))

    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout.split()[0] == "UNKNOWN"


def test_a_manifest_runtime_behind_the_release_names_its_src_digest(tmp_path: Path) -> None:
    mirror, old, new = _mirror_with_history(tmp_path)
    release_root = tmp_path / "release"
    _vendor(release_root, body="x = 2\n")
    old_tree = _vendor(tmp_path / "old", body="x = 1\n")
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    _built_runtime(runtime, old_tree)

    result = _run(release_root, runtime, f"{release_root}:{mirror}")

    assert result.returncode == 1, result.stdout + result.stderr
    assert _fields(result.stdout)["runtime"] == old
    assert _fields(result.stdout)["repo"] == new
    assert "corrupt" not in _fields(result.stdout)
    assert "direction=unknown" not in result.stdout
    assert "skills/mail/deploy.sh" in result.stdout


def test_a_vendor_state_reachable_only_through_an_ours_merge_still_resolves(tmp_path: Path) -> None:
    # Given: the runtime's vendor state exists only on the second parent of an `-s ours`
    # merge whose side branch is gone; the release moved on after that merge.
    mirror = tmp_path / "mirror"
    vendor = _vendor(mirror, body="x = 1\n")
    _git(mirror, "init", "-q")
    _commit(mirror, "base")
    _git(mirror, "checkout", "-q", "-b", "side")
    (vendor / "main.py").write_text("x = 7\n", encoding="utf-8")
    _commit(mirror, "side vendor edit")
    side = _digest(vendor)
    _git(mirror, "checkout", "-q", "-")
    _git(mirror, "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "merge", "-q", "-s", "ours", "side", "-m", "ours merge")
    _git(mirror, "branch", "-q", "-D", "side")
    (vendor / "main.py").write_text("x = 2\n", encoding="utf-8")
    _commit(mirror, "release vendor edit")
    release_root = tmp_path / "release"
    _vendor(release_root, body="x = 2\n")
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    _named_runtime(runtime, side)

    result = _run(release_root, runtime, f"{release_root}:{mirror}")

    assert result.returncode == 1, result.stdout + result.stderr
    assert _fields(result.stdout)["runtime"] == side
    assert "direction=unknown" not in result.stdout
    assert "skills/mail/deploy.sh" in result.stdout
    assert "automation/release.sh" not in result.stdout

def test_no_git_root_anywhere_still_says_unknown(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    _vendor(release_root, body="x = 2\n")
    plain = tmp_path / "plain"
    plain.mkdir()
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    _named_runtime(runtime, "deadbeefdeadbeef")

    result = _run(release_root, runtime, f"{release_root}:{plain}:{tmp_path / 'absent'}")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "direction=unknown" in result.stdout
    assert "skills/mail/deploy.sh" in result.stdout
    assert "automation/release.sh" in result.stdout


def test_a_runtime_without_a_manifest_is_judged_by_its_directory_name(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    release = _named_runtime(runtime, _digest(vendor))
    # No manifest: even an empty directory is judged by its name, exactly as before.
    assert not (release / "mailon").exists()

    result = _run(release_root, runtime, str(tmp_path / "nowhere"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.split()[0] == "OK"


def test_the_mail_declaration_carries_the_runtime_as_a_derived_row() -> None:
    relative = "skills/mail/deploy-manifest.txt"
    text = (_REPO / relative).read_text(encoding="utf-8")

    derived = [d for d in parse_declaration_file(relative, text) if d.kind == "derived"]

    assert len(derived) == 1
    assert derived[0].account == "agent"
    assert derived[0].source == "skills/mail/vendor/mailon"
    assert derived[0].destination == ".hermes/mailon-runtime/current"
    assert not derived[0].legacy
    assert derived[0].policy == "required"
    assert derived[0].attrs == (
        ("algorithm", "mailon-py-v1"),
        ("requirements", "skills/mail/vendor/requirements.txt"),
    )
