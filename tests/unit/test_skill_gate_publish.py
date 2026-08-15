from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from automation import skill_gate, skill_gate_publish, skill_gate_specs, skill_gate_surface
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ChannelFacts,
)
from automation.interop.injection_adapter import InboundEvent, sign_event

_DIGEST = "a" * 64
_MANIFEST_DIGEST = "b" * 64
_NONCE = "1" * 32
_OWNER_ID = "111"
_CHANNEL_ID = "123"
_SKILL = "managed-x"
_TAG = "managed-x/v1"
_TIMESTAMP = "2026-07-24T00:00:00Z"
_PUBLISH_APPROVAL_TEXT = f"PUBLISH skill:{_SKILL} sha256:{_DIGEST} msg:message-1"
_APPROVE_LINE = "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"


class _FakeDirectory:
    """What the shared directory answers for this bot — no Discord, no guild scan."""

    def owner_dm(self) -> str:
        raise AssertionError("the skill supply chain must never open a DM (SI-6)")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


def _bindings() -> skill_gate_surface.SupplyChainSurface:
    """The publish gate's declared surface, stubbed at the directory boundary."""
    return skill_gate_surface.SupplyChainSurface(
        ApprovalKind.SKILL_PUBLISH, _OWNER_ID, _FakeDirectory()
    )


def _deploy_bindings(skill: str) -> skill_gate_surface.SupplyChainSurface:
    """The DEPLOY gate's declared surface, for the request paths this suite reuses."""
    return skill_gate_surface.SupplyChainSurface(
        skill_gate_surface.deploy_kind(skill), _OWNER_ID, _FakeDirectory()
    )


def _publish_content(
    *,
    skill: str = _SKILL,
    digest: str = _DIGEST,
    manifest: str = _MANIFEST_DIGEST,
    tag: str = _TAG,
    nonce: str = _NONCE,
) -> str:
    return (
        "[skill-publish] 발행 승인 요청\n"
        f"- skill: `{skill}`\n"
        f"- sha256: `{digest}`\n"
        f"- manifest_sha256: `{manifest}`\n"
        f"- tag: `{tag}`\n"
        f"- publish_nonce: `{nonce}`\n"
        f"{_APPROVE_LINE}"
    )


def _run_publish_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    posted: list[str] = []

    def discord_api(method: str, path: str, payload: dict[str, str] | None = None) -> dict[str, str]:
        assert method == "POST"
        assert path == f"/channels/{_CHANNEL_ID}/messages"
        assert payload is not None
        posted.append(payload["content"])
        return {"id": "message-1"}

    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate_publish, "_publish_bindings", _bindings)
    monkeypatch.setattr(skill_gate, "_api", discord_api)

    args = argparse.Namespace(skill=_SKILL, hash=_DIGEST, manifest_hash=_MANIFEST_DIGEST, tag=_TAG, json=False)
    assert skill_gate_publish.cmd_publish_request(args) == 0
    return posted[0]


def _check_args(injection_file: str = "") -> argparse.Namespace:
    return argparse.Namespace(
        skill=_SKILL,
        hash=_DIGEST,
        manifest_hash=_MANIFEST_DIGEST,
        tag=_TAG,
        publish_nonce=_NONCE,
        message_id="message-1",
        injection_file=injection_file,
    )


def _configure_publish_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str | None,
    reactions: list[dict[str, object]],
) -> None:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")

    def discord_api(method: str, path: str, payload: dict[str, object] | None = None) -> object:
        assert method == "GET"
        assert payload is None
        if path == f"/channels/{_CHANNEL_ID}/messages/message-1":
            return {} if content is None else {"content": content}
        if "/reactions/" in path:
            return reactions
        raise AssertionError(path)

    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "approvals.jsonl")
    monkeypatch.setattr(skill_gate_publish, "_publish_bindings", _bindings)
    monkeypatch.setattr(skill_gate, "_api", discord_api)
    monkeypatch.setattr(skill_gate, "_utc_now", lambda: _TIMESTAMP)


