from __future__ import annotations

from pathlib import Path

import pytest

from automation.group_roster import MemberStatus, load_roster
from automation.group_roster.cli import main
from automation.report_hub.registry import load_registry

_VALID_PUBLIC_KEY = " ".join(
    (
        "ssh-ed25519",
        "AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g",
        "roster-example-admin",
    )
)


def _write_roster(path: Path) -> None:
    valid_yaml = "\n".join(
        (
            "schema: 1",
            "group_id: example-lab",
            "admin:",
            "  name: Example Admin",
            '  discord_user_id: "1001"',
            "  publisher_principal: publisher-example-admin@autophagy",
            "  signing_public_key: >-",
            f"    {_VALID_PUBLIC_KEY}",
            "members: []",
            "update_channel: ssh://git@example.invalid/example/group-skills.git",
            "",
        )
    )
    _ = path.write_text(valid_yaml, encoding="utf-8")


def test_validate_command_when_roster_valid_then_returns_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    _write_roster(path)

    # When
    result = main(["validate", str(path)])

    # Then
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.startswith("ROSTER-VALID ")
    assert captured.err == ""


def test_identity_command_when_roster_valid_then_reports_group_and_member_count(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    _write_roster(path)

    # When
    result = main(["identity", str(path)])

    # Then
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == 'ROSTER-IDENTITY group_id="example-lab" members=0\n'
    assert captured.err == ""


def test_validate_command_when_roster_invalid_then_returns_nonzero_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    _ = path.write_text("schema: 1\nadmin: null\n", encoding="utf-8")

    # When
    result = main(["validate", str(path)])

    # Then
    captured = capsys.readouterr()
    assert result != 0
    assert captured.out == ""
    assert captured.err.startswith("ROSTER-INVALID: ")
    assert "Traceback" not in captured.err


def test_add_member_command_when_entry_is_new_then_writes_active_member(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    _write_roster(path)

    # When
    result = main(
        [
            "add-member",
            str(path),
            "--name",
            "Example Member",
            "--discord-user-id",
            "1002",
            "--node-label",
            "member-node-a",
        ]
    )

    # Then
    captured = capsys.readouterr()
    roster = load_roster(path)
    assert result == 0
    assert len(roster.members) == 1
    assert roster.members[0].name == "Example Member"
    assert roster.members[0].status is MemberStatus.ACTIVE
    assert roster.update_channel == "ssh://git@example.invalid/example/group-skills.git"
    assert "ROSTER-MEMBER-ADDED" in captured.out
    assert "DEPLOY-KEY-REGISTRATION-REQUIRED" in captured.out
    assert captured.err == ""


def test_add_member_command_when_discord_id_exists_then_fails_without_rewriting(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    _write_roster(path)
    argv = [
        "add-member",
        str(path),
        "--name",
        "Example Member",
        "--discord-user-id",
        "1002",
        "--node-label",
        "member-node-a",
    ]
    assert main(argv) == 0
    before = path.read_bytes()
    _ = capsys.readouterr()

    # When
    result = main(argv)

    # Then
    assert result == 2
    assert path.read_bytes() == before
    assert "ROSTER-EDIT-REFUSED" in capsys.readouterr().err


def test_remove_member_command_when_active_then_records_revocation_without_remote_recall(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    _write_roster(path)
    assert main(
        [
            "add-member",
            str(path),
            "--name",
            "Example Member",
            "--discord-user-id",
            "1002",
            "--node-label",
            "member-node-a",
        ]
    ) == 0
    _ = capsys.readouterr()

    # When
    result = main(["remove-member", str(path), "--discord-user-id", "1002"])

    # Then
    captured = capsys.readouterr()
    roster = load_roster(path)
    assert result == 0
    assert roster.members[0].status is MemberStatus.REMOVED
    assert "ROSTER-REVOCATION-READY" in captured.out
    assert "DEPLOY-KEY-REVOCATION-REQUIRED" in captured.out
    assert "REMOTE-RECALL-LIMIT" in captured.out
    assert captured.err == ""


def test_peers_seed_command_when_roster_valid_then_emits_parseable_admin_and_member_entries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    _write_roster(path)
    assert main(
        [
            "add-member",
            str(path),
            "--name",
            "Example Member",
            "--discord-user-id",
            "1002",
            "--node-label",
            "member-node-a",
        ]
    ) == 0
    _ = capsys.readouterr()

    # When
    result = main(["peers-seed", str(path)])

    # Then
    generated_path = tmp_path / "peers.yaml"
    captured = capsys.readouterr()
    _ = generated_path.write_text(captured.out, encoding="utf-8")
    registry = load_registry(generated_path)
    assert result == 0
    assert captured.err == ""
    assert {(peer.agent_id, peer.bot_user_id, peer.bot_name) for peer in registry.peers} == {
        ("publisher-example-admin@autophagy", "1001", "Example Admin"),
        ("member-node-a", "1002", "Example Member"),
    }
    assert "account: agent" in captured.out
    assert "account: peer" in captured.out


def test_peers_seed_command_when_output_requested_then_writes_classification_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    output_path = tmp_path / "peers.yaml"
    _write_roster(path)

    # When
    result = main(["peers-seed", str(path), "--output", str(output_path)])

    # Then
    captured = capsys.readouterr()
    registry = load_registry(output_path)
    assert result == 0
    assert "PEERS-SEED-WRITTEN" in captured.out
    assert captured.err == ""
    assert len(registry.peers) == 1


def test_peers_seed_command_when_roster_malformed_then_returns_nonzero_without_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    path = tmp_path / "roster.yaml"
    output_path = tmp_path / "peers.yaml"
    _ = path.write_text("schema: 1\nadmin: null\n", encoding="utf-8")

    # When
    result = main(["peers-seed", str(path), "--output", str(output_path)])

    # Then
    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.startswith("ROSTER-INVALID: ")
    assert not output_path.exists()
