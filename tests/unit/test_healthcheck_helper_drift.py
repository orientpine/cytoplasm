from __future__ import annotations

import os
import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_PROBE = _REPO / "automation" / "release_helper_probe.sh"


def _run_probe(
    installed_helper: Path,
    installed_provenance: Path,
    release_root: Path,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "HEALTHCHECK_RELEASE_HELPER": str(installed_helper),
            "HEALTHCHECK_RELEASE_PROVENANCE": str(installed_provenance),
            "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(release_root),
        }
    )
    return subprocess.run(
        (
            "bash",
            "-c",
            f'source "{_PROBE}"; probe_release_helper_drift node ops ignored',
        ),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _matching_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    installed = tmp_path / "installed"
    source = tmp_path / "release" / "automation"
    installed.mkdir()
    source.mkdir(parents=True)
    helper = installed / "autophagy-install-release"
    provenance = installed / "release_provenance.py"
    helper.write_text("helper-v1\n", encoding="utf-8")
    provenance.write_text("provenance-v1\n", encoding="utf-8")
    (source / "release_store.py").write_bytes(helper.read_bytes())
    (source / "release_provenance.py").write_bytes(provenance.read_bytes())
    return helper, provenance, source.parent


def test_matching_privileged_helpers_pass(tmp_path: Path) -> None:
    helper, provenance, release_root = _matching_files(tmp_path)

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "HELPER-DRIFT-PASS" in result.stderr


def test_changed_privileged_helper_fails_with_check_marker(tmp_path: Path) -> None:
    helper, provenance, release_root = _matching_files(tmp_path)
    helper.write_text("drifted\n", encoding="utf-8")

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode != 0
    assert "HELPER-DRIFT" in result.stderr


def test_missing_helper_is_unknown_not_a_quiet_pass(tmp_path: Path) -> None:
    helper, provenance, release_root = _matching_files(tmp_path)
    helper.unlink()

    result = _run_probe(helper, provenance, release_root)

    assert result.returncode != 0
    assert "HELPER-DRIFT-UNKNOWN" in result.stderr
    assert "HELPER-DRIFT-PASS" not in result.stderr


def test_helper_probe_uses_no_ssh_or_sudo() -> None:
    result = subprocess.run(
        ("bash", "-c", f'source "{_PROBE}"; declare -f probe_release_helper_drift'),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ssh" not in result.stdout
    assert "sudo" not in result.stdout
