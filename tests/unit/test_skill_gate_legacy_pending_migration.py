from __future__ import annotations

import argparse
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

from automation import (
    skill_gate,
    skill_gate_approval,
    skill_gate_publish,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_lifecycle import ApprovalRequest, Probe
from automation.interop.approval_surface import ApprovalKind, ChannelFacts

_DIGEST = "a" * 64
_MANIFEST_DIGEST = "b" * 64
_LEGACY_NONCE = "c" * 32
_OWNER_ID = "111111111111111111"
_CHANNEL_ID = "100000000000000009"
_SKILL = "doctype"
_PUBLISH_SKILL = "managed-legacy"
_TAG = "managed-legacy/v1"


def _not_found() -> HTTPError:
    return HTTPError("https://discord.test", 404, "not found", Message(), None)


class _FakeDiscord:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.contents: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self.posted = 0

    def api(self, method: str, path: str, payload: dict[str, str] | None = None) -> object:
        self.calls.append((method, path))
        if method == "POST":
            self.posted += 1
            message_id = f"fresh-message-{self.posted}"
            self.contents[message_id] = "" if payload is None else payload["content"]
            return {"id": message_id}
        message_id = path.split("/messages/")[1].split("/")[0].split("?")[0]
        if method == "DELETE":
            if self.contents.pop(message_id, None) is None:
                raise _not_found()
            return None
        if "/reactions/" in path:
            emoji = unquote(path.split("/reactions/")[1].split("?")[0])
            users = self.reactions.get((message_id, emoji))
            if users is None:
                raise _not_found()
            return [{"id": user_id, "bot": False} for user_id in users]
        content = self.contents.get(message_id)
        if content is None:
            raise _not_found()
        return {"id": message_id, "content": content}


class _FakeDirectory:
    def owner_dm(self) -> str:
        raise AssertionError("skill supply-chain approvals must not use owner DM")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


def _surface(kind: ApprovalKind) -> skill_gate_surface.SupplyChainSurface:
    return skill_gate_surface.SupplyChainSurface(kind, _OWNER_ID, _FakeDirectory())


def _install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[_FakeDiscord, Path]:
    gate_dir = tmp_path / "skill-gate"
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    fake = _FakeDiscord()
    monkeypatch.setattr(skill_gate, "GATE_DIR", gate_dir)
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    monkeypatch.setattr(
        skill_gate,
        "_deploy_bindings",
        lambda skill: _surface(skill_gate_surface.deploy_kind(skill)),
    )
    monkeypatch.setattr(
        skill_gate_publish,
        "_publish_bindings",
        lambda: _surface(ApprovalKind.SKILL_PUBLISH),
    )
    return fake, gate_dir


def _deploy_args() -> argparse.Namespace:
    return argparse.Namespace(skill=_SKILL, hash=_DIGEST, fresh=False, json=False)


def _legacy_deploy(
    gate_dir: Path, fake: _FakeDiscord, *, includes_nonce: bool
) -> dict[str, str]:
    gate = skill_gate._deploy_gate(
        argparse.Namespace(
            skill=_SKILL,
            hash=_DIGEST,
            deploy_nonce=_LEGACY_NONCE,
            provenance_file="",
        )
    )
    content = gate.spec.render()
    record = {"hash": _DIGEST, "message_id": "legacy-message"}
    if includes_nonce:
        record["deploy_nonce"] = _LEGACY_NONCE
    else:
        content = content.replace(f"- deploy_nonce: `{_LEGACY_NONCE}`\n", "")
    pending = gate_dir / "pending" / f"{_SKILL}.json"
    pending.parent.mkdir(mode=0o700, parents=True)
    _ = pending.write_text(json.dumps(record), encoding="utf-8")
    fake.contents[record["message_id"]] = content
    return record


@pytest.mark.parametrize("includes_nonce", (True, False), ids=("nonce-present", "nonce-absent"))
def test_owner_request_when_deploy_pending_predates_binding_schema_then_supersedes_and_posts(
    includes_nonce: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a readable legacy record with only the fields deployed before action binding.
    fake, gate_dir = _install(tmp_path, monkeypatch)
    legacy = _legacy_deploy(gate_dir, fake, includes_nonce=includes_nonce)
    gate = skill_gate._deploy_gate(_deploy_args())

    # When: the owner approval request is issued through the real deploy request surface.
    outstanding = gate.outstanding(gate.spec.key())
    result = skill_gate.cmd_request(_deploy_args())

    # Then: the legacy message is superseded before one fresh bound request is posted.
    captured = capsys.readouterr()
    assert len(outstanding) == 1
    assert outstanding[0].action_hash == ""
    assert result == 0, captured.err
    assert "store-unreadable" not in captured.err
    methods = [method for method, _ in fake.calls]
    assert methods.count("DELETE") == 1
    assert methods.count("POST") == 1
    assert methods.index("DELETE") < methods.index("POST")
    assert legacy["message_id"] not in fake.contents
    rebound = json.loads((gate_dir / "pending" / f"{_SKILL}.json").read_text(encoding="utf-8"))
    assert rebound["message_id"] != legacy["message_id"]
    assert rebound["action_hash"]
    assert set(rebound) == {
        "action_hash",
        "approval_action",
        "approval_destination",
        "channel_id",
        "deploy_nonce",
        "hash",
        "kind",
        "message_id",
        "policy_version",
        "surface",
    }


def test_owner_request_when_publish_pending_predates_binding_schema_then_supersedes_and_posts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a publish record from before action/action-hash/destination were persisted.
    fake, gate_dir = _install(tmp_path, monkeypatch)
    args = argparse.Namespace(
        skill=_PUBLISH_SKILL,
        hash=_DIGEST,
        manifest_hash=_MANIFEST_DIGEST,
        tag=_TAG,
        json=False,
    )
    gate = skill_gate_publish._publish_gate(args)
    assert isinstance(gate.spec, skill_gate_specs.PublishSpec)
    legacy = {
        "hash": _DIGEST,
        "manifest_hash": _MANIFEST_DIGEST,
        "message_id": "legacy-publish-message",
        "publish_nonce": gate.spec.publish_nonce,
        "tag": _TAG,
    }
    pending = gate_dir / "pending" / f"publish-{_PUBLISH_SKILL}.json"
    pending.parent.mkdir(mode=0o700, parents=True)
    _ = pending.write_text(json.dumps(legacy), encoding="utf-8")
    fake.contents[legacy["message_id"]] = gate.spec.render()

    # When: the same release requests a new owner approval.
    result = skill_gate_publish.cmd_publish_request(args)

    # Then: the old message is deleted before a complete fresh request is posted.
    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert "store-unreadable" not in captured.err
    methods = [method for method, _ in fake.calls]
    assert methods.index("DELETE") < methods.index("POST")
    rebound = json.loads(pending.read_text(encoding="utf-8"))
    assert rebound["message_id"] != legacy["message_id"]
    assert rebound["action_hash"]


@pytest.mark.parametrize("includes_nonce", (True, False), ids=("nonce-present", "nonce-absent"))
def test_valid_approval_when_deploy_pending_is_legacy_then_fails_closed(
    includes_nonce: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: even the owner reacted to a legacy message that has no execution binding.
    fake, gate_dir = _install(tmp_path, monkeypatch)
    legacy = _legacy_deploy(gate_dir, fake, includes_nonce=includes_nonce)
    fake.reactions[(legacy["message_id"], skill_gate_specs.APPROVE_EMOJI)] = [_OWNER_ID]
    gate = skill_gate._deploy_gate(
        argparse.Namespace(
            skill=_SKILL,
            hash=_DIGEST,
            deploy_nonce=legacy.get("deploy_nonce", ""),
            provenance_file="",
        )
    )
    execution = skill_gate_approval.ApprovalExecution(
        request=ApprovalRequest(
            key=gate.spec.key(),
            action_hash=gate.spec.action_hash(),
            message_id=legacy["message_id"],
            channel_id=_CHANNEL_ID,
            created_at="",
        ),
        nonce=legacy.get("deploy_nonce", ""),
        action=skill_gate_specs.DEPLOY_ACTION,
        destination=f"skill:{_SKILL}",
    )

    # When: approval validity is evaluated against the incomplete persisted binding.
    approved = gate.valid_approval(execution, tmp_path / "approvals.jsonl")

    # Then: legacy compatibility never turns that reaction into authorization.
    assert not approved


def test_probe_when_action_hash_is_empty_then_owner_reaction_is_still_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a migration-only record whose existing message carries the owner's approval.
    fake, gate_dir = _install(tmp_path, monkeypatch)
    legacy = _legacy_deploy(gate_dir, fake, includes_nonce=True)
    fake.reactions[(legacy["message_id"], skill_gate_specs.APPROVE_EMOJI)] = [_OWNER_ID]
    gate = skill_gate._deploy_gate(_deploy_args())

    # When: the lifecycle gate probes the legacy message.
    result = gate.probe(gate.outstanding(gate.spec.key())[0])

    # Then: empty action_hash does not bypass the shared owner reaction judgment.
    assert result is Probe.APPROVED