def test_publish_request_when_posted_then_message_binds_all_release_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a mocked #approvals channel capturing the posted content.
    # When: a publish approval is requested for a managed release.
    content = _run_publish_request(tmp_path, monkeypatch)

    # Then: the message opens with the publish prefix and binds every release field plus a fresh nonce.
    assert content.startswith("[skill-publish] 발행 승인 요청\n")
    assert f"- skill: `{_SKILL}`\n" in content
    assert f"- sha256: `{_DIGEST}`\n" in content
    assert f"- manifest_sha256: `{_MANIFEST_DIGEST}`\n" in content
    assert f"- tag: `{_TAG}`\n" in content
    assert re.search(r"- publish_nonce: `[0-9a-f]{32}`\n", content) is not None
    assert _APPROVE_LINE in content


def test_publish_request_when_posted_then_pending_record_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given/When: a publish approval request is posted.
    content = _run_publish_request(tmp_path, monkeypatch)

    # Then: the pending record binds the same nonce that was posted.
    match = skill_gate_publish._PUBLISH_BINDING.match(content)
    assert match is not None
    state = json.loads((tmp_path / "skill-gate" / "pending" / f"publish-{_SKILL}.json").read_text(encoding="utf-8"))
    assert state == {
        "action_hash": skill_gate_specs._hash(
            "skill-publish", _SKILL, _DIGEST, _MANIFEST_DIGEST, _TAG
        ),
        "approval_action": "skill.publish",
        "approval_destination": f"skill:{_SKILL}",
        "channel_id": _CHANNEL_ID,
        "hash": _DIGEST,
        "kind": "skill-publish",
        "manifest_hash": _MANIFEST_DIGEST,
        "message_id": "message-1",
        "policy_version": str(POLICY_VERSION),
        "publish_nonce": match.group("nonce"),
        "surface": "skill-approvals",
        "tag": _TAG,
    }


def test_publish_binding_when_content_is_exact_then_captures_five_named_groups() -> None:
    # Given: the exact publish approval-request content.
    content = _publish_content()

    # When: the publish binding regex parses it.
    match = skill_gate_publish._PUBLISH_BINDING.match(content)

    # Then: all five protocol fields are captured via named groups.
    assert match is not None
    assert match.groupdict() == {
        "skill": _SKILL,
        "digest": _DIGEST,
        "manifest": _MANIFEST_DIGEST,
        "tag": _TAG,
        "nonce": _NONCE,
    }


def test_publish_binding_when_posted_request_is_matched_then_prefix_binds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given/When: the real posted request content is matched by the binding.
    match = skill_gate_publish._PUBLISH_BINDING.match(_run_publish_request(tmp_path, monkeypatch))

    # Then: the request output and the binding regex agree on every bound field.
    assert match is not None
    assert match.group("skill") == _SKILL
    assert match.group("digest") == _DIGEST
    assert match.group("manifest") == _MANIFEST_DIGEST
    assert match.group("tag") == _TAG


