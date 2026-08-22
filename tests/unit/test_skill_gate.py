from __future__ import annotations

import argparse
from copy import deepcopy
from collections.abc import Callable
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

import pytest

from automation import peer_attestation, skill_gate, skill_gate_specs, skill_gate_surface
from automation.interop.approval_surface import (
    _TRANSITIONS,
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ChannelFacts,
    surface_at_policy,
)
from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.peer_attestation import format_attestation


_CHECKS = ("content_digest", "frontmatter", "scenario", "secret_scan")
_DIGEST = "a" * 64
_NONCE = "1" * 32
_OWNER_ID = "111111111111111111"
_APPROVALS_CHANNEL_ID = "100000000000000009"
_AGENT_BOT_ID = "222222222222222222"
_PEER_BOT_ID = "333333333333333333"
_REQUEST_TIMESTAMP = "2026-07-17T00:00:00.000000+00:00"
_ATTESTATION_TIMESTAMP = "2026-07-17T00:01:00.000000+00:00"
_REQUEST_CONTENT = (
    "[skill-deploy] calendar 배포 승인 요청\n"
    "- skill: `calendar`\n"
    f"- sha256: `{_DIGEST}`\n"
    f"- deploy_nonce: `{_NONCE}`\n"
    "- review: ✅ PASS (frontmatter/scenario/secret_scan/content_digest 4/4, sha256-bound)\n"
    "- sandbox: PASS (peer 인스턴스, DUMMY 시크릿)\n"
    "- 승인 방법: 이 메시지에 cha가 ✅ 리액션 (소유자 전용 — 봇/타인 리액션은 거부됨)"
)


class _FakeDirectory:
    """What the shared directory answers for this bot — no Discord, no guild scan."""

    def owner_dm(self) -> str:
        raise AssertionError("the skill supply chain must never open a DM (SI-6)")

    def skill_approvals(self) -> str:
        return _APPROVALS_CHANNEL_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == _APPROVALS_CHANNEL_ID
        return ChannelFacts(0, "approvals", ())


def _bindings(skill: str) -> skill_gate_surface.SupplyChainSurface:
    """The gate's declared surface with the directory stubbed at its own boundary."""
    return skill_gate_surface.SupplyChainSurface(
        skill_gate_surface.deploy_kind(skill), _OWNER_ID, _FakeDirectory()
    )


def _write_verdict(path: Path, digest: str, verdict: str) -> None:
    path.parent.mkdir(parents=True)
    record = {
        "skill": "calendar",
        "hash": digest,
        "verdict": verdict,
        "checks": {check: True for check in _CHECKS},
        "timestamp": "2026-07-17T00:00:00Z",
    }
    _ = path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _request_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    gate_dir = tmp_path / "skill-gate"
    posted_messages: list[str] = []

    def discord_api(_method: str, _path: str, payload: dict[str, str] | None = None) -> dict[str, str]:
        assert payload is not None
        posted_messages.append(payload["content"])
        return {"id": "message-1"}

    monkeypatch.setattr(skill_gate, "GATE_DIR", gate_dir)
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _bindings)
    monkeypatch.setattr(skill_gate, "_api", discord_api)

    result = skill_gate.cmd_request(argparse.Namespace(skill="calendar", hash=_DIGEST, fresh=False, json=False))

    assert result == 0
    return posted_messages[0]


def test_cmd_request_when_matching_pass_verdict_exists_then_posts_sha_bound_review_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the ledger records a complete PASS for the requested skill hash.
    _write_verdict(tmp_path / "skill-gate" / "review-verdicts.jsonl", _DIGEST, "PASS")

    # When: the request is posted to mocked Discord.
    content = _request_message(tmp_path, monkeypatch)

    # Then: the review proof is visible between hash binding and sandbox status.
    review = "- review: ✅ PASS (frontmatter/scenario/secret_scan/content_digest 4/4, sha256-bound)"
    assert review in content
    assert content.index(f"- sha256: `{_DIGEST}`") < content.index(review) < content.index("- sandbox:")


