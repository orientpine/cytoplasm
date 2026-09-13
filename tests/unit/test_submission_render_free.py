"""Clicked submissions must remain readable without invoking any renderer."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from automation.interop.approval_lifecycle import ApprovalIntent
from automation.interop.approval_types import Probe
from automation.managed_skills import submission_message as wire
from automation.managed_skills.submission_approval import PersonalSubmissionGate
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.managed_skills.submission_source import (
    ApprovedSubmissionConfig, SubmissionEvidence, open_approved_submission,
)
from automation.managed_skills.submission_transport import DiscordUser
from tests.unit.test_managed_submission_publish import _roster
from tests.unit.test_personal_submission_approval import _Transport, _config
from tests.unit.test_submission_message import _STORED_V2, stored_v1


@dataclass(frozen=True, slots=True)
class ClickedSubmission:
    gate: PersonalSubmissionGate
    transport: _Transport
    review: ApprovedSubmissionConfig
    content: str


def clicked_submission(tmp_path: Path, version: int) -> ClickedSubmission:
    transport = _Transport()
    config = _config(tmp_path, transport)
    envelope = wire.new_submission_envelope(
        wire.SubmissionIdentity(config.group_id, config.submitter), config.artifact, "b" * 32,
    )
    gate = PersonalSubmissionGate(config, envelope)
    # Independent archived wire body; real artifact fields replace the synthetic fixture.
    if version == 1:
        content = stored_v1(envelope)
    else:
        archived = wire.parse_submission_message(_STORED_V2)
        content = _STORED_V2
        for old, new in (
            (archived.action_hash, envelope.action_hash),
            (archived.manifest_filename, envelope.manifest_filename),
            (archived.tarball_filename, envelope.tarball_filename),
            (archived.manifest_sha256, envelope.manifest_sha256),
            (archived.skill_sha256, envelope.skill_sha256),
            (archived.source_commit, envelope.source_commit),
            (archived.tarball_sha256, envelope.tarball_sha256),
        ):
            content = content.replace(old, new)
    gate.__dict__["content"] = content
    intent = ApprovalIntent(gate.key(), envelope.action_hash, gate.binding.channel_id)
    posted = gate.post(intent)
    gate.commit(intent, posted, "2000-01-01T00:00:00+00:00")
    transport.reactions[(posted.message_id, "✅")] = (DiscordUser(config.reviewer_id, False),)
    review = ApprovedSubmissionConfig(
        config.artifact, SubmissionEvidence(posted.message_id, envelope.nonce),
        _roster(), config.surface, transport,
    )
    return ClickedSubmission(gate, transport, review, content)


def observable_renderer(
    calls: list[str], name: str, mode: str,
) -> Callable[[wire.SubmissionEnvelope], str]:
    def observed(envelope: wire.SubmissionEnvelope) -> str:
        del envelope
        calls.append(name)
        if mode == "raises":
            raise AssertionError("clicked submission must not render")
        return "different renderer output"
    return observed


@pytest.mark.parametrize("version", [1, 2], ids=["v1", "v2"])
@pytest.mark.parametrize("mode", ["raises", "drift"])
@pytest.mark.parametrize("route", ["parse", "probe", "consume"])
def test_clicked_submission_never_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int, mode: str, route: str,
) -> None:
    clicked = clicked_submission(tmp_path, version)
    gate, transport = clicked.gate, clicked.transport
    record = gate.path().read_bytes()
    messages = dict(transport.messages)
    calls: list[str] = []
    for name in ("_render_v1", "_render_v2", "render_submission_message"):
        monkeypatch.setattr(wire, name, observable_renderer(calls, name, mode))
    if route == "parse":
        assert wire.parse_submission_message(clicked.content) == gate.envelope
    elif route == "probe":
        request, = gate.outstanding(gate.key())
        assert gate.probe(request) is Probe.APPROVED
    else:
        with open_approved_submission(clicked.review) as approved:
            assert approved.manifest == gate.config.artifact.manifest
            assert (approved.source_dir / "SKILL.md").is_file()
    assert calls == []
    assert gate.path().read_bytes() == record
    assert transport.messages == messages
    assert len(transport.posts) == 1 and transport.deleted == []
    print(f"v{version} {mode} {route}: APPROVED/READABLE; renderer calls={calls}; stored/posted bytes unchanged")


@pytest.mark.parametrize("version", [1, 2], ids=["v1", "v2"])
@pytest.mark.parametrize("damage", ["json", "hash", "body", "field", "newline", "length"])
def test_stored_wire_integrity_guard(version: int, damage: str) -> None:
    envelope = wire.parse_submission_message(_STORED_V2)
    content = stored_v1(envelope) if version == 1 else _STORED_V2
    if damage == "json":
        content = content.replace('{"action_hash"', '{ "action_hash"')
    elif damage == "hash":
        content = content.replace(envelope.manifest_sha256, "f" * 64)
    elif damage == "body":
        content = content[:-1] + "X"
    elif damage == "field":
        content = content.replace('"nonce":', '"extra":"x","nonce":')
    elif damage == "newline":
        content = content.replace("\n", "\r\n")
    else:
        content = content.replace(envelope.nonce, "b" * 1900)
    with pytest.raises(SubmissionArtifactError):
        _ = wire.parse_submission_message(content)


@pytest.mark.parametrize("field", ["skill", "group_id", "submitter", "action_hash"])
def test_stored_v2_human_binding_guard(field: str) -> None:
    first, body = _STORED_V2.split("\n", 1)
    envelope = wire.parse_submission_message(_STORED_V2)
    values = {"skill": envelope.skill, "group_id": envelope.group_id,
              "submitter": envelope.submitter, "action_hash": envelope.action_hash}
    replacement = "sha256:" + "f" * 64 if field == "action_hash" else "other"
    with pytest.raises(SubmissionArtifactError):
        _ = wire.parse_submission_message(first + "\n" + body.replace(values[field], replacement))


def test_stored_v2_whitespace_values_remain_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = wire.parse_submission_message(_STORED_V2)
    provisional = replace(envelope, group_id=" group\t a ", submitter="member\n a")
    envelope = replace(provisional, action_hash=wire._semantic_hash(provisional))
    content = wire.render_submission_message(envelope)
    calls: list[str] = []
    for name in ("_render_v1", "_render_v2"):
        monkeypatch.setattr(wire, name, observable_renderer(calls, name, "raises"))
    assert wire.parse_submission_message(content) == envelope
    assert calls == []
