from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Protocol


REVIEWER: Final = "peer-sandbox-v1"
SIGNATURE_NAMESPACE: Final = "autophagy-peer-attest"
SIGNATURE_PRINCIPAL: Final = "peer-attest@autophagy"
_SKILL: Final = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")
_NONCE: Final = re.compile(r"[0-9a-f]{32}")
_RFC3339: Final = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z"
_SNOWFLAKE: Final = r"[0-9]{1,20}"
_SIGNED_RECORD: Final = re.compile(
    "".join(
        (
            rf"\A(?P<preimage>autophagy-peer-attest-v1\n",
            rf"request=(?P<request>{_NONCE.pattern})\n",
            rf"skill=(?P<skill>{_SKILL.pattern})\n",
            rf"sha256=(?P<digest>{_DIGEST.pattern})\n",
            rf"verdict=(?P<verdict>PASS|FAIL)\n",
            rf"reviewer={REVIEWER}\n",
            rf"attested_at=(?P<attested_at>{_RFC3339})\n",
            rf"approval_channel=(?P<channel>{_SNOWFLAKE})\n",
            rf"approval_message=(?P<message>{_SNOWFLAKE})\n)",
            r"(?P<signature>-----BEGIN SSH SIGNATURE-----\n",
            r"(?:[A-Za-z0-9+/=]+\n)+",
            r"-----END SSH SIGNATURE-----\n)\Z",
        )
    )
)


@dataclass(frozen=True, slots=True)
class SignedAttestationPayload:
    request: str
    skill: str
    digest: str
    verdict: Literal["PASS", "FAIL"]
    attested_at: datetime
    approval_channel: str
    approval_message: str


@dataclass(frozen=True, slots=True)
class SignedAttestationRecord:
    payload: SignedAttestationPayload
    preimage: bytes
    signature: bytes


class SignedAttestationVerifier(Protocol):
    def verify(self, preimage: bytes, signature: bytes) -> bool: ...


@dataclass(frozen=True, slots=True)
class SignedAttestationExpectation:
    channel: str
    message: str
    nonce: str
    skill: str
    digest: str
    requested_at: datetime
    now: datetime
    ttl: timedelta


class SignedAttestationFormatError(RuntimeError):
    pass


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        return ""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def signed_attestation_preimage(payload: SignedAttestationPayload) -> bytes:
    return (
        "autophagy-peer-attest-v1\n"
        f"request={payload.request}\n"
        f"skill={payload.skill}\n"
        f"sha256={payload.digest}\n"
        f"verdict={payload.verdict}\n"
        f"reviewer={REVIEWER}\n"
        f"attested_at={_timestamp(payload.attested_at)}\n"
        f"approval_channel={payload.approval_channel}\n"
        f"approval_message={payload.approval_message}\n"
    ).encode("utf-8")


def sign_signed_attestation(private_key: Path, payload: SignedAttestationPayload) -> str | None:
    try:
        key_stat = private_key.lstat()
        parent_stat = private_key.parent.lstat()
    except OSError:
        return None
    if (
        not stat.S_ISREG(key_stat.st_mode)
        or not stat.S_ISDIR(parent_stat.st_mode)
        or key_stat.st_uid != os.geteuid()
        or parent_stat.st_uid != os.geteuid()
        or stat.S_IMODE(key_stat.st_mode) != 0o600
        or parent_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        return None
    try:
        completed = subprocess.run(
            (
                "ssh-keygen",
                "-Y",
                "sign",
                "-f",
                str(private_key),
                "-n",
                SIGNATURE_NAMESPACE,
            ),
            input=signed_attestation_preimage(payload),
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return completed.stdout.decode("ascii")
    except UnicodeDecodeError:
        return None


def format_signed_attestation(payload: SignedAttestationPayload, signature: str) -> str:
    normalized = signature if signature.endswith("\n") else f"{signature}\n"
    record = signed_attestation_preimage(payload).decode("utf-8") + normalized
    if parse_signed_attestation(record) is None:
        raise SignedAttestationFormatError
    return record


def parse_signed_attestation(blob: str) -> SignedAttestationRecord | None:
    matched = _SIGNED_RECORD.fullmatch(blob)
    if matched is None:
        return None
    try:
        attested_at = datetime.strptime(
            matched.group("attested_at"),
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=UTC)
    except ValueError:
        return None
    verdict: Literal["PASS", "FAIL"] = (
        "PASS" if matched.group("verdict") == "PASS" else "FAIL"
    )
    payload = SignedAttestationPayload(
        request=matched.group("request"),
        skill=matched.group("skill"),
        digest=matched.group("digest"),
        verdict=verdict,
        attested_at=attested_at,
        approval_channel=matched.group("channel"),
        approval_message=matched.group("message"),
    )
    return SignedAttestationRecord(
        payload=payload,
        preimage=matched.group("preimage").encode("utf-8"),
        signature=matched.group("signature").encode("ascii"),
    )


def valid_signed_attestation(
    blob: str,
    expectation: SignedAttestationExpectation,
    verifier: SignedAttestationVerifier,
) -> bool:
    record = parse_signed_attestation(blob)
    if record is None:
        return False
    payload = record.payload
    if (
        payload.request != expectation.nonce
        or payload.skill != expectation.skill
        or payload.digest != expectation.digest
        or payload.verdict != "PASS"
        or payload.approval_channel != expectation.channel
        or payload.approval_message != expectation.message
        or payload.attested_at < expectation.requested_at
        or payload.attested_at > expectation.now
        or expectation.now > payload.attested_at + expectation.ttl
    ):
        return False
    return verifier.verify(record.preimage, record.signature)
