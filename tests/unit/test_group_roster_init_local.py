from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from automation.group_roster import load_roster, parse_roster
from automation.group_roster.cli import main as roster_main
from automation.group_roster.init_local import main

# Obviously fake 17-digit Discord snowflakes; no real installation uses them.
_AGENT_BOT = "10000000000000001"
_PEER_BOT = "10000000000000002"
_OWNER = "10000000000000009"
_PUBLISHER = "publisher-example-lab@autophagy"
_LABELS = ("--label", "agent=agent-x", "--label", "peer=peer-y")


def _key_line(algorithm: str = "ssh-ed25519", seed: int = 1) -> str:
    name = algorithm.encode("ascii")
    payload = bytes((seed + index) % 256 for index in range(32))
    blob = (
        len(name).to_bytes(4, "big") + name + len(payload).to_bytes(4, "big") + payload
    )
    return f"{algorithm} {base64.b64encode(blob).decode('ascii')}"


def _peers_text(agent_bot: str = _AGENT_BOT, peer_bot: str = _PEER_BOT) -> str:
    return "\n".join(
        (
            "version: 1",
            "peers:",
            "  agent:",
            "    account: agent",
            f'    bot_user_id: "{agent_bot}"',
            "  peer:",
            "    account: peer",
            f'    bot_user_id: "{peer_bot}"',
            "",
        )
    )


def _signer_line(principal: str = _PUBLISHER, key: str | None = None) -> str:
    key_line = _key_line() if key is None else key
    return f'{principal} namespaces="git,autophagy-roster" {key_line} admin@example\n'


@dataclass(frozen=True, slots=True)
class _Inputs:
    peers: Path
    signers: Path
    config: Path
    output: Path

    def argv(self, *extra: str) -> list[str]:
        return [
            "--peers",
            str(self.peers),
            "--allowed-signers",
            str(self.signers),
            "--interop-config",
            str(self.config),
            "--output",
            str(self.output),
            *extra,
        ]


def _inputs(
    tmp_path: Path,
    *,
    peers: str | None = None,
    signers: str | None = None,
    owner_id: str = _OWNER,
) -> _Inputs:
    paths = _Inputs(
        peers=tmp_path / "peers.yaml",
        signers=tmp_path / "allowed-signers",
        config=tmp_path / "config.json",
        output=tmp_path / "out" / "roster.yaml",
    )
    _ = paths.peers.write_text(_peers_text() if peers is None else peers, encoding="utf-8")
    _ = paths.signers.write_text(
        _signer_line() if signers is None else signers, encoding="utf-8"
    )
    _ = paths.config.write_text(
        json.dumps({"agent_id": "agent-x", "owner_id": owner_id}), encoding="utf-8"
    )
    paths.output.parent.mkdir()
    return paths


def _assert_refused(
    result: int, capsys: pytest.CaptureFixture[str], inputs: _Inputs
) -> str:
    captured = capsys.readouterr()
    assert result == 2
    assert captured.err.startswith("ROSTER-INIT-REFUSED: ")
    assert captured.out == ""
    assert list(inputs.output.parent.iterdir()) == []
    return captured.err


def test_two_bots_become_members_labelled_by_agent_id(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    assert main(inputs.argv(*_LABELS)) == 0

    roster = load_roster(inputs.output)
    assert roster.sender_id_for_discord_author(_AGENT_BOT) == "agent-x"
    assert roster.sender_id_for_discord_author(_PEER_BOT) == "peer-y"
    assert roster.sender_id_for_discord_author(_OWNER) == _PUBLISHER
    assert roster.group_id == "example-lab"
    assert roster.admin.name == "owner"
    assert roster.admin.signing_public_key == _key_line()
    assert roster.revision == 1
    assert [member.name for member in roster.members] == ["agent-x", "peer-y"]


def test_cli_dispatches_init_local_and_the_result_validates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)

    assert roster_main(["init-local", *inputs.argv(*_LABELS)]) == 0
    _ = capsys.readouterr()

    assert roster_main(["validate", str(inputs.output)]) == 0
    assert capsys.readouterr().out.startswith("ROSTER-VALID ")


