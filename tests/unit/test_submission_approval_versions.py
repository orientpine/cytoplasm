"""Version selection through the real submission lifecycle and its live probe."""
from __future__ import annotations

import base64
import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from automation import skill_gate
from automation.group_roster.editor import render_roster
from automation.interop.approval_lifecycle import ApprovalIntent, Outcome
from automation.interop.approval_types import Probe
from automation.managed_skills import submission_approval as approval
from automation.managed_skills import submission_cli, submission_transport
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.managed_skills.submission_message import (
    _JSON_LOADS, SubmissionEnvelope, SubmissionIdentity, new_submission_envelope,
    parse_submission_message, render_submission_message,
)
from automation.managed_skills.submission_transport import DiscordSubmissionMessage
from tests.unit.test_managed_submission_publish import _roster
from tests.unit.test_personal_submission_approval import _Transport, _config


def test_commit_when_renderer_changes_retains_posted_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a capability which changes after its first use.
    config = _config(tmp_path, _Transport())
    original = render_submission_message
    rendered: list[str] = []

    def changing(envelope: SubmissionEnvelope) -> str:
        content = original(envelope) + ("X" if rendered else "")
        rendered.append(content)
        return content

    monkeypatch.setattr(approval, "render_submission_message", changing)
    # When: the real lifecycle posts and commits.
    verdict = approval.request_submission_approval(config)
    # Then: the recorded wire version and bytes are precisely what was sent.
    assert verdict.outcome is Outcome.POSTED
    record = _JSON_LOADS(next((config.state_root / "pending").glob("*.json")).read_text())
    assert isinstance(record, dict)
    assert record["content"] == rendered[0]
    assert len(rendered) == 1


@pytest.mark.parametrize("capability", [True, False])
def test_probe_when_selected_version_is_recorded_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: bool,
) -> None:
    # Given: an available or missing optional owner-message import.
    transport = _Transport()
    config = _config(tmp_path, transport)
    if not capability:
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    posted = approval.request_submission_approval(config)
    assert posted.posted is not None
    content = transport.messages[posted.posted.message_id].content
    envelope = parse_submission_message(content)
    gate = approval.PersonalSubmissionGate(config, envelope)
    request, = gate.outstanding(gate.key())
    # When: the real probe compares the remote card, local record and attachments.
    probe = gate.probe(request)
    # Then: the selected version is recorded, accepted and not replaced.
    if capability:
        assert content.splitlines()[-1].startswith("-# [personal-skill-submission-v3] ")
    else:
        assert content.startswith("[personal-skill-submission-v1] ")
    assert probe is Probe.BOUND_PENDING
    record = _JSON_LOADS(gate.path().read_text())
    assert isinstance(record, dict)
    assert record["content"] == content
    assert transport.deleted == []


@pytest.mark.parametrize("change", ["body", "attachment", "canonical-body"])
def test_request_when_new_card_is_tampered_never_reposts(
    tmp_path: Path, change: str,
) -> None:
    # Given: a posted submission whose body or attachment set is altered remotely.
    transport = _Transport()
    config = _config(tmp_path, transport)
    posted = approval.request_submission_approval(config)
    assert posted.posted is not None
    message_id = posted.posted.message_id
    message = transport.messages[message_id]
    content = message.content
    if change == "canonical-body":
        envelope = parse_submission_message(content)
        content = render_submission_message(replace(envelope, nonce="alternate"))
    transport.messages[message_id] = DiscordSubmissionMessage(
        content[:-1] + "X" if change == "body" else content,
        () if change == "attachment" else message.attachment_names,
    )
    # When: the real lifecycle probes the stale request.
    verdict = approval.request_submission_approval(config)
    # Then: neither malformed canonical content nor binding mismatch authorizes replacement.
    assert verdict.outcome is (Outcome.DEFERRED if change == "body" else Outcome.REFUSED)
    assert len(transport.posts) == 1
    assert transport.deleted == []


def test_request_when_v1_exists_after_upgrade_reuses_without_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an existing v1 request, then the capability becomes available.
    transport = _Transport()
    config = _config(tmp_path, transport)
    with monkeypatch.context() as unavailable:
        unavailable.setitem(sys.modules, "automation.interop.owner_message", None)
        posted = approval.request_submission_approval(config)
    assert posted.posted is not None
    legacy = transport.messages[posted.posted.message_id].content
    # When: the same semantic submission is requested with the new renderer installed.
    verdict = approval.request_submission_approval(config)
    # Then: the pending v1 binding and all reactions remain on the original card.
    assert verdict.outcome is Outcome.PENDING
    assert verdict.live is not None and verdict.live.message_id == posted.posted.message_id
    assert transport.posts[0][1] == legacy
    assert len(transport.posts) == 1 and transport.deleted == []


