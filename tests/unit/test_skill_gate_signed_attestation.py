from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

import pytest

from automation import peer_attestation, skill_gate, skill_gate_specs, skill_gate_surface
from automation.interop.approval_surface import POLICY_VERSION, ChannelFacts
from automation.peer_signed_attestation import (
    SignedAttestationPayload,
    signed_attestation_preimage,
)


_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
_OWNER_ID = "111111111111111111"
_AGENT_BOT_ID = "222222222222222222"
_CHANNEL_ID = "100000000000000009"
_MESSAGE_ID = "100000000000000010"
_NONCE = "1" * 32
_DIGEST = "a" * 64
_SKILL = "calendar"
_PASS_REVIEW = "- review: PASS"


class _FakeDirectory:
    def owner_dm(self) -> str:
        raise AssertionError("skill deployment must stay on the supply-chain surface")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


class FakeDiscord:
    def __init__(self, content: str) -> None:
        self.content = content
        self.patch_count = 0
        self.after_queries = 0

    def api(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        if path == "/users/@me":
            return {"id": _AGENT_BOT_ID, "bot": True}
        if path == f"/channels/{_CHANNEL_ID}/messages/{_MESSAGE_ID}" and method == "GET":
            return {
                "id": _MESSAGE_ID,
                "channel_id": _CHANNEL_ID,
                "timestamp": (_NOW - timedelta(minutes=2)).isoformat(),
                "author": {"id": _AGENT_BOT_ID, "bot": True},
                "content": self.content,
            }
        if path == f"/channels/{_CHANNEL_ID}/messages/{_MESSAGE_ID}" and method == "PATCH":
            assert payload is not None
            self.patch_count += 1
            self.content = str(payload["content"])
            return {"id": _MESSAGE_ID, "content": self.content}
        if path.startswith(f"/channels/{_CHANNEL_ID}/messages?after="):
            self.after_queries += 1
            return ["a Discord attestation must not satisfy signed mode"]
        if "/reactions/" in path:
            emoji = unquote(path.split("/reactions/")[1].split("?")[0])
            return [{"id": _OWNER_ID, "bot": False}] if emoji == skill_gate.APPROVE_EMOJI else []
        raise AssertionError(f"unexpected Discord call: {method} {path}")


def _key(tmp_path: Path) -> Path:
    private_key = tmp_path / "peer"
    _ = subprocess.run(
        ("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)),
        check=True,
        capture_output=True,
    )
    private_key.with_suffix(".pub").chmod(0o644)
    private_key.parent.chmod(0o755)
    return private_key


def _blob(private_key: Path) -> str:
    payload = SignedAttestationPayload(
        request=_NONCE,
        skill=_SKILL,
        digest=_DIGEST,
        verdict="PASS",
        attested_at=_NOW - timedelta(seconds=1),
        approval_channel=_CHANNEL_ID,
        approval_message=_MESSAGE_ID,
    )
    completed = subprocess.run(
        (
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            "autophagy-peer-attest",
        ),
        input=signed_attestation_preimage(payload),
        check=True,
        capture_output=True,
    )
    return peer_attestation.format_signed_attestation(
        payload,
        completed.stdout.decode("ascii"),
    )


def _args(public_key: Path, blob: str) -> argparse.Namespace:
    return argparse.Namespace(
        skill=_SKILL,
        hash=_DIGEST,
        message_id=_MESSAGE_ID,
        deploy_nonce=_NONCE,
        injection_file="",
        provenance_file="",
        peer_attest_mode="signed",
        peer_attest_public_key=str(public_key),
        peer_attestation_blob=blob,
        peer_attestation_stdin=False,
    )


def _configure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    public_key: Path,
) -> FakeDiscord:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    pending_spec = skill_gate_specs.DeploySpec(
        skill=_SKILL,
        digest=_DIGEST,
        deploy_nonce=_NONCE,
        review_status=_PASS_REVIEW,
        provenance=skill_gate_specs.provenance_of(""),
        binding=skill_gate._REQUEST_BINDING,
        peer_attest_mode="signed",
        peer_status=skill_gate.SIGNED_PEER_PENDING,
    )
    fake = FakeDiscord(pending_spec.render())
    pending = tmp_path / "skill-gate" / "pending"
    pending.mkdir(parents=True)
    _ = (pending / f"{_SKILL}.json").write_text(
        json.dumps(
            {
                "action_hash": pending_spec.action_hash(),
                "approval_action": "skill.deploy",
                "approval_destination": f"skill:{_SKILL}",
                "channel_id": _CHANNEL_ID,
                "deploy_nonce": _NONCE,
                "hash": _DIGEST,
                "kind": "skill-deploy",
                "message_id": _MESSAGE_ID,
                "policy_version": str(POLICY_VERSION),
                "surface": "skill-approvals",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        peer_attestation,
        "_trusted_owner_uids",
        lambda: frozenset({os.getuid()}),
    )
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "review_status_line", lambda *_args: _PASS_REVIEW)
    monkeypatch.setattr(
        skill_gate,
        "_deploy_bindings",
        lambda skill: skill_gate_surface.SupplyChainSurface(
            skill_gate_surface.deploy_kind(skill), _OWNER_ID, _FakeDirectory()
        ),
    )
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    monkeypatch.setattr(skill_gate, "_now", lambda: _NOW)
    public_key.chmod(0o644)
    return fake


def test_cmd_check_when_peer_attest_mode_is_unset_then_rejects_before_discord(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a direct gate invocation omits the install-level attestation mode.
    args = argparse.Namespace(skill=_SKILL, hash=_DIGEST)
    monkeypatch.setattr(
        skill_gate,
        "_api",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Discord must not be read")),
    )

    # When: cmd_check reaches its configuration boundary.
    result = skill_gate.cmd_check(args)

    # Then: unset is a hard configuration rejection, never a Discord-mode fallback.
    assert result == 2
    assert "peer_attest_mode" in capsys.readouterr().err


def test_cmd_check_when_signed_record_is_valid_then_promotes_message_and_approves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a signed-mode request, trusted peer public key, and matching signed stdout record.
    private_key = _key(tmp_path)
    fake = _configure(tmp_path, monkeypatch, private_key.with_suffix(".pub"))

    # When: the owner gate verifies the peer record and the owner's reaction.
    result = skill_gate.cmd_check(_args(private_key.with_suffix(".pub"), _blob(private_key)))

    # Then: the existing message is upgraded in place with visible peer evidence and approved.
    assert result == 0
    assert fake.patch_count == 1
    assert "peer verdict: PASS (key fp SHA256:" in fake.content
    assert fake.after_queries == 0


def test_cmd_check_when_signed_blob_is_absent_then_never_falls_back_to_discord_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: signed mode has a trusted key but the stdout courier carries no record.
    private_key = _key(tmp_path)
    fake = _configure(tmp_path, monkeypatch, private_key.with_suffix(".pub"))

    # When: the gate checks signed mode with no blob.
    result = skill_gate.cmd_check(_args(private_key.with_suffix(".pub"), ""))

    # Then: it rejects without querying Discord attestations as a silent fallback.
    assert result == 1
    assert fake.patch_count == 0
    assert fake.after_queries == 0
