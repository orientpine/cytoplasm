"""메일 런타임 빌드는 쓰이고 있는 릴리스를 지우지 않고, 같은 신원이면 아무것도 하지 않는다.

`skills/mail/scripts/mailon_runtime_release.sh` 는 기관메일을 실제로 보내는 런타임을 만든다.
예전에는 같은 digest 를 다시 배포하면 `current` 가 가리키는 활성 디렉터리를 `rm -rf` 로
지운 뒤 다시 만들었다 — 그 사이 발송은 사라진 venv 를 실행한다. 그리고 저장된 초안의 argv 는
옛 릴리스의 venv 파이썬을 가리킬 수 있으므로 옛 디렉터리는 남아 있어야 한다.

신원 = 소스 digest + requirements digest, 새 디렉터리 이름 = `releases/<src>-<req>`.
가짜 파이썬으로 venv·pip 를 흉내 내므로 네트워크도 실제 의존성도 필요 없다.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "mail" / "scripts"
_RELEASE = _SCRIPTS / "mailon_runtime_release.sh"
_DIGEST_HELPER = _SCRIPTS / "mailon_vendor_digest.sh"

_FAKE_PYTHON = """#!/bin/bash
if [ -n "${FAKE_PY_LOG:-}" ]; then printf '%s\\n' "$*" >> "$FAKE_PY_LOG"; fi
case "$1" in
  --version) echo "Python 3.12.0"; exit 0 ;;
  -c) echo cp312; exit 0 ;;
  -) cat >/dev/null; exit 0 ;;
  -m)
    case "$2" in
      venv)
        dir="${@: -1}"
        mkdir -p "$dir/bin" && cp "$0" "$dir/bin/python" && chmod 700 "$dir/bin/python"
        exit $? ;;
      ensurepip) exit 0 ;;
      pip)
        for arg in "$@"; do
          if [ "$arg" = "-r" ]; then exit "${FAKE_PIP_RC:-0}"; fi
        done
        exit 0 ;;
    esac ;;
