"""Shared peer-attestation wire contract and fail-closed verifier."""

from __future__ import annotations

import pwd
import os
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from automation.git_tag_signature import (
    DetachedSignatureInvocation,
    DetachedSignatureRequest,
    verify_detached_signature,
)
from automation.peer_signed_attestation import (
    SIGNATURE_NAMESPACE,
    SIGNATURE_PRINCIPAL,
    SignedAttestationExpectation,
    SignedAttestationPayload,
    SignedAttestationRecord,
    SignedAttestationVerifier,
    format_signed_attestation as _format_signed_attestation,
    parse_signed_attestation as _parse_signed_attestation,
    valid_signed_attestation as _valid_signed_attestation,
)

REVIEWER: Final = "peer-sandbox-v1"
PEER_ATTESTATION_TTL: Final = timedelta(minutes=30)
_SKILL: Final = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")
_NONCE: Final = re.compile(r"[0-9a-f]{32}")
_BODY: Final = re.compile(
    "".join(
        (
            r"^\[skill-attest\] request=(?P<request>[0-9a-f]{32}|[0-9]+) ",
            rf"skill=(?P<skill>{_SKILL.pattern}) sha256=(?P<digest>{_DIGEST.pattern}) ",
            rf"verdict=(?P<verdict>PASS|FAIL) reviewer={REVIEWER}$",
        )
    )
)
_PEER_START: Final = re.compile(r"^  [a-z0-9][a-z0-9-]*:\s*$")
_PEER_FIELD: Final = re.compile(r"^    (?P<key>account|bot_user_id):\s*(?P<value>[^#\s]+)\s*$")
_SNOWFLAKE: Final = re.compile(r"[0-9]{17,19}")
_UNTRUSTED_WRITE_BITS: Final = stat.S_IWGRP | stat.S_IWOTH


@dataclass(frozen=True, slots=True)
class BotIds:
    """Discord bot identities anchored in the ops-owned peer registry."""

    agent_bot_id: str
    peer_bot_id: str


@dataclass(frozen=True, slots=True)
class Attestation:
    """Parsed canonical attestation body fields."""

    request: str
    skill: str
    digest: str
    verdict: str


@dataclass(frozen=True, slots=True)
class AttestationExpectation:
    """One deployment request's immutable attestation binding."""

    channel_id: str
    message_id: str
    deploy_nonce: str
    skill: str
    digest: str
    requested_at: datetime


def format_attestation(request: str, skill: str, digest: str, verdict: str) -> str:
    """Build the exact producer/verifier attestation body."""
    return f"[skill-attest] request={request} skill={skill} sha256={digest} verdict={verdict} reviewer={REVIEWER}"


def parse_attestation(content: str) -> Attestation | None:
    """Parse only the single canonical attestation body form."""
    matched = _BODY.fullmatch(content)
    if matched is None:
        return None
    return Attestation(matched.group("request"), matched.group("skill"), matched.group("digest"), matched.group("verdict"))


def parse_timestamp(value: str) -> datetime | None:
    """Parse a timezone-aware Discord ISO-8601 timestamp."""
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _trusted_owner_uids() -> frozenset[int]:
    try:
        ops_uid = pwd.getpwnam("ops").pw_uid
    except (KeyError, OSError):
        return frozenset({0})
    return frozenset({0, ops_uid})


def _is_trusted_peer_config_path(path: Path) -> bool:
    """Require immutable root/ops ownership at both file and parent boundaries."""
    try:
        config_stat = path.lstat()
        parent_stat = path.parent.lstat()
    except OSError:
        return False
    trusted = _trusted_owner_uids()
    return (
        stat.S_ISREG(config_stat.st_mode)
        and stat.S_ISDIR(parent_stat.st_mode)
        and config_stat.st_uid in trusted
        and parent_stat.st_uid in trusted
        and config_stat.st_mode & _UNTRUSTED_WRITE_BITS == 0
        and parent_stat.st_mode & _UNTRUSTED_WRITE_BITS == 0
    )


