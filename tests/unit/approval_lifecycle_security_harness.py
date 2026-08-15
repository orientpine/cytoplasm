from __future__ import annotations

import argparse
import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, TypeAlias
from urllib.parse import unquote

import pytest

from automation import (
    peer_attest,
    peer_attestation,
    skill_gate,
    skill_gate_refresh,
    skill_gate_specs,
    skill_gate_surface,
)
from automation.interop.approval_surface import ChannelFacts
from automation.skill_review import skill_digest

JsonValue: TypeAlias = (
    str | int | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
)

OWNER_ID: Final = "111111111111111111"
AGENT_BOT_ID: Final = "222222222222222222"
PEER_BOT_ID: Final = "333333333333333333"
CHANNEL_ID: Final = "100000000000000009"
SKILL: Final = "wiki"
MESSAGE_ID: Final = "request-1"
NONCE: Final = "b" * 32
START: Final = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
PASS_REVIEW: Final = (
    "- review: ✅ PASS (frontmatter/scenario/secret_scan/content_digest 4/4, sha256-bound)"
)


class FakeClock:
    """Mutable deterministic clock used to move the whole lifecycle without sleeping."""

    def __init__(self) -> None:
        self.current: datetime = START

    def advance(self, elapsed: timedelta) -> None:
        self.current += elapsed


class FakeDirectory:
    def owner_dm(self) -> str:
        raise AssertionError("skill deploy approval cannot move to owner DM")

    def skill_approvals(self) -> str:
        return CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


class FakeDiscord:
    """Mutable in-memory Discord boundary with observable owner, peer, and mount counts."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock: FakeClock = clock
        self.contents: dict[str, str] = {}
        self.timestamps: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self.attestations: list[dict[str, JsonValue]] = []
        self.owner_request_posts: int = 0
        self.peer_refresh_posts: int = 0
        self.mounts: int = 0

    def api(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        if method == "POST":
            assert payload is not None
            content = payload["content"]
            assert isinstance(content, str)
            self.owner_request_posts += 1
            self.contents[MESSAGE_ID] = content
            self.timestamps[MESSAGE_ID] = self.clock.current.isoformat()
            return {"id": MESSAGE_ID}
        if path.startswith(f"/channels/{CHANNEL_ID}/messages?after="):
            return self.attestations
        message_id = path.split("/messages/")[1].split("/")[0]
        if "/reactions/" in path:
            emoji = unquote(path.split("/reactions/")[1].split("?")[0])
            return [
                {"id": user_id, "bot": False}
                for user_id in self.reactions.get((message_id, emoji), [])
            ]
        return {
            "author": {"id": AGENT_BOT_ID, "bot": True},
            "channel_id": CHANNEL_ID,
            "content": self.contents[message_id],
            "id": message_id,
            "timestamp": self.timestamps[message_id],
        }

    def replies_after(
        self, channel_id: str, message_id: str
    ) -> list[dict[str, JsonValue]]:
        assert (channel_id, message_id) == (CHANNEL_ID, MESSAGE_ID)
        return self.attestations

    def post_reply(self, channel_id: str, message_id: str, content: str) -> None:
        assert (channel_id, message_id) == (CHANNEL_ID, MESSAGE_ID)
        self.peer_refresh_posts += 1
        self.attestations.append(
            {
                "author": {"id": PEER_BOT_ID, "bot": True},
                "channel_id": CHANNEL_ID,
                "content": content,
                "message_reference": {
                    "channel_id": CHANNEL_ID,
                    "message_id": MESSAGE_ID,
                },
                "timestamp": self.clock.current.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class LifecycleHarness:
    clock: FakeClock
    discord: FakeDiscord
    skill_dir: Path
    digest: str
    pending: Path
    approval_log: Path


def _deploy_bindings(skill: str) -> skill_gate_surface.SupplyChainSurface:
    return skill_gate_surface.SupplyChainSurface(
        skill_gate_surface.deploy_kind(skill), OWNER_ID, FakeDirectory()
    )


def _skill(tmp_path: Path) -> Path:
    skill_dir = tmp_path / SKILL
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    _ = (skill_dir / "SKILL.md").write_text(
        "---\nname: wiki\ndescription: Deterministic wiki skill.\n---\n",
        encoding="utf-8",
    )
    scenario = scripts / "scenario.sh"
    _ = scenario.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho SCENARIO-PASS\n",
        encoding="utf-8",
    )
    scenario.chmod(0o700)
    return skill_dir


def _review_status(_path: Path, _skill_name: str, _digest: str) -> str:
    return PASS_REVIEW


def _nonce(_length: int) -> str:
    return NONCE


def request_args(harness: LifecycleHarness) -> argparse.Namespace:
    return argparse.Namespace(
        skill=SKILL,
        hash=harness.digest,
        fresh=False,
        json=False,
        provenance_file="",
        peer_attest_mode="discord",
    )


def check_args(
    harness: LifecycleHarness, digest: str | None = None
) -> argparse.Namespace:
    return argparse.Namespace(
        skill=SKILL,
        hash=harness.digest if digest is None else digest,
        message_id=MESSAGE_ID,
        deploy_nonce=NONCE,
        injection_file="",
        provenance_file="",
        peer_attest_mode="discord",
    )


def peer_request(harness: LifecycleHarness) -> peer_attest.AttestRequest:
    return peer_attest.AttestRequest(
        skill=SKILL,
        staged_dir=harness.skill_dir,
        expected_digest=harness.digest,
        request_message_id=MESSAGE_ID,
        deploy_nonce=NONCE,
        channel_id=CHANNEL_ID,
    )


def install_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> LifecycleHarness:
    clock = FakeClock()
    discord = FakeDiscord(clock)
    skill_dir = _skill(tmp_path)
    digest = skill_digest(skill_dir)
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": OWNER_ID}), encoding="utf-8")
    peers = tmp_path / "peers.yaml"
    _ = peers.write_text(
        f"""peers:
  agent:
    account: agent
    bot_user_id: {AGENT_BOT_ID}
  peer:
    account: peer
    bot_user_id: {PEER_BOT_ID}
