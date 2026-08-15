from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, assert_never, runtime_checkable
from urllib.parse import unquote

import pytest

from automation import (
    peer_attestation,
    skill_gate,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_surface import ChannelFacts


@runtime_checkable
class RefreshModule(Protocol):
    PEER_ATTESTATION_REFRESH_EXIT: int

    def refresh_required(self, args: argparse.Namespace) -> int: ...


skill_gate_refresh = import_module("automation.skill_gate_refresh")
assert isinstance(skill_gate_refresh, RefreshModule)

_DIGEST = "a" * 64
_NONCE = "b" * 32
_OWNER_ID = "111111111111111111"
_AGENT_BOT_ID = "222222222222222222"
_PEER_BOT_ID = "333333333333333333"
_CHANNEL_ID = "100000000000000009"
_SKILL = "wiki"
_MESSAGE_ID = "request-1"
_NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
_PASS_REVIEW = "- review: ✅ PASS (frontmatter/scenario/secret_scan/content_digest 4/4, sha256-bound)"
_REFRESH_EXIT = skill_gate_refresh.PEER_ATTESTATION_REFRESH_EXIT
InvalidState: TypeAlias = Literal[
    "content-changed",
    "cancelled",
    "superseded",
    "withdrawn",
    "nonce-reused",
    "action-hash-changed",
    "action-changed",
    "destination-changed",
]


class _FakeDirectory:
    def owner_dm(self) -> str:
        raise AssertionError("skill deployment must stay on the supply-chain surface")

    def skill_approvals(self) -> str:
        return _CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


class FakeDiscord:
    def __init__(self) -> None:
        self.contents: dict[str, str] = {}
        self.timestamps: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self.attestations: list[dict[str, object]] = []
        self.owner_request_posts = 0

    def api(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        if method == "POST":
            self.owner_request_posts += 1
            self.contents[_MESSAGE_ID] = str((payload or {})["content"])
            self.timestamps[_MESSAGE_ID] = _NOW.isoformat()
            return {"id": _MESSAGE_ID}
        if path.startswith(f"/channels/{_CHANNEL_ID}/messages?after="):
            return self.attestations
        message_id = path.split("/messages/")[1].split("/")[0]
        if "/reactions/" in path:
            emoji = unquote(path.split("/reactions/")[1].split("?")[0])
            return [
                {"id": user_id, "bot": False}
                for user_id in self.reactions.get((message_id, emoji), [])
            ]
        return {
            "author": {"id": _AGENT_BOT_ID, "bot": True},
            "channel_id": _CHANNEL_ID,
            "content": self.contents[message_id],
            "id": message_id,
            "timestamp": self.timestamps[message_id],
        }


def _deploy_bindings(skill: str) -> skill_gate_surface.SupplyChainSurface:
    return skill_gate_surface.SupplyChainSurface(
        skill_gate_surface.deploy_kind(skill), _OWNER_ID, _FakeDirectory()
    )


def _request_args() -> argparse.Namespace:
    return argparse.Namespace(
        skill=_SKILL,
        hash=_DIGEST,
        fresh=False,
        json=False,
        provenance_file="",
    )


def _check_args() -> argparse.Namespace:
    return argparse.Namespace(
        skill=_SKILL,
        hash=_DIGEST,
        message_id=_MESSAGE_ID,
        deploy_nonce=_NONCE,
        injection_file="",
        provenance_file="",
        peer_attest_mode="discord",
    )


def _pending(tmp_path: Path) -> Path:
    return tmp_path / "skill-gate" / "pending" / f"{_SKILL}.json"


def _install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FakeDiscord, dict[str, str]]:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    peers = tmp_path / "peers.yaml"
    _ = peers.write_text(
        "peers:\n"
        "  agent:\n"
        "    account: agent\n"
        f"    bot_user_id: {_AGENT_BOT_ID}\n"
        "  peer:\n"
        "    account: peer\n"
        f"    bot_user_id: {_PEER_BOT_ID}\n",
        encoding="utf-8",
    )
    peers.chmod(0o600)
    monkeypatch.setattr(
        peer_attestation,
        "_trusted_owner_uids",
        lambda: frozenset({peers.stat().st_uid}),
    )
    fake = FakeDiscord()
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "logs" / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "OPS_PEERS_CONFIG", peers)
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _deploy_bindings)
    monkeypatch.setattr(skill_gate, "_api", fake.api)
    monkeypatch.setattr(skill_gate, "_now", lambda: _NOW)
    monkeypatch.setattr(skill_gate, "review_status_line", lambda *_args: _PASS_REVIEW)
    monkeypatch.setattr(skill_gate.secrets, "token_hex", lambda _length: _NONCE)
    assert skill_gate.cmd_request(_request_args()) == 0
    fake.timestamps[_MESSAGE_ID] = (_NOW - timedelta(minutes=45)).isoformat()
    fake.reactions[(_MESSAGE_ID, skill_gate_specs.APPROVE_EMOJI)] = [_OWNER_ID]
    fake.attestations = [
        {
            "author": {"id": _PEER_BOT_ID, "bot": True},
            "channel_id": _CHANNEL_ID,
            "content": peer_attestation.format_attestation(
                _NONCE, _SKILL, _DIGEST, "PASS"
            ),
            "message_reference": {
                "channel_id": _CHANNEL_ID,
                "message_id": _MESSAGE_ID,
            },
            "timestamp": (_NOW - timedelta(minutes=44)).isoformat(),
        }
    ]
    record = json.loads(_pending(tmp_path).read_text(encoding="utf-8"))
    return fake, {str(key): str(value) for key, value in record.items()}


