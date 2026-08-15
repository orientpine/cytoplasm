"""EXACTLY ONE live owner-approval message per skill gate key.

Locks the invariants the shared lifecycle façade buys the deploy gate
(``skill-deploy:{skill}``) and the managed-skill publish gate
(``skill-publish:{skill}``): a stored ``message_id`` is never replaced, only
superseded (DELETE before the new POST) or left alone, an owner-decided request
is never destroyed, and an unreadable record refuses instead of posting.

The Discord surface is an in-memory fake with an ordered call log — no mocks, so
the DELETE/POST ordering is asserted on what the gate actually did.
"""
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
    skill_gate_publish,
    skill_gate_request,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_surface import ApprovalKind, ChannelFacts

_DIGEST = "a" * 64
_OTHER_DIGEST = "c" * 64
_MANIFEST_DIGEST = "b" * 64
_OWNER_ID = "111111111111111111"
_CHANNEL_ID = "100000000000000009"
_SKILL = "calendar"
_PUBLISH_SKILL = "managed-x"
_TAG = "managed-x/v1"


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://discord.test", code, "error", Message(), None)


class FakeDiscord:
    """In-memory #approvals channel: real message store plus an ordered call log."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.contents: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self.posted = 0

    def api(self, method: str, path: str, payload: dict[str, str] | None = None) -> object:
        self.calls.append((method, path))
        if method == "POST":
            self.posted += 1
            message_id = f"message-{self.posted}"
            self.contents[message_id] = "" if payload is None else payload["content"]
            return {"id": message_id}
        message_id = path.split("/messages/")[1].split("/")[0].split("?")[0]
        if method == "DELETE":
            if self.contents.pop(message_id, None) is None:
                raise _http_error(404)
            return None
        if "/reactions/" in path:
            emoji = unquote(path.split("/reactions/")[1].split("?")[0])
            users = self.reactions.get((message_id, emoji))
            if users is None:
                raise _http_error(404)
            return [{"id": user, "bot": False} for user in users]
        content = self.contents.get(message_id)
        if content is None:
            raise _http_error(404)
        return {"id": message_id, "content": content}

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]


class _FakeDirectory:
    """What the shared directory answers for this bot — no Discord, no guild scan."""

    def owner_dm(self) -> str:
        raise AssertionError("the skill supply chain must never open a DM (SI-6)")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


def _surface(kind: ApprovalKind) -> skill_gate_surface.SupplyChainSurface:
    return skill_gate_surface.SupplyChainSurface(kind, _OWNER_ID, _FakeDirectory())


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDiscord:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    fake = FakeDiscord()
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(
        skill_gate, "_deploy_bindings", lambda skill: _surface(skill_gate_surface.deploy_kind(skill))
    )
    monkeypatch.setattr(
        skill_gate_publish, "_publish_bindings", lambda: _surface(ApprovalKind.SKILL_PUBLISH)
    )
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    return fake


def _pending_dir(tmp_path: Path) -> Path:
    return tmp_path / "skill-gate" / "pending"


def _record(tmp_path: Path, name: str) -> dict[str, str]:
    raw = (_pending_dir(tmp_path) / f"{name}.json").read_text(encoding="utf-8")
    decoded = json.loads(raw)
    return {str(key): str(value) for key, value in decoded.items()}


def _request_args(*, digest: str = _DIGEST, fresh: bool = False) -> argparse.Namespace:
    return argparse.Namespace(skill=_SKILL, hash=digest, fresh=fresh, json=False)


def _publish_args() -> argparse.Namespace:
    return argparse.Namespace(
        skill=_PUBLISH_SKILL, hash=_DIGEST, manifest_hash=_MANIFEST_DIGEST, tag=_TAG, json=False
    )


def test_same_hash_repeat_when_request_is_live_then_nothing_is_posted_and_record_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: one live deploy request for this skill/digest.
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    first = _record(tmp_path, _SKILL)
    _ = capsys.readouterr()

    # When: the identical request is issued again.
    assert skill_gate.cmd_request(_request_args()) == 0

    # Then: no second message exists and the stored binding — id and nonce — is byte-identical.
    assert fake.posted == 1
    assert "POST" not in fake.methods()[1:]
    assert _record(tmp_path, _SKILL) == first
    assert capsys.readouterr().out == "message-1\n"


def test_different_hash_when_request_is_live_then_delete_precedes_post_and_one_record_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a live deploy request bound to message-1.
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    first = _record(tmp_path, _SKILL)

    # When: the same skill is requested under a different digest.
    assert skill_gate.cmd_request(_request_args(digest=_OTHER_DIGEST)) == 0

    # Then: the live message was deleted BEFORE the replacement was posted — never orphaned.
    methods = fake.methods()
    assert methods.index("DELETE") < methods.index("POST", 1)
    assert "message-1" not in fake.contents
    second = _record(tmp_path, _SKILL)
    assert second["message_id"] != first["message_id"]
    assert second["hash"] == _OTHER_DIGEST
    assert sorted(path.name for path in _pending_dir(tmp_path).glob("*.json")) == [f"{_SKILL}.json"]


def test_fresh_when_request_is_live_then_supersedes_the_old_message_instead_of_orphaning_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a live deploy request the PENDING fast path would otherwise reuse verbatim.
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    first = _record(tmp_path, _SKILL)

    # When: the same digest is requested with --fresh.
    assert skill_gate.cmd_request(_request_args(fresh=True)) == 0

    # Then: --fresh supersedes — the old message is deleted before the new one is posted.
    methods = fake.methods()
    assert methods.index("DELETE") < methods.index("POST", 1)
    assert "message-1" not in fake.contents
    second = _record(tmp_path, _SKILL)
    assert second["message_id"] != first["message_id"]
    assert second["deploy_nonce"] != first["deploy_nonce"]
    assert len(fake.contents) == 1


def test_owner_already_approved_when_content_changed_then_defers_without_deleting_or_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the owner already reacted ✅ to the live request for the first digest.
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    first = _record(tmp_path, _SKILL)
    fake.reactions[("message-1", skill_gate_specs.APPROVE_EMOJI)] = [_OWNER_ID]
    _ = capsys.readouterr()

    # When: a different digest is requested for the same skill.
    result = skill_gate.cmd_request(_request_args(digest=_OTHER_DIGEST))

    # Then: an owner-decided request is never destroyed — nothing deleted, nothing posted.
    captured = capsys.readouterr()
    assert result == skill_gate_request.LIFECYCLE_REFUSAL_EXIT
    assert "reason=owner-decided" in captured.err
    assert captured.out == ""
    assert "DELETE" not in fake.methods()
    assert fake.posted == 1
    assert _record(tmp_path, _SKILL) == first


def test_corrupt_pending_record_when_requested_then_refuses_without_touching_discord(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the pending record for this skill cannot be decoded.
    fake = _install(tmp_path, monkeypatch)
    _pending_dir(tmp_path).mkdir(mode=0o700, parents=True)
    corrupt = _pending_dir(tmp_path) / f"{_SKILL}.json"
    _ = corrupt.write_text("{not json", encoding="utf-8")

    # When: a deploy approval is requested.
    result = skill_gate.cmd_request(_request_args())

    # Then: an unreadable record is never "absent" — the gate refuses and posts nothing.
    captured = capsys.readouterr()
    assert result == skill_gate_request.LIFECYCLE_REFUSAL_EXIT
    assert "reason=store-unreadable" in captured.err
    assert captured.out == ""
    assert fake.calls == []
    assert corrupt.read_text(encoding="utf-8") == "{not json"


def test_publish_repeat_when_release_is_unchanged_then_no_duplicate_and_id_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: one live publish approval request for this managed release.
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate_publish.cmd_publish_request(_publish_args()) == 0
    first = _record(tmp_path, f"publish-{_PUBLISH_SKILL}")
    _ = capsys.readouterr()

    # When: the identical publish request is issued a second time.
    assert skill_gate_publish.cmd_publish_request(_publish_args()) == 0

    # Then: the release metadata authorizes one message only — id and nonce survive.
    assert fake.posted == 1
    assert _record(tmp_path, f"publish-{_PUBLISH_SKILL}") == first
    assert capsys.readouterr().out == "message-1\n"


def test_lifecycle_refusal_exit_code_when_compared_to_rate_limit_then_is_distinct() -> None:
    # Given/When: the gate's terminal exit codes are compared.
    refusal = skill_gate_request.LIFECYCLE_REFUSAL_EXIT

    # Then: a lifecycle refusal is never reported as the weekly auto-proposal rate limit (3).
    assert refusal == 6
    assert refusal not in {0, 1, 2, 3}


def test_legacy_record_without_execution_binding_when_fresh_requested_then_is_superseded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a legacy pending record with no action hash, action, destination, or surface binding.
    fake = _install(tmp_path, monkeypatch)
    assert skill_gate.cmd_request(_request_args()) == 0
    _ = capsys.readouterr()
    legacy = _record(tmp_path, _SKILL)
    _ = (_pending_dir(tmp_path) / f"{_SKILL}.json").write_text(
        json.dumps({name: legacy[name] for name in ("hash", "message_id", "deploy_nonce")}),
        encoding="utf-8",
    )

    # When: --fresh supersedes the unbound request.
    result = skill_gate.cmd_request(_request_args(fresh=True))

    # Then: the old message is deleted before one replacement with the full current binding.
    assert result == 0
    assert ("DELETE", f"/channels/{_CHANNEL_ID}/messages/{legacy['message_id']}") in fake.calls
    assert fake.methods().index("DELETE") < fake.methods().index("POST", 1)
    rebound = _record(tmp_path, _SKILL)
    assert rebound["message_id"] != legacy["message_id"]
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