def test_request_binding_when_cmd_request_content_is_exact_then_captures_skill_digest_and_nonce() -> None:
    # Given: the exact approval-request content produced for a hash-bound PASS review.
    content = _REQUEST_CONTENT

    # When: the current binding regex parses the request.
    match = skill_gate._REQUEST_BINDING.match(content)

    # Then: the three protocol fields are captured from the generated prefix.
    assert match is not None
    assert match.groupdict() == {"skill": "calendar", "digest": _DIGEST, "nonce": _NONCE}


def test_request_binding_when_provenance_lines_are_appended_then_prefix_still_matches() -> None:
    # Given: the exact approval request with later provenance lines appended.
    content = _REQUEST_CONTENT + "\n- provenance: manifest-sha256: " + "b" * 64

    # When: the binding is matched against the extended message.
    match = skill_gate._REQUEST_BINDING.match(content)

    # Then: the prefix-anchored binding continues to accept the request.
    assert match is not None
    assert match.groupdict() == {"skill": "calendar", "digest": _DIGEST, "nonce": _NONCE}


def test_log_approval_when_written_then_record_shape_and_canonical_hash_are_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a deterministic approval log destination and timestamp.
    approval_log = tmp_path / "approvals.jsonl"
    timestamp = "2026-07-17T00:03:00Z"
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", approval_log)
    monkeypatch.setattr(skill_gate, "_utc_now", lambda: timestamp)

    # When: the current approval logger records an owner approval.
    skill_gate._log_approval(
        argparse.Namespace(skill="calendar", hash=_DIGEST, message_id="message-1"),
        "owner-reaction",
    )

    # Then: the JSONL record exposes the stable fields and reproducible canonical hash.
    record = json.loads(approval_log.read_text(encoding="utf-8"))
    assert set(record) == {"action", "approval", "hash", "result", "target_id", "timestamp"}
    assert record["action"] == "skill.deploy"
    assert record["approval"] == {
        "channel": "approvals",
        "message_id": "message-1",
        "method": "owner-reaction",
    }
    assert record["result"] == {"status": "approved"}
    assert record["target_id"] == "skill:calendar"
    assert record["timestamp"] == timestamp

    payload = {
        "action": "skill.deploy",
        "approval": {
            "channel": "approvals",
            "message_id": "message-1",
            "method": "owner-reaction",
        },
        "payload": {"skill_sha256": _DIGEST},
        "target_id": "skill:calendar",
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    expected_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert record["hash"] == expected_hash


@pytest.mark.parametrize(
    ("stored_digest", "verdict"),
    [(None, None), ("b" * 64, "PASS"), (_DIGEST, "FAIL")],
    ids=("absent", "different-hash", "fail"),
)
def test_cmd_request_when_matching_pass_verdict_is_absent_then_posts_review_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored_digest: str | None,
    verdict: str | None,
) -> None:
    # Given: the ledger is absent, hash-mismatched, or explicitly failed.
    if stored_digest is not None and verdict is not None:
        _write_verdict(tmp_path / "skill-gate" / "review-verdicts.jsonl", stored_digest, verdict)

    # When: the request is posted to mocked Discord.
    content = _request_message(tmp_path, monkeypatch)

    # Then: review status fails closed rather than claiming a hash-bound PASS.
    assert "- review: ❌ 미검토/FAIL — 승인 금지" in content


