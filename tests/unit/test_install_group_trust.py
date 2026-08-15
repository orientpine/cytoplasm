from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from automation.install import trust_key_bootstrap as trust
from automation.install.checks import Status
from automation.install.installer import main as installer_main

_DEFAULT_KEY_SEED = bytes(range(32))


def _ssh_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _key_line(seed: bytes = _DEFAULT_KEY_SEED) -> str:
    material = base64.b64encode(
        _ssh_string(b"ssh-ed25519") + _ssh_string(seed)
    ).decode("ascii")
    return f"ssh-ed25519 {material} group-admin"


class RecordingFilesystem:
    def __init__(self) -> None:
        self.real: trust.RealFilesystem = trust.RealFilesystem()
        self.ownership: list[tuple[Path, int, int]] = []

    def lstat(self, path: Path) -> os.stat_result:
        return self.real.lstat(path)

    def read_text(self, path: Path) -> str:
        return self.real.read_text(path)

    def write_atomic(self, path: Path, content: str, mode: int) -> None:
        self.real.write_atomic(path, content, mode)

    def set_ownership(self, path: Path, uid: int, gid: int) -> None:
        self.ownership.append((path, uid, gid))


def _write_roster(path: Path, key_line: str) -> None:
    _ = path.write_text(
        "\n".join(
            (
                "schema: 1",
                "group_id: example-lab",
                "admin:",
                "  name: Example Admin",
                '  discord_user_id: "1001"',
                "  publisher_principal: publisher-example-admin@autophagy",
                f"  signing_public_key: {key_line}",
                "members: []",
                "",
            )
        ),
        encoding="utf-8",
    )


def test_group_plan_when_valid_then_targets_distinct_managed_signers_file() -> None:
    # Given / When
    plan = trust.plan_group_install(
        _key_line(),
        principal="publisher-example-admin@autophagy",
    )

    # Then
    assert plan.path == trust.MANAGED_SKILLS_ALLOWED_SIGNERS_PATH
    assert plan.path != trust.UPDATE_ALLOWED_SIGNERS_PATH
    assert plan.principal == "publisher-example-admin@autophagy"
    assert 'namespaces="git,autophagy-roster"' in plan.content


def test_group_plan_when_update_path_is_requested_then_refuses_crossing_trust_domains() -> None:
    with pytest.raises(trust.TrustKeyError, match="WRONG-FILE"):
        _ = trust.plan_group_install(
            _key_line(),
            principal="publisher-example-admin@autophagy",
            path=trust.UPDATE_ALLOWED_SIGNERS_PATH,
        )


def test_group_install_when_written_to_temp_path_then_reuses_fail_closed_verifier(
    tmp_path: Path,
) -> None:
    # Given
    target = tmp_path / "etc" / "managed-skills-allowed-signers"
    filesystem = RecordingFilesystem()
    plan = trust.plan_group_install(
        _key_line(),
        principal="publisher-example-admin@autophagy",
        path=target,
    )

    # When
    trust.apply_install(plan, filesystem)
    results = trust.verify_group_installed(
        target,
        filesystem,
        expected_fingerprint=plan.fingerprint,
        trusted_uids=frozenset({os.getuid()}),
        trusted_gids=frozenset({os.getgid()}),
    )

    # Then
    assert [result.name for result in results] == [
        "group-skill-trust.file",
        "group-skill-trust.fingerprint",
    ]
    assert [result.status for result in results] == [Status.PASS, Status.PASS]
    assert filesystem.ownership == [(target, 0, 0)]


def test_installer_when_group_roster_and_oob_fingerprint_are_given_then_plans_group_trust(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    key_line = _key_line()
    key_path = tmp_path / "update.pub"
    roster_path = tmp_path / "roster.yaml"
    _ = key_path.write_text(f"{key_line}\n", encoding="utf-8")
    _write_roster(roster_path, key_line)
    expected = trust.fingerprint(trust.parse_public_key(key_line))

    # When
    code = installer_main(
        (
            "--update-trust-key",
            str(key_path),
            "--group-roster",
            str(roster_path),
            "--expect-group-skill-fingerprint",
            expected,
            "--dry-run",
        )
    )

    # Then
    output = capsys.readouterr().out
    assert code == 0
    assert str(trust.MANAGED_SKILLS_ALLOWED_SIGNERS_PATH) in output
    assert "check group-skill-trust" in output


def test_installer_when_group_roster_lacks_oob_fingerprint_then_refuses_before_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    key_line = _key_line()
    key_path = tmp_path / "update.pub"
    roster_path = tmp_path / "roster.yaml"
    _ = key_path.write_text(f"{key_line}\n", encoding="utf-8")
    _write_roster(roster_path, key_line)

    # When
    code = installer_main(
        (
            "--update-trust-key",
            str(key_path),
            "--group-roster",
            str(roster_path),
            "--dry-run",
        )
    )

    # Then
    assert code == 1
    assert "GROUP-TRUST-FINGERPRINT-REQUIRED" in capsys.readouterr().err