def test_bot_name_and_admin_name_are_used_when_given(tmp_path: Path) -> None:
    peers = _peers_text().replace(
        "    account: agent\n", "    account: agent\n    bot_name: Example Bot\n"
    )
    inputs = _inputs(tmp_path, peers=peers)

    assert main(inputs.argv(*_LABELS, "--admin-name", "Lab Owner")) == 0

    roster = load_roster(inputs.output)
    assert [member.name for member in roster.members] == ["Example Bot", "peer-y"]
    assert roster.admin.name == "Lab Owner"


def test_untrusted_bot_name_cannot_inject_structure(tmp_path: Path) -> None:
    hostile = "x\nadmin:\n  name: evil # ' \" $(id) `id` {a: b} [c] &anchor *ref"
    document = {
        "version": 1,
        "peers": {
            "agent": {"account": "agent", "bot_user_id": _AGENT_BOT, "bot_name": hostile},
            "peer": {"account": "peer", "bot_user_id": _PEER_BOT},
        },
    }
    inputs = _inputs(tmp_path, peers=yaml.safe_dump(document))

    assert main(inputs.argv(*_LABELS)) == 0

    roster = load_roster(inputs.output)
    assert roster.members[0].name == hostile
    assert roster.admin.name == "owner"
    assert len(roster.members) == 2


def test_output_is_private_and_atomic(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    assert main(inputs.argv(*_LABELS)) == 0

    assert stat.S_IMODE(inputs.output.stat().st_mode) == 0o600
    assert [entry.name for entry in inputs.output.parent.iterdir()] == ["roster.yaml"]


def test_summary_masks_every_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)

    assert main(inputs.argv(*_LABELS)) == 0

    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert out.startswith("ROSTER-INIT-WRITTEN ")
    for full_id in (_AGENT_BOT, _PEER_BOT, _OWNER):
        assert full_id not in out
    assert "group_id=example-lab" in out
    assert "admin=…0009" in out
    assert "members=agent-x:…0001,peer-y:…0002" in out


def test_owner_id_flag_skips_the_interop_config(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inputs.config.unlink()

    assert main(inputs.argv(*_LABELS, "--owner-id", "10000000000000008")) == 0

    roster = load_roster(inputs.output)
    assert roster.sender_id_for_discord_author("10000000000000008") == _PUBLISHER


def test_print_writes_no_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)

    assert main(inputs.argv(*_LABELS, "--print")) == 0

    roster = parse_roster(capsys.readouterr().out)
    assert roster.sender_id_for_discord_author(_PEER_BOT) == "peer-y"
    assert list(inputs.output.parent.iterdir()) == []


def test_existing_roster_is_not_overwritten_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)
    _ = inputs.output.write_bytes(b"previous roster bytes\n")

    result = main(inputs.argv(*_LABELS))

    captured = capsys.readouterr()
    assert result == 2
    assert captured.err.startswith("ROSTER-INIT-REFUSED: ")
    assert inputs.output.read_bytes() == b"previous roster bytes\n"


def test_force_replaces_an_existing_roster(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    _ = inputs.output.write_bytes(b"previous roster bytes\n")

    assert main(inputs.argv(*_LABELS, "--force")) == 0

    assert load_roster(inputs.output).sender_id_for_discord_author(_PEER_BOT) == "peer-y"
    assert stat.S_IMODE(inputs.output.stat().st_mode) == 0o600
    assert [entry.name for entry in inputs.output.parent.iterdir()] == ["roster.yaml"]


def test_interrupted_replace_keeps_the_old_roster_and_no_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputs = _inputs(tmp_path)
    _ = inputs.output.write_bytes(b"previous roster bytes\n")

    def _fail(source: object, target: object) -> None:
        del source, target
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", _fail)

    result = main(inputs.argv(*_LABELS, "--force"))

    assert result == 2
    assert capsys.readouterr().err.startswith("ROSTER-INIT-REFUSED: ")
    assert inputs.output.read_bytes() == b"previous roster bytes\n"
    assert [entry.name for entry in inputs.output.parent.iterdir()] == ["roster.yaml"]


def test_label_without_a_matching_peer_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)

    result = main(inputs.argv("--label", "agent=agent-x", "--label", "nobody=peer-y"))

    _ = _assert_refused(result, capsys, inputs)