esac
exit 64
"""


def _fake_python(tmp_path: Path) -> Path:
    path = tmp_path / "fake-python"
    path.write_text(_FAKE_PYTHON, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _vendor(tmp_path: Path, requirements: str = "pyotp==2.9.0\n") -> Path:
    vendor = tmp_path / "vendor"
    (vendor / "mailon").mkdir(parents=True)
    (vendor / "mailon" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (vendor / "mailon" / "config.py").write_text("PROJECT_ROOT = None\n", encoding="utf-8")
    (vendor / "requirements.txt").write_text(requirements, encoding="utf-8")
    return vendor


def _src_digest(tree: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", f'. "{_DIGEST_HELPER}"; mailon_vendor_digest "{tree}"'],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return result.stdout.strip()


def _req_digest(vendor: Path) -> str:
    return hashlib.sha256((vendor / "requirements.txt").read_bytes()).hexdigest()[:16]


def _run(tmp_path: Path, vendor: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "MAILON_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "MAILON_RUNTIME_PYTHON": str(_fake_python(tmp_path)),
        **extra,
    }
    return subprocess.run(
        ["bash", str(_RELEASE), str(vendor)],
        capture_output=True, text=True, check=False, timeout=120, env=env,
    )


def _current(tmp_path: Path) -> Path:
    return (tmp_path / "runtime" / "current").resolve()


def _identity(path: Path) -> tuple[int, int]:
    info = os.stat(path)
    return info.st_ino, info.st_mtime_ns


def _legacy_release(tmp_path: Path, vendor: Path, *, manifest: str | None = None) -> Path:
    """기존 이름(`releases/<src>`) 의 릴리스 — 신원은 이름이 아니라 manifest 로 읽힌다."""
    src, req = _src_digest(vendor / "mailon"), _req_digest(vendor)
    runtime = tmp_path / "runtime"
    legacy = runtime / "releases" / src
    legacy.mkdir(parents=True)
    subprocess.run(["cp", "-a", str(vendor / "mailon"), str(legacy / "mailon")], check=True)
    venv = runtime / "venvs" / f"cp312-{req}"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text(_FAKE_PYTHON, encoding="utf-8")
    (venv / "bin" / "python").chmod(0o700)
    (legacy / ".venv").symlink_to(venv)
    body = manifest if manifest is not None else (
        f'{{\n  "src_digest": "{src}",\n  "req_digest": "{req}",\n'
        f'  "venv_key": "cp312-{req}",\n  "python": "Python 3.12.0",\n'
        '  "built_at": "2026-09-01T00:00:00Z",\n  "vendor_source": "skills/mail/vendor"\n}\n'
    )
    (legacy / "runtime-manifest.json").write_text(body, encoding="utf-8")
    (runtime / "current").symlink_to(legacy)
    return legacy


def test_first_build_activates_a_composite_named_release(tmp_path: Path) -> None:
    vendor = _vendor(tmp_path)

    result = _run(tmp_path, vendor)

    assert result.returncode == 0, result.stderr
    expected = tmp_path / "runtime" / "releases" / (
        f"{_src_digest(vendor / 'mailon')}-{_req_digest(vendor)}")
    assert result.stdout.splitlines() == [str(expected)]
    assert _current(tmp_path) == expected.resolve()
    assert _src_digest(expected / "mailon") == _src_digest(vendor / "mailon")
    assert (expected / ".venv" / "bin" / "python").is_file()


def test_same_identity_redeploy_is_a_no_op(tmp_path: Path) -> None:
    vendor = _vendor(tmp_path)
    first = _run(tmp_path, vendor)
    assert first.returncode == 0, first.stderr
    active = _current(tmp_path)
    link = tmp_path / "runtime" / "current"
    before = (_identity(active), os.lstat(link).st_ino, sorted(os.listdir(active.parent)))
    log = tmp_path / "py.log"

    second = _run(tmp_path, vendor, FAKE_PY_LOG=str(log))

    assert second.returncode == 0, second.stderr
    assert second.stdout.splitlines() == first.stdout.splitlines()
    after = (_identity(active), os.lstat(link).st_ino, sorted(os.listdir(active.parent)))
    assert after == before
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert not [call for call in calls if call.startswith("-m ")]


def test_requirements_only_change_builds_a_new_release_and_keeps_the_old_one(
    tmp_path: Path,
) -> None:
    vendor = _vendor(tmp_path)
    assert _run(tmp_path, vendor).returncode == 0
    old = _current(tmp_path)

    (vendor / "requirements.txt").write_text("pyotp==2.9.1\n", encoding="utf-8")
    result = _run(tmp_path, vendor)

    assert result.returncode == 0, result.stderr
    new = _current(tmp_path)
    assert new != old
    assert new.name == f"{_src_digest(vendor / 'mailon')}-{_req_digest(vendor)}"
    assert old.is_dir()
    assert (old / "runtime-manifest.json").is_file()


def test_corrupt_existing_release_is_refused_not_deleted(tmp_path: Path) -> None:
    # Given: 활성 릴리스 A 와, 다음 신원 이름을 가졌지만 내용이 다른 디렉터리 B.
    vendor = _vendor(tmp_path)
    assert _run(tmp_path, vendor).returncode == 0
    active = _current(tmp_path)
    (vendor / "requirements.txt").write_text("pyotp==2.9.1\n", encoding="utf-8")
    src, req = _src_digest(vendor / "mailon"), _req_digest(vendor)
    target = tmp_path / "runtime" / "releases" / f"{src}-{req}"
    subprocess.run(["cp", "-a", str(active), str(target)], check=True)
    (target / "runtime-manifest.json").write_text(
        f'{{\n  "src_digest": "{src}",\n  "req_digest": "{req}"\n}}\n', encoding="utf-8")
    (target / "mailon" / "main.py").write_text("x = 'tampered'\n", encoding="utf-8")

    result = _run(tmp_path, vendor)

    assert result.returncode == 1
    assert _current(tmp_path) == active
    assert target.is_dir()
    assert (target / "mailon" / "main.py").read_text(encoding="utf-8") == "x = 'tampered'\n"


def test_active_release_whose_content_differs_from_its_name_is_refused(
    tmp_path: Path,
) -> None:
    vendor = _vendor(tmp_path)
    assert _run(tmp_path, vendor).returncode == 0
    active = _current(tmp_path)
    (active / "mailon" / "main.py").write_text("x = 'tampered'\n", encoding="utf-8")

    result = _run(tmp_path, vendor)

    assert result.returncode == 1
    assert _current(tmp_path) == active
    assert (active / "mailon" / "main.py").read_text(encoding="utf-8") == "x = 'tampered'\n"


def test_unreadable_manifest_is_refused_not_deleted(tmp_path: Path) -> None:
    vendor = _vendor(tmp_path)
    assert _run(tmp_path, vendor).returncode == 0
    active = _current(tmp_path)
    (active / "runtime-manifest.json").write_text("{not json", encoding="utf-8")

    result = _run(tmp_path, vendor)

    assert result.returncode == 1
    assert _current(tmp_path) == active
    assert (active / "runtime-manifest.json").read_text(encoding="utf-8") == "{not json"


def test_pip_failure_keeps_the_previous_current(tmp_path: Path) -> None:
    vendor = _vendor(tmp_path)
    assert _run(tmp_path, vendor).returncode == 0
    active = _current(tmp_path)
    (vendor / "requirements.txt").write_text("pyotp==2.9.1\n", encoding="utf-8")
    target = f"{_src_digest(vendor / 'mailon')}-{_req_digest(vendor)}"

    failed = _run(tmp_path, vendor, FAKE_PIP_RC="1")

    assert failed.returncode == 6
    assert _current(tmp_path) == active
    releases = tmp_path / "runtime" / "releases"
    assert not (releases / target).exists()
    assert sorted(p.name for p in releases.iterdir()) == [active.name]

    retried = _run(tmp_path, vendor)

    assert retried.returncode == 0, retried.stderr
    assert _current(tmp_path).name == target
    assert active.is_dir()


def test_legacy_named_release_is_judged_by_its_manifest(tmp_path: Path) -> None:
    vendor = _vendor(tmp_path)
    legacy = _legacy_release(tmp_path, vendor)
    before = _identity(legacy)

    result = _run(tmp_path, vendor)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(legacy)]
    assert _current(tmp_path) == legacy.resolve()
    assert _identity(legacy) == before


def test_legacy_release_survives_a_new_identity(tmp_path: Path) -> None:
    vendor = _vendor(tmp_path)
    legacy = _legacy_release(tmp_path, vendor)
    (vendor / "requirements.txt").write_text("pyotp==2.9.1\n", encoding="utf-8")

    result = _run(tmp_path, vendor)

    assert result.returncode == 0, result.stderr
    assert _current(tmp_path).name == f"{_src_digest(vendor / 'mailon')}-{_req_digest(vendor)}"
    assert legacy.is_dir()
    assert (legacy / "runtime-manifest.json").is_file()