def test_publish_check_when_owner_reaction_then_approves_and_logs_publish_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a fully bound publish request message with an owner ✅ reaction.
    _configure_publish_check(
        tmp_path, monkeypatch, content=_publish_content(), reactions=[{"id": _OWNER_ID, "bot": False}]
    )

    # When: the publish check runs.
    result = skill_gate_publish.cmd_publish_check(_check_args())

    # Then: it approves and appends a skill.publish record carrying manifest digest + tag.
    assert result == 0
    record = json.loads((tmp_path / "approvals.jsonl").read_text(encoding="utf-8"))
    assert set(record) == {"action", "approval", "hash", "payload", "result", "target_id", "timestamp"}
    assert record["action"] == "skill.publish"
    assert record["approval"] == {"channel": "approvals", "message_id": "message-1", "method": "manual_reaction"}
    assert record["payload"] == {"manifest_sha256": _MANIFEST_DIGEST, "skill_sha256": _DIGEST, "tag": _TAG}
    assert record["result"] == {"status": "approved"}
    assert record["target_id"] == f"skill:{_SKILL}"
    assert record["timestamp"] == _TIMESTAMP
    payload = {
        "action": "skill.publish",
        "approval": {"channel": "approvals", "message_id": "message-1", "method": "manual_reaction"},
        "payload": {"manifest_sha256": _MANIFEST_DIGEST, "skill_sha256": _DIGEST, "tag": _TAG},
        "target_id": f"skill:{_SKILL}",
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert record["hash"] == "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_publish_check_when_reactor_is_non_owner_then_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a bound request whose only ✅ reaction comes from a non-owner user.
    _configure_publish_check(
        tmp_path, monkeypatch, content=_publish_content(), reactions=[{"id": "999", "bot": False}]
    )

    # When: the publish check runs.
    result = skill_gate_publish.cmd_publish_check(_check_args())

    # Then: the reaction is ignored and no approval is recorded.
    assert result == 1
    assert not (tmp_path / "approvals.jsonl").exists()


def test_publish_check_when_reactor_is_owner_flagged_bot_then_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a bound request where the owner id reacts but as a bot account.
    _configure_publish_check(
        tmp_path, monkeypatch, content=_publish_content(), reactions=[{"id": _OWNER_ID, "bot": True}]
    )

    # When: the publish check runs.
    result = skill_gate_publish.cmd_publish_check(_check_args())

    # Then: bot reactions never approve.
    assert result == 1
    assert not (tmp_path / "approvals.jsonl").exists()


@pytest.mark.parametrize(
    "content",
    (
        None,
        _publish_content(skill="other-skill"),
        _publish_content(digest="c" * 64),
        _publish_content(manifest="d" * 64),
        _publish_content(tag="managed-x/v2"),
        _publish_content(nonce="2" * 32),
    ),
    ids=("missing-content", "wrong-skill", "wrong-digest", "wrong-manifest", "wrong-tag", "wrong-nonce"),
)
def test_publish_check_when_any_bound_field_is_missing_or_mismatched_then_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None
) -> None:
    # Given: an owner ✅ reaction on a message whose binding is absent or mismatched in one field.
    _configure_publish_check(tmp_path, monkeypatch, content=content, reactions=[{"id": _OWNER_ID, "bot": False}])

    # When: the publish check runs.
    result = skill_gate_publish.cmd_publish_check(_check_args())

    # Then: every ambiguity fails closed — no approval, no log record.
    assert result == 1
    assert not (tmp_path / "approvals.jsonl").exists()


def test_publish_check_when_injection_without_e2e_mode_then_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an injection file argument outside E2E mode.
    _configure_publish_check(tmp_path, monkeypatch, content=_publish_content(), reactions=[])
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)

    # When: the publish check runs with an injection file.
    result = skill_gate_publish.cmd_publish_check(_check_args(str(tmp_path / "injection.json")))

    # Then: injected approvals are refused without E2E_TEST_MODE=1.
    assert result == 1
    assert not (tmp_path / "approvals.jsonl").exists()


def test_publish_check_when_e2e_mode_without_injection_file_then_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: E2E_TEST_MODE set but no injection file provided.
    _configure_publish_check(tmp_path, monkeypatch, content=_publish_content(), reactions=[])
    monkeypatch.setenv("E2E_TEST_MODE", "1")

    # When: the publish check runs without --injection-file.
    result = skill_gate_publish.cmd_publish_check(_check_args())

    # Then: the ambiguous mode is fatal (exit 2), mirroring the deploy gate.
    assert result == 2
    assert not (tmp_path / "approvals.jsonl").exists()


def _write_injection(tmp_path: Path, secret: str, text: str) -> Path:
    event = InboundEvent("event-1", _OWNER_ID, _CHANNEL_ID, text)
    injection = tmp_path / "injection.json"
    _ = injection.write_text(
        json.dumps({"event": asdict(event), "signature": sign_event(event, secret.encode("utf-8"))}),
        encoding="utf-8",
    )
    return injection