def _write_peers(path: Path) -> None:
    _ = path.write_text(
        "peers:\n"
        "  agent-cha:\n"
        f"    bot_user_id: \"{_AGENT_BOT_ID}\"\n"
        "    account: agent\n"
        "  peer-test:\n"
        f"    bot_user_id: \"{_PEER_BOT_ID}\"\n"
        "    account: peer\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _request_record() -> dict[str, object]:
    return {
        "id": "request-1",
        "channel_id": _APPROVALS_CHANNEL_ID,
        "timestamp": _REQUEST_TIMESTAMP,
        "author": {"id": _AGENT_BOT_ID, "bot": True},
        "content": _REQUEST_CONTENT,
    }


def _attestation_record() -> dict[str, object]:
    return {
        "channel_id": _APPROVALS_CHANNEL_ID,
        "timestamp": _ATTESTATION_TIMESTAMP,
        "author": {"id": _PEER_BOT_ID, "bot": True},
        "message_reference": {"message_id": "request-1", "channel_id": _APPROVALS_CHANNEL_ID},
        "content": format_attestation(_NONCE, "calendar", _DIGEST, "PASS"),
    }


def _check_args(injection_file: str = "") -> argparse.Namespace:
    return argparse.Namespace(
        skill="calendar",
        hash=_DIGEST,
        message_id="request-1",
        deploy_nonce=_NONCE,
        injection_file=injection_file,
        peer_attest_mode="discord",
    )


def _configure_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    messages: list[dict[str, object]],
    *,
    reactions: list[dict[str, object]] | None = None,
) -> None:
    interop = tmp_path / "interop.json"
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    peers = tmp_path / "peers.yaml"
    _write_peers(peers)
    monkeypatch.setattr(
        peer_attestation,
        "_trusted_owner_uids",
        lambda: frozenset({peers.stat().st_uid}),
    )
    _write_verdict(tmp_path / "skill-gate" / "review-verdicts.jsonl", _DIGEST, "PASS")

    pending_dir = tmp_path / "skill-gate" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    pending_file = pending_dir / "calendar.json"

    _ = pending_file.write_text(
        json.dumps(
            {
                "action_hash": skill_gate_specs._hash(
                    "skill-deploy", "calendar", _DIGEST, "", ""
                ),
                "approval_action": "skill.deploy",
                "approval_destination": "skill:calendar",
                "channel_id": _APPROVALS_CHANNEL_ID,
                "deploy_nonce": _NONCE,
                "hash": _DIGEST,
                "kind": "skill-deploy",
                "message_id": "request-1",
                "policy_version": str(POLICY_VERSION),
                "surface": "skill-approvals",
            }
        ),
        encoding="utf-8",
    )

    def discord_api(method: str, path: str, payload: dict[str, object] | None = None) -> object:
        assert method == "GET"
        assert payload is None
        if path == f"/channels/{_APPROVALS_CHANNEL_ID}/messages/request-1":
            return _request_record()
        if path.startswith(f"/channels/{_APPROVALS_CHANNEL_ID}/messages?"):
            return messages
        if "/reactions/" in path:
            if "✅" in path or "%E2%9C%85" in path:
                return reactions if reactions is not None else [{"id": _OWNER_ID, "bot": False}]
            return []
        raise AssertionError(path)

    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path / "skill-gate")
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "OPS_PEERS_CONFIG", peers)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "approvals.jsonl")
    monkeypatch.setattr(skill_gate, "_deploy_bindings", _bindings)
    monkeypatch.setattr(skill_gate, "_api", discord_api)
    monkeypatch.setattr(skill_gate, "_now", lambda: skill_gate._parse_timestamp("2026-07-17T00:02:00+00:00"))


def test_cmd_check_when_owner_and_valid_peer_attestation_then_approves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the agent-authored request, owner reaction, and peer bot reply all bind one nonce and digest.
    _configure_check(tmp_path, monkeypatch, [_attestation_record()])

    # When: the production check evaluates the deployment approval.
    result = skill_gate.cmd_check(_check_args())

    # Then: both independent attestations are required and the gate approves.
    assert result == 0


def test_cmd_check_when_peer_attestation_is_missing_then_rejects_even_in_e2e_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: E2E provides a valid signed owner injection but Discord has no peer reply.
    _configure_check(tmp_path, monkeypatch, [])
    secret = "test-e2e-secret"
    event = InboundEvent("event-1", _OWNER_ID, _APPROVALS_CHANNEL_ID, skill_gate._approval_text("calendar", _DIGEST, "request-1"))
    injection = tmp_path / "injection.json"
    _ = injection.write_text(
        json.dumps({"event": asdict(event), "signature": sign_event(event, secret.encode("utf-8"))}),
        encoding="utf-8",
    )
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("INTEROP_E2E_SECRET", secret)

    # When: the E2E-only owner replacement path runs.
    result = skill_gate.cmd_check(_check_args(str(injection)))

    # Then: E2E never bypasses the genuine peer attestation requirement.
    assert result == 1


