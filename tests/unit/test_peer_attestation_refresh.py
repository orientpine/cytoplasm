from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from automation import peer_attest, peer_attestation
from automation.skill_review import skill_digest

_AGENT_BOT_ID = "111111111111111111"
_PEER_BOT_ID = "222222222222222222"
_CHANNEL_ID = "100000000000000009"
_MESSAGE_ID = "request-1"
_NONCE = "1" * 32
_NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


class FakeDiscordTransport:
    def __init__(self, existing: list[dict[str, Any]]) -> None:
        self.existing = existing
        self.posted: list[tuple[str, str, str]] = []

    def replies_after(self, channel_id: str, message_id: str) -> list[dict[str, Any]]:
        assert (channel_id, message_id) == (_CHANNEL_ID, _MESSAGE_ID)
        return self.existing

    def post_reply(self, channel_id: str, message_id: str, content: str) -> None:
        self.posted.append((channel_id, message_id, content))


def _skill(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "calendar"
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    _ = (skill_dir / "SKILL.md").write_text(
        "---\nname: calendar\ndescription: Deterministic calendar skill.\n---\n",
        encoding="utf-8",
    )
    scenario = scripts / "scenario.sh"
    _ = scenario.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho SCENARIO-PASS\n",
        encoding="utf-8",
    )
    scenario.chmod(0o700)
    return skill_dir


def _request(skill_dir: Path) -> peer_attest.AttestRequest:
    return peer_attest.AttestRequest(
        skill="calendar",
        staged_dir=skill_dir,
        expected_digest=skill_digest(skill_dir),
        request_message_id=_MESSAGE_ID,
        deploy_nonce=_NONCE,
        channel_id=_CHANNEL_ID,
    )


def _attestation(digest: str, timestamp: datetime) -> dict[str, Any]:
    return {
        "author": {"id": _PEER_BOT_ID, "bot": True},
        "channel_id": _CHANNEL_ID,
        "content": peer_attestation.format_attestation(
            _NONCE, "calendar", digest, "PASS"
        ),
        "message_reference": {
            "channel_id": _CHANNEL_ID,
            "message_id": _MESSAGE_ID,
        },
        "timestamp": timestamp.isoformat(),
    }


def _expectation(digest: str) -> peer_attestation.AttestationExpectation:
    return peer_attestation.AttestationExpectation(
        channel_id=_CHANNEL_ID,
        message_id=_MESSAGE_ID,
        deploy_nonce=_NONCE,
        skill="calendar",
        digest=digest,
        requested_at=_NOW - timedelta(minutes=45),
    )


def test_verifier_when_request_is_old_but_same_binding_attestation_is_fresh_then_accepts(
    tmp_path: Path,
) -> None:
    # Given: the owner request is older than the TTL, but peer re-attested one minute ago.
    digest = skill_digest(_skill(tmp_path))
    messages = [_attestation(digest, _NOW - timedelta(minutes=1))]

    # When: mount verifies the exact original binding at the execution clock.
    valid = peer_attestation.valid_peer_attestation(
        messages,
        _expectation(digest),
        peer_attestation.BotIds(_AGENT_BOT_ID, _PEER_BOT_ID),
        _NOW,
    )

    # Then: peer TTL applies to the fresh attestation, not the durable owner decision.
    assert valid, "fresh peer re-attestation for the unchanged owner binding was rejected"


def test_verifier_when_latest_same_binding_attestation_exceeds_ttl_then_rejects(
    tmp_path: Path,
) -> None:
    # Given: the only exact peer attestation itself exceeds the shared TTL.
    digest = skill_digest(_skill(tmp_path))
    messages = [
        _attestation(digest, _NOW - peer_attestation.PEER_ATTESTATION_TTL - timedelta(seconds=1))
    ]

    # When / Then: an old peer verdict never authorizes mount.
    assert not peer_attestation.valid_peer_attestation(
        messages,
        _expectation(digest),
        peer_attestation.BotIds(_AGENT_BOT_ID, _PEER_BOT_ID),
        _NOW,
    )


def test_attestor_when_refresh_authorized_and_prior_reply_expired_then_posts_fresh_same_binding(
    tmp_path: Path,
) -> None:
    # Given: one expired PASS reply for the exact content, nonce, action target, and request.
    request = _request(_skill(tmp_path))
    stale = _attestation(
        request.expected_digest,
        _NOW - peer_attestation.PEER_ATTESTATION_TTL - timedelta(seconds=1),
    )
    transport = FakeDiscordTransport([stale])

    # When: the deploy authorizes a refresh at a deterministic execution clock.
    result = peer_attest.attest(replace(request, refresh=True), transport, now=_NOW)

    # Then: independent review runs and persists one new verdict for the SAME binding.
    assert result.exit_code == 0
    assert transport.posted == [
        (
            _CHANNEL_ID,
            _MESSAGE_ID,
            peer_attestation.format_attestation(
                _NONCE, "calendar", request.expected_digest, "PASS"
            ),
        )
    ]


def test_attestor_when_refresh_authorized_but_prior_reply_is_fresh_then_stays_idempotent(
    tmp_path: Path,
) -> None:
    # Given: the exact peer verdict remains inside its TTL.
    request = _request(_skill(tmp_path))
    transport = FakeDiscordTransport(
        [_attestation(request.expected_digest, _NOW - timedelta(minutes=1))]
    )

    # When: a concurrent execution reaches the same refresh attempt.
    result = peer_attest.attest(replace(request, refresh=True), transport, now=_NOW)

    # Then: the existing fresh verdict wins and no duplicate is persisted.
    assert result.exit_code == 0
    assert transport.posted == []


def test_attestor_when_refresh_is_not_authorized_then_expired_reply_is_not_reposted(
    tmp_path: Path,
) -> None:
    # Given: the exact reply is old, but the owner gate has not authorized refresh.
    request = _request(_skill(tmp_path))
    transport = FakeDiscordTransport(
        [
            _attestation(
                request.expected_digest,
                _NOW - peer_attestation.PEER_ATTESTATION_TTL - timedelta(seconds=1),
            )
        ]
    )

    # When: the ordinary initial attestation path runs.
    result = peer_attest.attest(request, transport, now=_NOW)

    # Then: only the dedicated owner-authorized path may create a replacement verdict.
    assert result.exit_code == 0
    assert transport.posted == []