def test_publish_check_when_signed_owner_injection_valid_then_approves_via_e2e_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a bound request plus a signed owner injection carrying the publish approval text.
    _configure_publish_check(tmp_path, monkeypatch, content=_publish_content(), reactions=[])
    secret = "test-e2e-secret"
    injection = _write_injection(tmp_path, secret, _PUBLISH_APPROVAL_TEXT)
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("INTEROP_E2E_SECRET", secret)

    # When: the publish check consumes the injection.
    result = skill_gate_publish.cmd_publish_check(_check_args(str(injection)))

    # Then: the E2E path approves and logs the publish action with the injection method.
    assert result == 0
    record = json.loads((tmp_path / "approvals.jsonl").read_text(encoding="utf-8"))
    assert record["action"] == "skill.publish"
    assert record["approval"]["method"] == "signed_injection_e2e"
    assert record["payload"] == {"manifest_sha256": _MANIFEST_DIGEST, "skill_sha256": _DIGEST, "tag": _TAG}


def test_publish_check_when_injection_carries_deploy_approval_text_then_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a signed owner injection carrying the DEPLOY approval text instead of the publish one.
    _configure_publish_check(tmp_path, monkeypatch, content=_publish_content(), reactions=[])
    secret = "test-e2e-secret"
    injection = _write_injection(tmp_path, secret, f"APPROVE skill:{_SKILL} sha256:{_DIGEST} msg:message-1")
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("INTEROP_E2E_SECRET", secret)

    # When: the publish check consumes the cross-protocol injection.
    result = skill_gate_publish.cmd_publish_check(_check_args(str(injection)))

    # Then: a deploy approval never doubles as a publish approval.
    assert result == 1
    assert not (tmp_path / "approvals.jsonl").exists()


def test_publish_module_when_inspected_then_defines_no_polling_loop() -> None:
    # Given: the publish helper module source (single-gate reuse — no new watcher).
    source = Path(skill_gate_publish.__file__).read_text(encoding="utf-8")

    # When/Then: no polling constructs exist in the module.
    assert "while True" not in source
    assert "time.sleep" not in source
    assert "import time" not in source


def test_request_when_provenance_file_given_then_lines_follow_binding_and_binding_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a provenance JSON file for a managed release.
    provenance = tmp_path / "provenance.json"
    _ = provenance.write_text(
        json.dumps(
            {
                "publisher": "agent-cha-bot",
                "tag": _TAG,
                "release_sequence": 1,
                "manifest_sha256": _MANIFEST_DIGEST,
            }
        ),
        encoding="utf-8",
    )
    posted: list[str] = []

    def discord_api(_method: str, _path: str, payload: dict[str, str] | None = None) -> dict[str, str]:
        assert payload is not None
        posted.append(payload["content"])
        return {"id": "message-1"}

    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _deploy_bindings)
    monkeypatch.setattr(skill_gate, "_api", discord_api)

    # When: a deploy approval request is posted with --provenance-file.
    args = argparse.Namespace(
        skill="calendar", hash=_DIGEST, fresh=False, json=False, provenance_file=str(provenance)
    )
    assert skill_gate.cmd_request(args) == 0
    content = posted[0]

    # Then: masked provenance lines trail the request and the prefix binding still matches.
    expected = (
        f"- provenance: publisher …-bot / tag `{_TAG}` / sequence 1"
        f" / manifest-sha256 `{_MANIFEST_DIGEST}`"
    )
    assert expected in content
    assert content.index(_APPROVE_LINE) < content.index("- provenance:")
    match = skill_gate._REQUEST_BINDING.match(content)
    assert match is not None
    assert match.group("skill") == "calendar"
    assert match.group("digest") == _DIGEST



