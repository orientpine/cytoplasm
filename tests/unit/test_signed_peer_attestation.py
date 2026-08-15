from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

import pytest

from automation import peer_attestation
from automation.peer_signed_attestation import (
    SignedAttestationPayload,
    signed_attestation_preimage,
)


_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
_CHANNEL_ID = "100000000000000009"
_MESSAGE_ID = "100000000000000010"
_NONCE = "1" * 32
_DIGEST = "a" * 64


def _generate_key(path: Path) -> None:
    _ = subprocess.run(
        ("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)),
        check=True,
        capture_output=True,
    )
    path.with_suffix(".pub").chmod(0o644)


def _payload() -> SignedAttestationPayload:
    return SignedAttestationPayload(
        request=_NONCE,
        skill="calendar",
        digest=_DIGEST,
        verdict="PASS",
        attested_at=_NOW - timedelta(seconds=1),
        approval_channel=_CHANNEL_ID,
        approval_message=_MESSAGE_ID,
    )


def _expectation(
    *, requested_at: datetime = _NOW - timedelta(minutes=1)
) -> peer_attestation.AttestationExpectation:
    return peer_attestation.AttestationExpectation(
        channel_id=_CHANNEL_ID,
        message_id=_MESSAGE_ID,
        deploy_nonce=_NONCE,
        skill="calendar",
        digest=_DIGEST,
        requested_at=requested_at,
    )


def _sign(private_key: Path, payload: SignedAttestationPayload, namespace: str) -> str:
    completed = subprocess.run(
        (
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            namespace,
        ),
        input=signed_attestation_preimage(payload),
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode("ascii")


def _blob(
    private_key: Path,
    payload: SignedAttestationPayload | None = None,
    *,
    namespace: str = "autophagy-peer-attest",
) -> str:
    bound = payload or _payload()
    return peer_attestation.format_signed_attestation(
        bound,
        _sign(private_key, bound, namespace),
    )


def _verifier(
    public_key: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> peer_attestation.SshSignedAttestationVerifier:
    public_key.parent.chmod(0o755)
    public_key.chmod(0o644)
    monkeypatch.setattr(
        peer_attestation,
        "_trusted_owner_uids",
        lambda: frozenset({os.getuid()}),
    )
    return peer_attestation.SshSignedAttestationVerifier(public_key)


def test_signed_attestation_when_record_is_canonical_and_key_is_trusted_then_accepts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one canonical record signed by the peer key anchored in a trusted path.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    blob = _blob(private_key)

    # When: the signed verifier checks the exact deployment binding and freshness window.
    valid = peer_attestation.valid_signed_attestation(
        blob,
        _expectation(),
        _verifier(private_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )

    # Then: the record is accepted and parses back to the canonical payload.
    assert valid
    parsed = peer_attestation.parse_signed_attestation(blob)
    assert parsed is not None and parsed.payload == _payload()


def test_signed_attestation_when_signed_by_a_different_key_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the record is signed by an attacker key while the trust root names the peer key.
    peer_key = tmp_path / "peer"
    attacker_key = tmp_path / "attacker"
    _generate_key(peer_key)
    _generate_key(attacker_key)

    # When / Then: a structurally valid record cannot cross the wrong-key boundary.
    assert not peer_attestation.valid_signed_attestation(
        _blob(attacker_key),
        _expectation(),
        _verifier(peer_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )


@pytest.mark.parametrize(
    ("before", "after"),
    (
        ("autophagy-peer-attest-v1", "autophagy-peer-attest-v2"),
        (f"request={_NONCE}", f"request={'2' * 32}"),
        ("skill=calendar", "skill=meeting"),
        (f"sha256={_DIGEST}", f"sha256={'b' * 64}"),
        ("verdict=PASS", "verdict=FAIL"),
        ("reviewer=peer-sandbox-v1", "reviewer=peer-sandbox-v2"),
        ("attested_at=2026-08-15T11:59:59.000000Z", "attested_at=2026-08-15T11:59:58.000000Z"),
        (f"approval_channel={_CHANNEL_ID}", "approval_channel=100000000000000019"),
        (f"approval_message={_MESSAGE_ID}", "approval_message=100000000000000020"),
    ),
    ids=("version", "request", "skill", "digest", "verdict", "reviewer", "time", "channel", "message"),
)
def test_signed_attestation_when_any_single_preimage_field_is_tampered_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: str,
    after: str,
) -> None:
    # Given: a valid signed record with exactly one preimage field changed after signing.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    tampered = _blob(private_key).replace(before, after, 1)

    # When / Then: no individual preimage edit remains an approvable record.
    assert not peer_attestation.valid_signed_attestation(
        tampered,
        _expectation(),
        _verifier(private_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )


def test_signed_attestation_when_older_than_ttl_by_one_second_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the signature is valid but its attestation clock exceeds the shared TTL by one second.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    payload = replace(
        _payload(),
        attested_at=_NOW - peer_attestation.PEER_ATTESTATION_TTL - timedelta(seconds=1),
    )

    # When / Then: the signed transport preserves the Discord mode's exact freshness bound.
    assert not peer_attestation.valid_signed_attestation(
        _blob(private_key, payload),
        _expectation(requested_at=payload.attested_at - timedelta(minutes=1)),
        _verifier(private_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )


def test_signed_attestation_when_attested_before_request_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid signature whose timestamp predates the owner approval request.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    payload = replace(_payload(), attested_at=_NOW - timedelta(minutes=2))

    # When / Then: a pre-request verdict cannot authorize the later request.
    assert not peer_attestation.valid_signed_attestation(
        _blob(private_key, payload),
        _expectation(requested_at=_NOW - timedelta(minutes=1)),
        _verifier(private_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )


def _wrong_nonce(payload: SignedAttestationPayload) -> SignedAttestationPayload:
    return replace(payload, request="2" * 32)


def _wrong_skill(payload: SignedAttestationPayload) -> SignedAttestationPayload:
    return replace(payload, skill="meeting")


def _wrong_digest(payload: SignedAttestationPayload) -> SignedAttestationPayload:
    return replace(payload, digest="b" * 64)


_BINDING_MUTATIONS: tuple[
    Callable[[SignedAttestationPayload], SignedAttestationPayload], ...
] = (_wrong_nonce, _wrong_skill, _wrong_digest)


@pytest.mark.parametrize(
    "mutate",
    _BINDING_MUTATIONS,
    ids=("nonce", "skill", "digest"),
)
def test_signed_attestation_when_required_binding_mismatches_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[SignedAttestationPayload], SignedAttestationPayload],
) -> None:
    # Given: a genuinely signed record that binds a different request field.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    payload = mutate(_payload())

    # When / Then: nonce, skill, and digest mismatches each fail closed independently.
    assert not peer_attestation.valid_signed_attestation(
        _blob(private_key, payload),
        _expectation(),
        _verifier(private_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )


def test_signed_attestation_when_verdict_is_fail_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the peer authentically signs a FAIL verdict for the exact requested bytes.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    payload = replace(_payload(), verdict="FAIL")

    # When / Then: authenticity never turns a failing review into permission to mount.
    assert not peer_attestation.valid_signed_attestation(
        _blob(private_key, payload),
        _expectation(),
        _verifier(private_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )


def _without_signature(blob: str) -> str:
    return blob.split("-----BEGIN SSH SIGNATURE-----", maxsplit=1)[0]


def _unchanged(blob: str) -> str:
    return blob


@pytest.mark.parametrize(
    ("mutate", "namespace"),
    (
        (_without_signature, "autophagy-peer-attest"),
        (_unchanged, "git"),
    ),
    ids=("missing-signature", "wrong-namespace"),
)
def test_signed_attestation_when_signature_namespace_is_missing_or_wrong_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[str], str],
    namespace: str,
) -> None:
    # Given: the record either has no SSH signature or one scoped to another protocol.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    blob = mutate(_blob(private_key, namespace=namespace))

    # When / Then: only the mandatory autophagy-peer-attest namespace authorizes this protocol.
    assert not peer_attestation.valid_signed_attestation(
        blob,
        _expectation(),
        _verifier(private_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )


def test_signed_attestation_when_public_key_is_readable_but_agent_owned_then_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the configured public key is readable but its owner is outside root-or-ops trust.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    public_key = private_key.with_suffix(".pub")
    public_key.parent.chmod(0o755)
    public_key.chmod(0o644)
    monkeypatch.setattr(peer_attestation, "_trusted_owner_uids", frozenset)

    # When / Then: readable-but-agent-writable key material cannot redefine the verifier root.
    assert not peer_attestation.valid_signed_attestation(
        _blob(private_key),
        _expectation(),
        peer_attestation.SshSignedAttestationVerifier(public_key),
        _NOW,
    )


def test_signed_attestation_when_stdout_contains_two_records_then_rejects_framing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a courier output contains two individually valid signed records.
    private_key = tmp_path / "peer"
    _generate_key(private_key)
    record = _blob(private_key)

    # When / Then: the parser accepts exactly one full stdout record, never a prefix.
    assert not peer_attestation.valid_signed_attestation(
        record + record,
        _expectation(),
        _verifier(private_key.with_suffix(".pub"), monkeypatch),
        _NOW,
    )