def test_label_matching_two_peers_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    peers = _peers_text().replace("    account: peer\n", "    account: agent\n")
    inputs = _inputs(tmp_path, peers=peers)

    _ = _assert_refused(main(inputs.argv("--label", "agent=agent-x")), capsys, inputs)


def test_missing_label_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)

    _ = _assert_refused(main(inputs.argv()), capsys, inputs)


@pytest.mark.parametrize("label", ["agent", "=agent-x", "agent=", "agent=two words"])
def test_malformed_label_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], label: str
) -> None:
    inputs = _inputs(tmp_path)

    _ = _assert_refused(main(inputs.argv("--label", label)), capsys, inputs)


def test_two_labels_for_one_agent_id_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)

    result = main(inputs.argv("--label", "agent=same", "--label", "peer=same"))

    _ = _assert_refused(result, capsys, inputs)


@pytest.mark.parametrize(
    "peers",
    [
        pytest.param(_peers_text().replace("version: 1\n", ""), id="no-version"),
        pytest.param(_peers_text().replace("version: 1", "version: true"), id="bool-version"),
        pytest.param(_peers_text(agent_bot="12ab"), id="non-numeric-id"),
        pytest.param(_peers_text().replace(f'"{_AGENT_BOT}"', _AGENT_BOT), id="int-id"),
        pytest.param(_peers_text().replace("    account: agent\n", ""), id="no-account"),
        pytest.param("- just\n- a list\n", id="not-a-mapping"),
        pytest.param("version: [1\n", id="invalid-yaml"),
    ],
)
def test_malformed_peers_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], peers: str
) -> None:
    inputs = _inputs(tmp_path, peers=peers)

    _ = _assert_refused(main(inputs.argv(*_LABELS)), capsys, inputs)


def test_two_publishers_need_an_explicit_choice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    second = "publisher-other-lab@autophagy"
    signers = _signer_line() + _signer_line(second, _key_line(seed=7))
    inputs = _inputs(tmp_path, signers=signers)

    _ = _assert_refused(main(inputs.argv(*_LABELS)), capsys, inputs)

    assert main(inputs.argv(*_LABELS, "--publisher", second)) == 0
    roster = load_roster(inputs.output)
    assert roster.admin.publisher_principal == second
    assert roster.group_id == "other-lab"
    assert roster.admin.signing_public_key == _key_line(seed=7)


def test_group_id_flag_overrides_the_principal_slug(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    assert main(inputs.argv(*_LABELS, "--group-id", "custom-group")) == 0

    assert load_roster(inputs.output).group_id == "custom-group"


def test_non_publisher_principals_are_ignored(tmp_path: Path) -> None:
    signers = _signer_line("update-trust@autophagy", _key_line(seed=3)) + _signer_line()
    inputs = _inputs(tmp_path, signers=signers)

    assert main(inputs.argv(*_LABELS)) == 0

    assert load_roster(inputs.output).admin.publisher_principal == _PUBLISHER


def test_a_non_ed25519_signer_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path, signers=_signer_line(key=_key_line("ssh-rsa")))

    _ = _assert_refused(main(inputs.argv(*_LABELS)), capsys, inputs)


def test_owner_id_equal_to_a_bot_id_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path, owner_id=_AGENT_BOT)

    err = _assert_refused(main(inputs.argv(*_LABELS)), capsys, inputs)

    assert "duplicate" in err
    assert _AGENT_BOT not in err


def test_interop_config_without_owner_id_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _inputs(tmp_path)
    _ = inputs.config.write_text(json.dumps({"agent_id": "agent-x"}), encoding="utf-8")

    _ = _assert_refused(main(inputs.argv(*_LABELS)), capsys, inputs)