class _BlockSkillGatePublish:
    """Meta-path finder that makes automation.skill_gate_publish unimportable (staged-agent shape)."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001, ANN201
        if fullname == "automation.skill_gate_publish":
            raise ModuleNotFoundError(f"No module named {fullname!r}")
        return None


def _make_unimportable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "automation.skill_gate_publish", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockSkillGatePublish(), *sys.meta_path])


def test_deploy_gate_registers_without_skill_gate_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_unimportable(monkeypatch)
    dispatched: list[str] = []
    for name in ("cmd_request", "cmd_check", "cmd_sign"):
        monkeypatch.setattr(skill_gate, name, (lambda label: lambda args: dispatched.append(label) or 0)(name.removeprefix("cmd_")))
    invocations = (
        ["skill_gate.py", "request", "--skill", "mail", "--hash", _DIGEST],
        ["skill_gate.py", "check", "--skill", "mail", "--hash", _DIGEST, "--message-id", "m", "--deploy-nonce", _NONCE],
        ["skill_gate.py", "sign", "--skill", "mail", "--hash", _DIGEST, "--message-id", "m", "--out", "/tmp/x"],
    )
    for argv in invocations:
        monkeypatch.setattr(sys, "argv", argv)
        assert skill_gate.main() == 0
    assert dispatched == ["request", "check", "sign"]


def test_publish_subcommand_absent_without_skill_gate_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_unimportable(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["skill_gate.py", "publish-request", "--skill", _SKILL, "--hash", _DIGEST, "--manifest-hash", _MANIFEST_DIGEST, "--tag", _TAG],
    )
    with pytest.raises(SystemExit):
        skill_gate.main()


def test_publish_subcommands_registered_when_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatched: list[str] = []
    monkeypatch.setattr(skill_gate_publish, "cmd_publish_request", lambda args: dispatched.append("publish-request") or 0)
    monkeypatch.setattr(
        sys, "argv",
        ["skill_gate.py", "publish-request", "--skill", _SKILL, "--hash", _DIGEST, "--manifest-hash", _MANIFEST_DIGEST, "--tag", _TAG],
    )
    assert skill_gate.main() == 0
    assert dispatched == ["publish-request"]


def test_provenance_lines_moved_to_skill_gate(tmp_path: Path) -> None:
    assert hasattr(skill_gate, "provenance_lines")
    assert not hasattr(skill_gate_publish, "provenance_lines")
    path = tmp_path / "provenance.json"
    path.write_text(
        json.dumps({"publisher": "publisher-cha-9876", "tag": _TAG, "release_sequence": 1, "manifest_sha256": _MANIFEST_DIGEST}),
        encoding="utf-8",
    )
    line = skill_gate.provenance_lines(path)
    assert line.startswith("\n- provenance: publisher \u2026")
    assert "publisher-cha-9876" not in line
    assert _TAG in line
    assert _MANIFEST_DIGEST in line


def test_cmd_request_appends_provenance_after_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[str] = []
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _deploy_bindings)
    monkeypatch.setattr(skill_gate, "review_status_line", lambda *a, **k: "- review: PASS")
    monkeypatch.setattr(skill_gate, "_api", lambda method, path, payload=None: posted.append(payload["content"]) or {"id": "message-1"})
    provenance = tmp_path / "provenance.json"
    provenance.write_text(
        json.dumps({"publisher": _OWNER_ID, "tag": _TAG, "release_sequence": 2, "manifest_sha256": _MANIFEST_DIGEST}),
        encoding="utf-8",
    )
    args = argparse.Namespace(skill=_SKILL, hash=_DIGEST, fresh=False, json=False, provenance_file=str(provenance))
    assert skill_gate.cmd_request(args) == 0
    content = posted[0]
    assert content.startswith("[skill-deploy] \uc2b9\uc778 \uc694\uccad\n")
    assert "\n- provenance:" in content
    assert content.index("- provenance:") > content.index("- deploy_nonce:")

def test_new_publish_record_persists_every_binding_field() -> None:
    # Given: the binding a brand-new publish approval resolved to.
    binding = ApprovalBinding(
        ApprovalKind.SKILL_PUBLISH, ApprovalSurface.SKILL_APPROVALS, _CHANNEL_ID, POLICY_VERSION
    )
    spec = skill_gate_specs.PublishSpec(
        skill=_SKILL,
        digest=_DIGEST,
        manifest_hash=_MANIFEST_DIGEST,
        tag=_TAG,
        publish_nonce=_NONCE,
        binding=skill_gate_publish._PUBLISH_BINDING,
    )

    # When: the spec builds the pending record that ✅ will be bound to.
    record = spec.new_record("message-1", binding)

    # Then: the record replays the binding without re-resolving anything (SI-1).
    assert {name: record[name] for name in ("channel_id", "kind", "policy_version", "surface")} == {
        "channel_id": _CHANNEL_ID,
        "kind": "skill-publish",
        "policy_version": str(POLICY_VERSION),
        "surface": "skill-approvals",
    }