def test_cmd_check_when_discord_peer_trust_root_is_unavailable_then_reports_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: Discord carries an otherwise valid peer reply but its private identity anchor is absent.
    _configure_check(tmp_path, monkeypatch, [_attestation_record()])
    monkeypatch.setattr(skill_gate, "OPS_PEERS_CONFIG", tmp_path / "missing-peers.yaml")

    # When: the production check tries to authenticate the peer bot.
    result = skill_gate.cmd_check(_check_args())

    # Then: configuration failure is distinct from a genuinely absent attestation.
    assert result == 2


@pytest.mark.parametrize(
    ("mutate", "reactions"),
    (
        (lambda record: record["author"].update({"id": _AGENT_BOT_ID}), None),
        (lambda record: record["author"].update({"bot": False}), None),
        (lambda record: record.update({"webhook_id": "webhook-1"}), None),
        (lambda record: record.update({"content": format_attestation("2" * 32, "calendar", _DIGEST, "PASS")}), None),
        (lambda record: record.update({"content": format_attestation(_NONCE, "calendar", "b" * 64, "PASS")}), None),
        (lambda record: record.update({"timestamp": "2026-07-16T23:59:00+00:00"}), None),
        (lambda record: record.update({"timestamp": "2026-07-17T01:00:00+00:00"}), None),
    ),
    ids=("agent-forgery", "human-author", "webhook", "wrong-nonce", "wrong-digest", "pre-request", "expired"),
)
def test_cmd_check_when_peer_attestation_binding_is_invalid_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, object]], None],
    reactions: list[dict[str, object]] | None,
) -> None:
    # Given: the owner reacts, but one required peer attestation property is invalid.
    record = deepcopy(_attestation_record())
    mutate(record)
    _configure_check(tmp_path, monkeypatch, [record], reactions=reactions)

    # When: the production check evaluates the candidate reply.
    result = skill_gate.cmd_check(_check_args())

    # Then: every mismatch fails closed before mount authorization.
    assert result == 1


def test_cmd_request_when_verdict_file_is_malformed_then_posts_review_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the recorded ledger cannot be parsed as JSONL.
    verdicts = tmp_path / "skill-gate" / "review-verdicts.jsonl"
    verdicts.parent.mkdir()
    _ = verdicts.write_text("{malformed\n", encoding="utf-8")
    verdicts.chmod(0o600)

    # When: the request is posted to mocked Discord.
    content = _request_message(tmp_path, monkeypatch)

    # Then: malformed evidence cannot falsely advertise review completion.
    assert "- review: ❌ 미검토/FAIL — 승인 금지" in content


def _channel_lookup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    interop = tmp_path / "interop.json"
    gate_dir = tmp_path / "skill-gate"
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", interop)
    monkeypatch.setattr(skill_gate, "GATE_DIR", gate_dir)
    return interop, gate_dir


def _facts_only_api(method: str, path: str, payload: dict[str, object] | None = None) -> object:
    """Channel facts are the ONE call a pinned or cached resolution is still allowed."""
    assert (method, payload) == ("GET", None)
    assert path.startswith("/channels/"), f"unexpected Discord API call: {method} {path}"
    return {"type": 0, "name": "approvals", "id": path.removeprefix("/channels/")}


def _resolved_channel_id(skill: str = "calendar") -> str:
    return skill_gate._deploy_bindings(skill).new().channel_id


