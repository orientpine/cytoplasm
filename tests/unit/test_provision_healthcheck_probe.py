"""RC-2 owner-run healthcheck forced-command wrapper provisioner.

The generator already owns command discovery.  The missing boundary is a repository-shipped
installer that can be rerun safely: valid generated Bash only, atomic replacement, byte
read-back, and no rewrite when the installed wrapper already matches.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_INSTALLER: Final = _REPO / "automation" / "provision-healthcheck-probe.sh"
_GENERATOR: Final = _REPO / "automation" / "healthcheck_probe_wrapper.sh"


def _fixture(tmp_path: Path, generated: str) -> tuple[dict[str, str], Path]:
    payload = tmp_path / "generated-wrapper"
    _ = payload.write_text(generated, encoding="utf-8")
    generator = tmp_path / "generator"
    _ = generator.write_text(
        "#!/usr/bin/env bash\n"
        '[[ "$1" == "--print" ]] || exit 2\n'
        'cat "$FAKE_GENERATED_WRAPPER"\n',
        encoding="utf-8",
    )
    generator.chmod(0o755)
    target = tmp_path / "home" / ".local" / "libexec" / "autophagy-healthcheck-probe"
    env = {
        **os.environ,
        "FAKE_GENERATED_WRAPPER": str(payload),
        "HEALTHCHECK_WRAPPER_GENERATOR": str(generator),
        "HEALTHCHECK_WRAPPER_PATH": str(target),
    }
    return env, target


def _run(env: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(_INSTALLER), *arguments),
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_first_run_installs_and_second_run_is_a_true_noop(tmp_path: Path) -> None:
    env, target = _fixture(tmp_path, "#!/usr/bin/env bash\nexit 0\n")

    first = _run(env, "primary")
    before = target.stat()
    second = _run(env, "primary")
    after = target.stat()

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert "WRAPPER-INSTALLED" in first.stdout
    assert "WRAPPER-UNCHANGED" in second.stdout
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert os.access(target, os.X_OK)


def test_invalid_generated_bash_preserves_the_installed_wrapper(tmp_path: Path) -> None:
    env, target = _fixture(tmp_path, "#!/usr/bin/env bash\nif\n")
    target.parent.mkdir(parents=True)
    _ = target.write_text("known-good\n", encoding="utf-8")

    result = _run(env, "primary")

    assert result.returncode != 0
    assert "WRAPPER-PROVISION-BLOCK" in result.stderr
    assert target.read_text(encoding="utf-8") == "known-good\n"


def test_a_drifted_wrapper_is_replaced_and_read_back(tmp_path: Path) -> None:
    expected = "#!/usr/bin/env bash\nprintf 'ok\\n'\n"
    env, target = _fixture(tmp_path, expected)
    target.parent.mkdir(parents=True)
    _ = target.write_text("stale\n", encoding="utf-8")

    result = _run(env, "primary")

    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == expected
    assert "WRAPPER-INSTALLED" in result.stdout


def test_help_does_not_generate_or_install(tmp_path: Path) -> None:
    env, target = _fixture(tmp_path, "#!/usr/bin/env bash\nexit 0\n")

    result = _run(env, "--help")

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert not target.exists()


def test_installer_ships_executable() -> None:
    assert os.access(_INSTALLER, os.X_OK)


def test_generator_install_compatibility_delegates_to_the_provisioner(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "calls"
    provisioner = tmp_path / "provisioner"
    _ = provisioner.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "{calls}"\n',
        encoding="utf-8",
    )
    provisioner.chmod(0o755)

    result = subprocess.run(
        ("bash", str(_GENERATOR), "--install", "primary"),
        cwd=_REPO,
        env={**os.environ, "HEALTHCHECK_PROVISIONER": str(provisioner)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.read_text(encoding="utf-8") == "primary\n"