@dataclass(frozen=True, slots=True)
class SshSignedAttestationVerifier:
    public_key_path: Path

    def fingerprint(self) -> str | None:
        if not _is_trusted_peer_config_path(self.public_key_path):
            return None
        try:
            completed = subprocess.run(
                ("ssh-keygen", "-lf", str(self.public_key_path), "-E", "sha256"),
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        fields = completed.stdout.split()
        if completed.returncode != 0 or len(fields) < 2 or not fields[1].startswith("SHA256:"):
            return None
        return fields[1]

    def verify(self, preimage: bytes, signature: bytes) -> bool:
        if not _is_trusted_peer_config_path(self.public_key_path):
            return False
        try:
            public_key = self.public_key_path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if len(public_key.splitlines()) != 1 or not public_key.startswith("ssh-ed25519 "):
            return False
        allowed_signers = (
            f'{SIGNATURE_PRINCIPAL} namespaces="{SIGNATURE_NAMESPACE}" {public_key}\n'
        ).encode("utf-8")
        return verify_detached_signature(
            DetachedSignatureInvocation(subprocess.run, dict(os.environ), 30.0),
            DetachedSignatureRequest(
                message=preimage,
                signature=signature,
                allowed_signers=allowed_signers,
                expected_principal=SIGNATURE_PRINCIPAL,
                namespace=SIGNATURE_NAMESPACE,
            ),
        )


def format_signed_attestation(payload: SignedAttestationPayload, signature: str) -> str:
    return _format_signed_attestation(payload, signature)


def parse_signed_attestation(blob: str) -> SignedAttestationRecord | None:
    return _parse_signed_attestation(blob)


def valid_signed_attestation(
    blob: str,
    expectation: AttestationExpectation,
    verifier: SignedAttestationVerifier,
    now: datetime,
) -> bool:
    return _valid_signed_attestation(
        blob,
        SignedAttestationExpectation(
            channel=expectation.channel_id,
            message=expectation.message_id,
            nonce=expectation.deploy_nonce,
            skill=expectation.skill,
            digest=expectation.digest,
            requested_at=expectation.requested_at,
            now=now,
            ttl=PEER_ATTESTATION_TTL,
        ),
        verifier,
    )


def load_bot_ids(path: Path) -> BotIds | None:
    """Read the agent and peer bot identities from simple trusted YAML."""
    if not _is_trusted_peer_config_path(path):
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        if _PEER_START.fullmatch(line) is not None:
            if current is not None:
                records.append(current)
            current = {}
            continue
        matched = _PEER_FIELD.fullmatch(line)
        if matched is not None and current is not None:
            current[matched.group("key")] = matched.group("value").strip('"')
    if current is not None:
        records.append(current)
    identities = {record.get("account", ""): record.get("bot_user_id", "") for record in records}
    agent_id, peer_id = identities.get("agent", ""), identities.get("peer", "")
    if _SNOWFLAKE.fullmatch(agent_id) is None or _SNOWFLAKE.fullmatch(peer_id) is None or agent_id == peer_id:
        return None
    return BotIds(agent_id, peer_id)


def valid_peer_attestation(
    messages: Sequence[Mapping[str, Any]], expectation: AttestationExpectation, bot_ids: BotIds, now: datetime
) -> bool:
    """Return true only for a fresh request-bound peer-bot PASS reply."""
    return any(_matches_candidate(message, expectation, bot_ids, now) for message in messages)


def _matches_candidate(
    message: Mapping[str, Any], expectation: AttestationExpectation, bot_ids: BotIds, now: datetime
) -> bool:
    author = message.get("author")
    reference = message.get("message_reference")
    if not isinstance(author, Mapping) or not isinstance(reference, Mapping) or message.get("webhook_id") is not None:
        return False
    if author.get("id") != bot_ids.peer_bot_id or author.get("id") == bot_ids.agent_bot_id or author.get("bot") is not True:
        return False
    if message.get("channel_id") != expectation.channel_id:
        return False
    if reference.get("message_id") != expectation.message_id or reference.get("channel_id") != expectation.channel_id:
        return False
    timestamp = message.get("timestamp")
    content = message.get("content")
    if not isinstance(timestamp, str) or not isinstance(content, str):
        return False
    attested_at = parse_timestamp(timestamp)
    attestation = parse_attestation(content)
    if (
        attested_at is None
        or attestation is None
        or attested_at < expectation.requested_at
        or attested_at > now
        or now > attested_at + PEER_ATTESTATION_TTL
    ):
        return False
    return (
        attestation.request == expectation.deploy_nonce
        and attestation.skill == expectation.skill
        and attestation.digest == expectation.digest
        and attestation.verdict == "PASS"
    )