def test_approvals_channel_prefers_interop_config_over_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: interop config pins the deploy channel while a stale cache points elsewhere.
    interop, gate_dir = _channel_lookup_env(tmp_path, monkeypatch)
    _ = interop.write_text(
        json.dumps({"owner_id": _OWNER_ID, "deploy_approvals_channel_id": _APPROVALS_CHANNEL_ID}),
        encoding="utf-8",
    )
    gate_dir.mkdir()
    _ = (gate_dir / "config.json").write_text(
        json.dumps({"approvals_channel_id": "100000000000000099"}), encoding="utf-8"
    )
    monkeypatch.setattr(skill_gate, "_api", _facts_only_api)

    # When: the declared deploy surface is resolved through the shared directory.
    channel_id = _resolved_channel_id()

    # Then: the operator pin supersedes the cache and no guild scan runs.
    assert channel_id == _APPROVALS_CHANNEL_ID


def test_approvals_channel_falls_back_to_cache_when_config_lacks_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: interop config exists without any channel key but a per-bot cache holds one.
    interop, gate_dir = _channel_lookup_env(tmp_path, monkeypatch)
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    gate_dir.mkdir()
    fingerprint = hashlib.sha256(b"bot-token").hexdigest()[:16]
    _ = (gate_dir / "config.json").write_text(
        json.dumps({"token_fingerprint": fingerprint, "approvals_channel_id": _APPROVALS_CHANNEL_ID}),
        encoding="utf-8",
    )
    monkeypatch.setattr(skill_gate, "_api", _facts_only_api)

    # Then: the cached id is returned without a guild scan.
    assert _resolved_channel_id() == _APPROVALS_CHANNEL_ID


def test_approvals_channel_guild_scan_multi_match_exits_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: no config key, no cache, and two guilds each expose an approvals channel.
    interop, _gate_dir = _channel_lookup_env(tmp_path, monkeypatch)
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")
    channels = {"guild-1": "100000000000000001", "guild-2": "100000000000000002"}

    def discord_api(method: str, path: str, payload: dict[str, object] | None = None) -> object:
        assert (method, payload) == ("GET", None)
        if path == "/users/@me/guilds":
            return [{"id": guild_id} for guild_id in channels]
        guild_id = path.removeprefix("/guilds/").removesuffix("/channels")
        return [{"type": 0, "name": "approvals", "id": channels[guild_id]}]

    monkeypatch.setattr(skill_gate, "_api", discord_api)
    monkeypatch.setattr(
        sys, "argv", ["skill_gate.py", "request", "--skill", "calendar", "--hash", _DIGEST]
    )

    # When: the ambiguous guild scan runs behind the real CLI entry point.
    exit_code = skill_gate.main()

    # Then: the gate fails closed on 2 and names the config key that resolves the ambiguity.
    assert exit_code == 2
    assert "deploy_approvals_channel_id" in capsys.readouterr().err


def test_approvals_channel_guild_scan_single_match_returns_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: no config key, no cache, and exactly one guild exposes an approvals channel.
    interop, gate_dir = _channel_lookup_env(tmp_path, monkeypatch)
    _ = interop.write_text(json.dumps({"owner_id": _OWNER_ID}), encoding="utf-8")

    def discord_api(method: str, path: str, payload: dict[str, object] | None = None) -> object:
        assert (method, payload) == ("GET", None)
        if path == "/users/@me/guilds":
            return [{"id": "guild-1"}]
        if path == "/guilds/guild-1/channels":
            return [{"type": 0, "name": "approvals", "id": _APPROVALS_CHANNEL_ID}]
        return _facts_only_api(method, path)

    monkeypatch.setattr(skill_gate, "_api", discord_api)

    # When: the unambiguous guild scan runs.
    channel_id = _resolved_channel_id()

    # Then: the single match is returned and cached per bot identity for later runs.
    assert channel_id == _APPROVALS_CHANNEL_ID
    cached = json.loads((gate_dir / "config.json").read_text(encoding="utf-8"))
    assert cached["approvals_channel_id"] == _APPROVALS_CHANNEL_ID
    assert cached["token_fingerprint"] == hashlib.sha256(b"bot-token").hexdigest()[:16]