""",
        encoding="utf-8",
    )
    peers.chmod(0o600)
    monkeypatch.setattr(
        peer_attestation,
        "_trusted_owner_uids",
        lambda: frozenset({peers.stat().st_uid}),
    )
    gate_dir = tmp_path / "skill-gate"
    approval_log = tmp_path / "logs" / "approvals.jsonl"
    monkeypatch.setattr(skill_gate, "GATE_DIR", gate_dir)
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", approval_log)
    monkeypatch.setattr(skill_gate, "OPS_PEERS_CONFIG", peers)
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _deploy_bindings)
    monkeypatch.setattr(skill_gate, "_api", discord.api)
    monkeypatch.setattr(skill_gate, "_now", lambda: clock.current)
    monkeypatch.setattr(skill_gate, "review_status_line", _review_status)
    monkeypatch.setattr(secrets, "token_hex", _nonce)
    harness = LifecycleHarness(
        clock,
        discord,
        skill_dir,
        digest,
        gate_dir / "pending" / f"{SKILL}.json",
        approval_log,
    )
    assert skill_gate.cmd_request(request_args(harness)) == 0
    discord.reactions[(MESSAGE_ID, skill_gate_specs.APPROVE_EMOJI)] = [OWNER_ID]
    discord.attestations.append(
        {
            "author": {"id": PEER_BOT_ID, "bot": True},
            "channel_id": CHANNEL_ID,
            "content": peer_attestation.format_attestation(NONCE, SKILL, digest, "PASS"),
            "message_reference": {"channel_id": CHANNEL_ID, "message_id": MESSAGE_ID},
            "timestamp": clock.current.isoformat(),
        }
    )
    return harness


def approval_event_count(harness: LifecycleHarness) -> int:
    if not harness.approval_log.exists():
        return 0
    return len(harness.approval_log.read_text(encoding="utf-8").splitlines())


def execute_deploy(harness: LifecycleHarness, args: argparse.Namespace) -> int:
    decision = skill_gate.cmd_check(args)
    if decision != 0:
        refresh = skill_gate_refresh.refresh_required(args)
        if refresh != skill_gate_refresh.PEER_ATTESTATION_REFRESH_EXIT:
            return refresh
        attested = peer_attest.attest(
            replace(peer_request(harness), refresh=True),
            harness.discord,
            now=harness.clock.current,
        )
        if attested.exit_code != 0:
            return attested.exit_code
        decision = skill_gate.cmd_check(args)
    if decision == 0:
        harness.discord.mounts += 1
    return decision
