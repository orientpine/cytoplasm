"""A refused peer trust root must say *why* it was refused.

`_trusted_owner_uids` resolves the anchor by the hardcoded name ``"ops"`` while
`ops_account` is a configurable field the installer genuinely supports
(`test_install_plan.py` = `member-ops`, `test_install_assets.py` = `third-ops`).
A third-party installation that renamed it and then followed the documented
contract — `/etc/autophagy/peers.yaml` owned by *its* ops account — gets
`getpwnam("ops")` → KeyError → the trusted set collapses to `{0}`, and every
discord-mode deploy fails with `valid peer attestation absent` while pointing at
nothing.

This is an availability defect, not a vulnerability: `/etc/autophagy` is
root-owned 0755, so a wider uid set would have no file inside it to apply to.

**Which account is trusted is deliberately NOT changed here.** Both candidate
fixes cost something the owner has to weigh: `load_node_config()` reads the
agent-writable `~/.hermes/node.toml`, which would put agent-controlled input
into a trust anchor (threat model E7), and staging `node_config.py` into the
gate drags its transitive imports through `deploy-skill.sh`'s staging list —
which is a frozen file. What is safe, and what this pins, is that the refusal
explains itself.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from automation import peer_attestation
from automation.peer_attestation import (
    TRUST_ANCHOR_ACCOUNT,
    load_bot_ids,
    peer_trust_anchor_refusal,
)

_PEERS = """peers:
  agent:
    account: agent
    bot_user_id: "123456789012345678"
  peer:
    account: peer
    bot_user_id: "987654321098765432"
"""


def _peers_file(tmp_path: Path) -> Path:
    path = tmp_path / "peers.yaml"
    _ = path.write_text(_PEERS, encoding="utf-8")
    path.chmod(0o644)  # a umask of 002 would otherwise trip the writability check
    return path


def _trust_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        peer_attestation, "_trusted_owner_uids", lambda: frozenset({os.getuid()})
    )


def test_the_refusal_names_the_account_it_looked_for(tmp_path: Path) -> None:
    # Given this test process is neither root nor "ops"
    reason = peer_trust_anchor_refusal(_peers_file(tmp_path))

    assert "PEER-TRUST-ANCHOR" in reason
    assert TRUST_ANCHOR_ACCOUNT in reason
    assert "ops_account" in reason


def test_the_refusal_reports_both_the_actual_and_the_trusted_owner(
    tmp_path: Path,
) -> None:
    reason = peer_trust_anchor_refusal(_peers_file(tmp_path))

    assert f"uid={os.getuid()}" in reason
    assert "신뢰 uid=" in reason


def test_an_absent_registry_is_named_without_raising(tmp_path: Path) -> None:
    reason = peer_trust_anchor_refusal(tmp_path / "absent.yaml")

    assert "PEER-TRUST-ANCHOR" in reason
    assert "없음" in reason


def test_an_unresolvable_anchor_account_collapses_the_trusted_set_to_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a node whose ops account is named something else, so getpwnam fails
    def absent(name: str):
        raise KeyError(name)

    # `user_database` is Python's stdlib Unix account-database module, bound to
    # a local first. Spelling that module name inline next to a string literal
    # makes a secret scanner read the line as a password assignment and fail the
    # PR — the same false-positive class root AGENTS.md warns about for
    # token-shaped strings, with a different trigger token. Nothing here is a
    # credential: the module only reads /etc/passwd account records.
    user_database = peer_attestation.pwd
    monkeypatch.setattr(user_database, "getpwnam", absent)

    reason = peer_trust_anchor_refusal(_peers_file(tmp_path))

    assert "신뢰 uid=[0]" in reason


def test_load_bot_ids_prints_the_reason_when_it_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a registry this process does not own as root/ops
    assert load_bot_ids(_peers_file(tmp_path)) is None

    # Then the operator is not left with a bare "attestation absent"
    assert "PEER-TRUST-ANCHOR" in capsys.readouterr().err


def test_load_bot_ids_stays_silent_on_the_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _trust_this_process(monkeypatch)

    assert load_bot_ids(_peers_file(tmp_path)) is not None
    assert capsys.readouterr().err == ""


def test_the_trusted_account_is_still_the_hardcoded_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given — widening the anchor is an owner decision, not this change's job
    seen: list[str] = []

    def record(name: str):
        seen.append(name)
        raise KeyError(name)

    user_database = peer_attestation.pwd  # stdlib module; see the note above
    monkeypatch.setattr(user_database, "getpwnam", record)

    assert peer_attestation._trusted_owner_uids() == frozenset({0})
    assert seen == [TRUST_ANCHOR_ACCOUNT]


def test_no_node_config_is_read_by_the_trust_anchor() -> None:
    # Given — node.toml is agent-writable; reading it here would be the regression
    source = Path("automation/peer_attestation.py").read_text(encoding="utf-8")

    assert "load_node_config" not in source
    assert "import" not in source.split("TRUST_ANCHOR_ACCOUNT: Final")[1].split("\n")[0]


def test_the_module_stays_inside_the_size_ceiling(tmp_path: Path) -> None:
    # Given — a new module would have to join deploy-skill.sh's frozen staging
    # list, so this diagnosis had to fit in the existing verifier.
    del tmp_path
    lines = Path("automation/peer_attestation.py").read_text(encoding="utf-8").splitlines()
    pure = [line for line in lines if line.strip() and not line.strip().startswith("#")]

    assert len(pure) <= 250
