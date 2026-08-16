from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
_MANIFEST_SCRIPT = _REPO / "automation" / "healthcheck_allowlist_manifest.sh"
_COMMITTED_MANIFEST = _REPO / "automation" / "healthcheck_allowlist_manifest.example.txt"
_SECURE_PATH = "/test-secure/sbin:/test-secure/bin"
_AGENT_UID = "4242"
_SYNTHETIC = (
    "synthetic nonexistent ops unit|user_unit_active|example-primary-node|ops|"
    "autophagy-healthcheck-synthetic-does-not-exist.service"
)


def _env(tmp_path: Path, *, secure_path: str = _SECURE_PATH) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "isolated-home")
    env["HEALTHCHECK_REPAIR_SECURE_PATH"] = secure_path
    env["HEALTHCHECK_REPAIR_AGENT_UID"] = _AGENT_UID
    env["HEALTHCHECK_SSH_USER"] = ""
    env["HEALTHCHECK_SSH_IDENTITY"] = ""
    return env


def _manifest(
    tmp_path: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(_MANIFEST_SCRIPT), *args),
        capture_output=True,
        text=True,
        check=False,
        env=env or _env(tmp_path),
        cwd=_REPO,
    )


def _definitions(tmp_path: Path) -> list[str]:
    script = (
        f'source "{_HEALTHCHECK}"; '
        'printf "%s\\n" "${LIVE_CHECKS[@]}"; '
        f'printf "%s\\n" "{_SYNTHETIC}"'
    )
    result = subprocess.run(
        ("bash", "-c", script),
        capture_output=True,
        text=True,
        check=False,
        env=_env(tmp_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.splitlines()


def _fake_ssh(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    journal = tmp_path / "ssh-command.txt"
    ssh = fake_bin / "ssh"
    _ = ssh.write_text(
        "#!/usr/bin/env bash\n"
        + "set -euo pipefail\n"
        + "printf '%s\\n' \"${*: -1}\" > \"$SSH_COMMAND_JOURNAL\"\n"
        + "cat >/dev/null\n"
        + "printf 't_manifest_test\\n'\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    return fake_bin, journal


def _capture_report_repair(tmp_path: Path, definition: str, index: int) -> str:
    case = tmp_path / str(index)
    case.mkdir()
    fake_bin, journal = _fake_ssh(case)
    check_name, probe_type, *_rest = definition.split("|")
    env = _env(tmp_path)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["SSH_COMMAND_JOURNAL"] = str(journal)
    script = f'source "{_HEALTHCHECK}"; report_repair "{check_name}" "{probe_type}"'
    result = subprocess.run(
        ("bash", "-c", script),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return journal.read_text(encoding="utf-8").rstrip("\n")


def _assert_manifest_matches(captured: list[str], expected: list[str]) -> None:
    assert captured == expected, (
        "repair command manifest mismatch; regenerate with: "
        "bash automation/healthcheck_allowlist_manifest.sh --print > "
        "automation/healthcheck_allowlist_manifest.example.txt"
    )


def test_healthcheck_has_a_source_execution_guard() -> None:
    body = _HEALTHCHECK.read_text(encoding="utf-8")

    assert 'if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi' in body


def test_manifest_prints_all_seventeen_d1_commands(tmp_path: Path) -> None:
    result = _manifest(tmp_path, "--print")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 17
    assert all("sudo -n -u agent -H env PATH=" in line for line in lines)
    assert all("XDG_RUNTIME_DIR=/run/user/4242" in line for line in lines)
    assert all("/usr/bin/python3 -I" in line for line in lines)


def test_committed_manifest_matches_generator_bytes(tmp_path: Path) -> None:
    env = _env(tmp_path, secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    env["HEALTHCHECK_REPAIR_AGENT_UID"] = "1002"
    result = _manifest(tmp_path, "--print", env=env)

    assert result.returncode == 0, result.stderr
    assert _COMMITTED_MANIFEST.read_bytes() == result.stdout.encode()


def test_report_repair_matches_manifest_through_seventeen_independent_calls(
    tmp_path: Path,
) -> None:
    definitions = _definitions(tmp_path)
    expected = _manifest(tmp_path, "--print").stdout.splitlines()

    captured = [
        _capture_report_repair(tmp_path, definition, index)
        for index, definition in enumerate(definitions)
    ]

    assert len(captured) == 17
    _assert_manifest_matches(captured, expected)


def test_different_builder_anchors_are_detected_as_red(tmp_path: Path) -> None:
    definitions = _definitions(tmp_path)
    expected = _manifest(
        tmp_path,
        "--print",
        env=_env(tmp_path, secure_path="/anchor-a/bin"),
    ).stdout.splitlines()
    captured: list[str] = []
    for index, definition in enumerate(definitions):
        original = _SECURE_PATH
        try:
            captured.append(_capture_report_repair(tmp_path, definition, index))
        finally:
            assert _SECURE_PATH == original

    with pytest.raises(AssertionError, match="regenerate with"):
        _assert_manifest_matches(captured, expected)


def test_manifest_check_failure_prints_the_regeneration_command(tmp_path: Path) -> None:
    stale = tmp_path / "stale-manifest.txt"
    _ = stale.write_text("stale\n", encoding="utf-8")
    env = _env(tmp_path)
    env["HEALTHCHECK_ALLOWLIST_MANIFEST_FILE"] = str(stale)

    result = _manifest(tmp_path, "--check", env=env)

    assert result.returncode != 0
    assert "bash automation/healthcheck_allowlist_manifest.sh --print" in result.stderr


def test_new_local_probes_are_registered_for_infra_failure_accounting() -> None:
    body = _HEALTHCHECK.read_text(encoding="utf-8")

    local_probes = body.split('readonly LOCAL_PROBES="', maxsplit=1)[1].split('"', maxsplit=1)[0]
    assert "update_trust" in local_probes
    assert "release_helper_drift" in local_probes
    assert "release_matches_origin" in local_probes
