from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation.node_config import default_node_config
from automation.update_trust_state import (
    ReleaseFloorError,
    advance_release_floor,
    load_release_floor,
    privileged_advance_release_floor,
    release_floor,
    release_floor_path,
    save_release_floor,
)

_REPO = Path(__file__).resolve().parents[2]
_PROVISION = _REPO / "automation" / "provision-deploy-converge.sh"
_SHA_1 = "a" * 40
_SHA_2 = "b" * 40


def test_authoritative_floor_is_outside_the_ops_private_root() -> None:
    config = default_node_config()

    path = release_floor_path(config)

    assert path == Path("/var/lib/autophagy/update-trust/release-floor.json")
    assert not path.is_relative_to(config.private_root)


def test_read_only_pre_gate_allows_missing_floor_without_creating_it(tmp_path: Path) -> None:
    missing = tmp_path / "root-state" / "release-floor.json"

    advance_release_floor(missing, "v1.0.0", _SHA_1)

    assert not missing.exists()


def test_read_only_pre_gate_fails_closed_when_floor_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "root-state" / "release-floor.json"
    save_release_floor(path, release_floor("v1.0.0", _SHA_1))
    original = Path.read_text

    def unreadable(
        self: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        if self == path:
            raise PermissionError("simulated ownership boundary")
        return original(self, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", unreadable)

    with pytest.raises(ReleaseFloorError, match=r"^RELEASE-FLOOR:.*cannot read"):
        advance_release_floor(path, "v1.0.1", _SHA_2)


def test_unprivileged_pre_gate_cannot_advance_authoritative_floor(tmp_path: Path) -> None:
    path = tmp_path / "root-state" / "release-floor.json"
    save_release_floor(path, release_floor("v1.0.0", _SHA_1))
    before = path.read_bytes()

    advance_release_floor(path, "v1.0.1", _SHA_2)

    assert path.read_bytes() == before
    assert load_release_floor(path) == release_floor("v1.0.0", _SHA_1)


def test_privileged_advance_creates_then_advances_monotonically(tmp_path: Path) -> None:
    path = tmp_path / "root-state" / "release-floor.json"

    privileged_advance_release_floor(path, "v1.0.0", _SHA_1)
    assert load_release_floor(path) == release_floor("v1.0.0", _SHA_1)
    privileged_advance_release_floor(path, "v1.0.1", _SHA_2)

    assert load_release_floor(path) == release_floor("v1.0.1", _SHA_2)
    with pytest.raises(ReleaseFloorError, match=r"^RELEASE-ROLLBACK:"):
        privileged_advance_release_floor(path, "v1.0.0", _SHA_1)


def _provision(prefix: Path, legacy: Path, authoritative: Path) -> subprocess.CompletedProcess[str]:
    shim = (
        "install() {\n"
        "  local args=()\n"
        "  while (( $# )); do\n"
        '    case "$1" in\n'
        "      -o|-g) shift 2 ;;\n"
        '      *) args+=("$1"); shift ;;\n'
        "    esac\n"
        "  done\n"
        '  command install "${args[@]}"\n'
        "}\n"
        "export -f install\n"
    )
    command = " ".join(
        (
            "DEPLOY_CONVERGE_ASSUME_ROOT=1",
            f'HELPER_PATH="{prefix}/usr/local/libexec/autophagy-converge-origin-main"',
            f'HELPER_LIBDIR="{prefix}/usr/local/libexec/autophagy-converge.d"',
            f'LOCK_DIR="{prefix}/srv/autophagy-private/locks"',
            f'LEGACY_RELEASE_FLOOR="{legacy}"',
            f'RELEASE_FLOOR_PATH="{authoritative}"',
            f'bash "{_PROVISION}"',
        )
    )
    return subprocess.run(
        ("bash", "-c", shim + command), capture_output=True, text=True, check=False
    )


def test_provision_migrates_existing_floor_exactly_without_deleting_source(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "private" / "deploy-reconcile" / "release-floor.json"
    authoritative = tmp_path / "var" / "lib" / "autophagy" / "update-trust" / "release-floor.json"
    save_release_floor(legacy, release_floor("v7.8.9", _SHA_2))
    exact = legacy.read_bytes()

    result = _provision(tmp_path, legacy, authoritative)

    assert result.returncode == 0, result.stdout + result.stderr
    assert authoritative.read_bytes() == exact
    assert legacy.read_bytes() == exact
    assert load_release_floor(authoritative) == release_floor("v7.8.9", _SHA_2)