def test_check_when_only_peer_attestation_expired_then_requests_refresh_without_owner_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the unchanged request remains owner-approved while only its peer verdict is stale.
    fake, owner_binding = _install(tmp_path, monkeypatch)

    # When: deploy execution checks both authorizers at a deterministic time.
    result = skill_gate_refresh.refresh_required(_check_args())

    # Then: it authorizes peer re-verification without posting or logging a new owner event.
    assert result == _REFRESH_EXIT
    assert fake.owner_request_posts == 1
    assert json.loads(_pending(tmp_path).read_text(encoding="utf-8")) == owner_binding
    assert not skill_gate.APPROVAL_LOG.exists()


@pytest.mark.parametrize(
    "invalid_state",
    (
        "content-changed",
        "cancelled",
        "superseded",
        "withdrawn",
        "nonce-reused",
        "action-hash-changed",
        "action-changed",
        "destination-changed",
    ),
)
def test_check_when_owner_or_binding_is_invalid_then_never_authorizes_refresh(
    invalid_state: InvalidState,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one stale peer verdict, with exactly one owner/binding invariant broken.
    fake, _owner_binding = _install(tmp_path, monkeypatch)
    record = json.loads(_pending(tmp_path).read_text(encoding="utf-8"))
    match invalid_state:
        case "content-changed":
            fake.contents[_MESSAGE_ID] += "\nchanged"
        case "cancelled":
            fake.reactions[(_MESSAGE_ID, skill_gate_specs.CANCEL_EMOJI)] = [_OWNER_ID]
        case "superseded":
            record["message_id"] = "request-2"
            _ = _pending(tmp_path).write_text(json.dumps(record), encoding="utf-8")
        case "withdrawn":
            fake.reactions[(_MESSAGE_ID, skill_gate_specs.APPROVE_EMOJI)] = []
        case "nonce-reused":
            duplicate = _pending(tmp_path).with_name("other.json")
            _ = duplicate.write_text(json.dumps(record), encoding="utf-8")
        case "action-hash-changed":
            record["action_hash"] = "0" * 64
            _ = _pending(tmp_path).write_text(json.dumps(record), encoding="utf-8")
        case "action-changed":
            record["approval_action"] = "skill.publish"
            _ = _pending(tmp_path).write_text(json.dumps(record), encoding="utf-8")
        case "destination-changed":
            record["approval_destination"] = "skill:other"
            _ = _pending(tmp_path).write_text(json.dumps(record), encoding="utf-8")
        case unreachable:
            assert_never(unreachable)

    # When: deploy execution evaluates refresh eligibility.
    result = skill_gate_refresh.refresh_required(_check_args())

    # Then: no invalid owner state can trigger automatic peer attestation.
    assert result == 1
    assert result != _REFRESH_EXIT
    assert fake.owner_request_posts == 1
    assert not skill_gate.APPROVAL_LOG.exists()