def test_managed_activation_declares_its_own_kind_on_the_same_surface() -> None:
    # Given / When: the reserved `managed-` prefix decides what the owner's ✅ authorizes.
    # Then: a managed activation is its own kind, still on the supply-chain surface (SI-6).
    assert skill_gate_surface.deploy_kind("managed-calendar") is ApprovalKind.MANAGED_ACTIVATE
    assert skill_gate_surface.deploy_kind("calendar") is ApprovalKind.SKILL_DEPLOY


# ── AS-1.10 · SI-6: the supply chain never leaves the guild approval surface ──
@pytest.mark.parametrize("kind", skill_gate_surface.SUPPLY_CHAIN_KINDS, ids=lambda kind: kind.value)
@pytest.mark.parametrize("policy_version", range(POLICY_VERSION + 6))
def test_supply_chain_kinds_never_resolve_to_owner_dm(kind: ApprovalKind, policy_version: int) -> None:
    # Given: one supply-chain kind, read at a policy version already allocated or not yet.
    # When: the policy is asked where that kind's owner approval must live.
    surface = surface_at_policy(kind, policy_version)

    # Then: it is the guild approval surface at every version, and the kind's transition
    # ledger still holds exactly ONE row — so a later flip cannot sweep it along (SI-6).
    assert surface is ApprovalSurface.SKILL_APPROVALS
    assert len(_TRANSITIONS[kind]) == 1


def test_new_deploy_record_persists_every_binding_field() -> None:
    # Given: the binding a brand-new deploy approval resolved to.
    binding = ApprovalBinding(
        ApprovalKind.SKILL_DEPLOY, ApprovalSurface.SKILL_APPROVALS, _APPROVALS_CHANNEL_ID, POLICY_VERSION
    )
    spec = skill_gate_specs.DeploySpec(
        skill="calendar",
        digest=_DIGEST,
        deploy_nonce=_NONCE,
        review_status="- review: ✅ PASS",
        provenance=skill_gate_specs.provenance_of(""),
        binding=skill_gate._REQUEST_BINDING,
    )

    # When: the spec builds the pending record that ✅ will be bound to.
    record = spec.new_record("message-1", binding)

    # Then: the record replays the binding without re-resolving anything (SI-1).
    assert {name: record[name] for name in ("channel_id", "kind", "policy_version", "surface")} == {
        "channel_id": _APPROVALS_CHANNEL_ID,
        "kind": "skill-deploy",
        "policy_version": str(POLICY_VERSION),
        "surface": "skill-approvals",
    }


def test_the_request_line_names_the_skill_so_threads_are_distinguishable() -> None:
    """Hermes 는 메시지 앞 80자를 스레드 제목으로 쓴다 — 그 창이 sha256 에서 끝나면
    16건의 제목이 전부 같아 보인다(2026-08-20 소유자 실측: "승인 메시지를 구분할 수 없다")."""
    spec = skill_gate_specs.DeploySpec(
        skill="calendar",
        digest=_DIGEST,
        deploy_nonce=_NONCE,
        review_status="- review: ✅ PASS",
        peer_status="",
        provenance=skill_gate_specs.Provenance("", "", ""),
        binding=skill_gate._REQUEST_BINDING,
        peer_attest_mode="discord",
    )

    title_window = " ".join(spec.render().split())[:80]

    # 요점은 창에서 해시를 완전히 몰아내는 것이 아니라, 사람이 **먼저 읽는 부분**이
    # 무엇에 대한 요청인지 말하는 것이다.
    assert title_window.startswith("[skill-deploy] calendar 배포 승인 요청")
    assert "calendar" in title_window[:40], "스킬명이 제목 앞부분에 있어야 구분된다"


def test_the_parser_still_accepts_the_old_first_line() -> None:
    """형식을 바꾸는 순간 이미 게시된 요청이 공중에 떠 있었다 — 그것들도 해소돼야 한다."""
    old = _REQUEST_CONTENT.replace(
        "[skill-deploy] calendar 배포 승인 요청", "[skill-deploy] 승인 요청"
    )

    matched = skill_gate._REQUEST_BINDING.match(old)

    assert matched is not None
    assert matched.group("skill") == "calendar"