def test_post_when_card_is_oversized_blocks_discord(tmp_path: Path) -> None:
    # Given: a validated artifact whose submission identity cannot fit on a card.
    transport = _Transport()
    config = replace(_config(tmp_path, transport), group_id="x" * 1900)
    envelope = new_submission_envelope(
        SubmissionIdentity(config.group_id, config.submitter), config.artifact, "b" * 32,
    )
    gate = approval.PersonalSubmissionGate(config, envelope)
    intent = ApprovalIntent(gate.key(), envelope.action_hash, gate.binding.channel_id)
    # When / Then: the posting boundary refuses before all Discord posting and reactions.
    with pytest.raises(SubmissionArtifactError, match="1900"):
        _ = gate.post(intent)
    assert transport.posts == []
    assert transport.messages == {}


@pytest.mark.parametrize("mode", ["v3", "fallback", "invalid-metadata"])
def test_cli_when_invoked_runs_packaging_to_wire_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    # Given: real CLI inputs and only the Discord HTTPS byte boundary replaced.
    config = _config(tmp_path, _Transport())
    roster = _roster()
    key = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"a" * 32).decode()
    roster = replace(roster, admin=replace(roster.admin, signing_public_key=f"ssh-ed25519 {key}"))
    roster_path = tmp_path / "roster.yaml"
    _ = roster_path.write_text(render_roster(roster), encoding="utf-8")
    metadata = tmp_path / "release.json"
    _ = metadata.write_text(json.dumps({
        "compatibility": "any", "breaking": False, "changelog": "Test submission.",
    }) if mode != "invalid-metadata" else "{}", encoding="utf-8")
    interop = tmp_path / "interop.json"
    _ = interop.write_text('{"owner_id":"111","deploy_approvals_channel_id":"123"}')
    credential = tmp_path / "credential"
    _ = credential.write_text("test-only", encoding="utf-8")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "gate")
    if mode == "fallback":
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    posted: list[str] = []

    def discord(request: submission_transport._DiscordRequest) -> bytes:
        if request.method == "GET" and request.path == "/channels/123":
            return b'{"id":"123","type":0,"name":"approvals","guild_id":"222"}'
        if request.method == "POST" and request.path == "/channels/123/messages":
            assert request.body is not None
            # Read the real multipart payload_json part, before attachment bytes.
            metadata_bytes = request.body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
            payload = _JSON_LOADS(metadata_bytes.decode("utf-8"))
            assert isinstance(payload, dict)
            attachments, content = payload["attachments"], payload["content"]
            assert isinstance(attachments, list) and len(attachments) == 2
            assert isinstance(content, str)
            posted.append(content)
            return b'{"id":"333"}'
        if request.method == "PUT" and request.path.startswith("/channels/123/messages/333/reactions/"):
            return b""
        raise AssertionError((request.method, request.path))

    monkeypatch.setattr(submission_transport, "_request_bytes", discord)
    monkeypatch.setattr(sys, "argv", [
        "submission_cli", "--personal", "personal-x", "--skill", "managed-x",
        "--personal-repo", str(tmp_path / "personal-x"),
        "--release-metadata", str(metadata), "--roster", str(roster_path),
        "--output-dir", str(tmp_path / "cli-output"), "--state-root", str(config.state_root),
        "--discord-token-file", str(credential),
    ])
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    # When: the actual argparse entry point runs its full submit path.
    result = submission_cli.main()
    # Then: successful cards reach the multipart wire and record; bad input posts nothing.
    if mode == "invalid-metadata":
        assert result == 1
        assert stderr.getvalue().startswith("SUBMISSION-BLOCK:")
        assert posted == []
    else:
        assert result == 0
        assert stdout.getvalue().strip() == "SUBMISSION-STAGED message_id=333"
        content, = posted
        if mode == "fallback":
            assert content.startswith("[personal-skill-submission-v1] ")
        else:
            assert content.splitlines()[-1].startswith("-# [personal-skill-submission-v3] ")
        assert parse_submission_message(content).skill == "managed-x"
        record = _JSON_LOADS(next((config.state_root / "pending").glob("*.json")).read_text())
        assert isinstance(record, dict)
        assert record["content"] == content
