from __future__ import annotations

import subprocess
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation import peer_attest, peer_attestation
from automation.skill_review import skill_digest


_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
_NONCE = "1" * 32
_CHANNEL_ID = "100000000000000009"
_MESSAGE_ID = "100000000000000010"


@dataclass
class FakeDiscordTransport:
    replies: list[tuple[str, str, str]] = field(default_factory=list)

    def replies_after(self, channel_id: str, message_id: str) -> list[dict[str, object]]:
        assert (channel_id, message_id) == (_CHANNEL_ID, _MESSAGE_ID)
        return []

    def post_reply(self, channel_id: str, message_id: str, content: str) -> None:
        self.replies.append((channel_id, message_id, content))


def _skill(tmp_path: Path, scenario: str = "echo SCENARIO-PASS") -> Path:
    skill_dir = tmp_path / "calendar"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    _ = (skill_dir / "SKILL.md").write_text(
        "---\nname: calendar\ndescription: Deterministic calendar skill.\n---\n",
        encoding="utf-8",
    )
    scenario_path = scripts / "scenario.sh"
    _ = scenario_path.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\n{scenario}\n",
        encoding="utf-8",
    )
    scenario_path.chmod(0o700)
    return skill_dir


def _key(tmp_path: Path) -> Path:
    private_key = tmp_path / "peer_attest_ed25519"
    _ = subprocess.run(
        ("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)),
        check=True,
        capture_output=True,
    )
    private_key.chmod(0o600)
    return private_key


def _request(skill_dir: Path) -> peer_attest.AttestRequest:
    return peer_attest.AttestRequest(
        skill="calendar",
        staged_dir=skill_dir,
        expected_digest=skill_digest(skill_dir),
        request_message_id=_MESSAGE_ID,
        deploy_nonce=_NONCE,
        channel_id=_CHANNEL_ID,
    )


def test_signed_mode_when_transition_peer_has_token_then_posts_discord_and_emits_record(
    tmp_path: Path,
) -> None:
    # Given: the rollout peer still has its Discord transport while its signing key is ready.
    request = _request(_skill(tmp_path))
    transport = FakeDiscordTransport()

    # When: signed output is enabled during the coexistence rollout step.
    context = peer_attest.SignedAttestContext(_key(tmp_path), transport, _NOW)
    result = peer_attest.attest_signed(request, context)

    # Then: the existing Discord reply remains and the same verdict also has one signed record.
    assert result.exit_code == 0
    assert len(transport.replies) == 1
    parsed = peer_attestation.parse_signed_attestation(result.signed_record)
    assert parsed is not None
    assert parsed.payload.digest == result.digest
    assert parsed.payload.verdict == "PASS"


def test_signed_mode_when_peer_has_no_discord_token_then_emits_record_without_transport(
    tmp_path: Path,
) -> None:
    # Given: a new-install peer has only its Unix-isolated signing key and no Discord bot.
    request = _request(_skill(tmp_path))

    # When: it independently reviews and signs the staged bytes.
    context = peer_attest.SignedAttestContext(_key(tmp_path), None, _NOW)
    result = peer_attest.attest_signed(request, context)

    # Then: one valid signed record is sufficient and no Discord transport is required.
    assert result.exit_code == 0
    assert peer_attestation.parse_signed_attestation(result.signed_record) is not None


def test_signed_mode_when_review_fails_then_signs_fail_and_returns_nonzero(tmp_path: Path) -> None:
    # Given: the staged scenario fails the unchanged deterministic review.
    request = _request(_skill(tmp_path, "echo scenario-failed"))

    # When: signed mode evaluates the same four checks.
    context = peer_attest.SignedAttestContext(_key(tmp_path), None, _NOW)
    result = peer_attest.attest_signed(request, context)

    # Then: the peer emits an authentic FAIL record but never an approvable exit status.
    parsed = peer_attestation.parse_signed_attestation(result.signed_record)
    assert result.exit_code == 1
    assert parsed is not None and parsed.payload.verdict == "FAIL"


def test_main_when_signed_mode_has_no_token_then_stdout_is_exactly_one_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: trusted runtime guards, a configured signed request, and no peer Discord token.
    request = replace(
        _request(_skill(tmp_path)),
        mode=peer_attest.AttestationMode.SIGNED,
    )
    private_key = _key(tmp_path)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setattr(peer_attest, "_find_tamperable_path", lambda _root: None)
    monkeypatch.setattr(peer_attest, "_is_trusted_attestor_root", lambda _root: True)
    monkeypatch.setattr(peer_attest, "_parse_request", lambda _argv: request)
    monkeypatch.setattr(peer_attest, "_peer_signing_key", lambda: private_key)

    def unexpected_transport(_token: str) -> peer_attest.DiscordRestTransport:
        raise AssertionError("signed mode must not require a second Discord bot")

    monkeypatch.setattr(peer_attest, "DiscordRestTransport", unexpected_transport)

    # When: the real CLI boundary runs in signed mode.
    code = peer_attest.main(())

    # Then: stdout is only one framed record and every diagnostic is on stderr.
    streams = capsys.readouterr()
    assert code == 0
    assert streams.out.count("-----BEGIN SSH SIGNATURE-----") == 1
    assert peer_attestation.parse_signed_attestation(streams.out) is not None
    assert "PEER-ATTEST-PASS" not in streams.out
    assert "PEER-ATTEST-PASS" in streams.err


def test_parse_request_when_mode_is_signed_then_preserves_configured_variant() -> None:
    # Given: the deploy pipeline passes the install-level mode into the peer CLI.
    arguments = [
        "--skill",
        "calendar",
        "--staged-dir",
        "/tmp/calendar",
        "--hash",
        "a" * 64,
        "--request-message-id",
        _MESSAGE_ID,
        "--deploy-nonce",
        _NONCE,
        "--channel-id",
        _CHANNEL_ID,
        "--mode",
        "signed",
    ]

    # When: the peer CLI parses the node-config-selected mode.
    request = peer_attest._parse_request(arguments)

    # Then: it is a closed signed variant rather than an unvalidated string.
    assert request is not None and request.mode is peer_attest.AttestationMode.SIGNED
