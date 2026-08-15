"""Typed OpenSSH allowed-signers planning shared by both D8 trust domains."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

UPDATE_ALLOWED_SIGNERS_PATH: Final = Path("/etc/autophagy/update-allowed-signers")
MANAGED_SKILLS_ALLOWED_SIGNERS_PATH: Final = Path(
    "/etc/autophagy/managed-skills-allowed-signers"
)
DEFAULT_UPDATE_TRUST_PRINCIPAL: Final = "update-trust@autophagy"
GIT_SIGNATURE_NAMESPACE: Final = "git"
GROUP_SIGNATURE_NAMESPACES: Final = "git,autophagy-roster"
REQUIRED_MODE: Final = 0o644
ROOT_UID: Final = 0
ROOT_GID: Final = 0
SUPPORTED_KEY_ALGORITHMS: Final = frozenset(
    {
        "ecdsa-sha2-nistp256",
        "sk-ssh-ed25519@openssh.com",
        "ssh-ed25519",
        "ssh-rsa",
    }
)


@dataclass(frozen=True, slots=True)
class TrustKeyError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class PublicKey:
    algorithm: str
    material: str
    comment: str

    def line(self) -> str:
        suffix = f" {self.comment}" if self.comment else ""
        return f"{self.algorithm} {self.material}{suffix}"


@dataclass(frozen=True, slots=True)
class SignerEntry:
    principal: str
    key: PublicKey
    namespaces: str = GIT_SIGNATURE_NAMESPACE

    def line(self) -> str:
        scope = f' namespaces="{self.namespaces}"' if self.namespaces else ""
        return f"{self.principal}{scope} {self.key.line()}"


@dataclass(frozen=True, slots=True)
class SignerTarget:
    path: Path
    forbidden_path: Path
    check_prefix: str
    header: str
    comparison_guidance: str
    mismatch_guidance: str


UPDATE_TRUST_TARGET: Final = SignerTarget(
    path=UPDATE_ALLOWED_SIGNERS_PATH,
    forbidden_path=MANAGED_SKILLS_ALLOWED_SIGNERS_PATH,
    check_prefix="trust-key",
    header=(
        "# autophagy UPDATE TRUST key — verifies public-repo release tags (plan D8).\n"
        "# NOT the group skill signing key (/etc/autophagy/managed-skills-allowed-signers).\n"
        "# Installed by automation/install/trust_key_bootstrap.py; root:root 0644.\n"
    ),
    comparison_guidance=(
        "공개 repo README·릴리스 노트의 공지값과 직접 대조한다 "
        "(--expect-fingerprint로 기계 대조 가능)"
    ),
    mismatch_guidance="설치기가 변조됐을 수 있으니 업데이트를 진행하지 말고 유지보수자에게 확인한다",
)
GROUP_SKILL_TRUST_TARGET: Final = SignerTarget(
    path=MANAGED_SKILLS_ALLOWED_SIGNERS_PATH,
    forbidden_path=UPDATE_ALLOWED_SIGNERS_PATH,
    check_prefix="group-skill-trust",
    header=(
        "# autophagy GROUP signing key — verifies managed-skill tags and roster snapshots.\n"
        "# NOT the update trust key (/etc/autophagy/update-allowed-signers).\n"
        "# Verify the fingerprint out-of-band; NEVER use the group Discord channel.\n"
        "# Installed by automation/install; root:root 0644.\n"
    ),
    comparison_guidance=(
        "관리자에게서 대역외로 받은 값과 직접 대조한다 "
        "(GROUP-DISCORD-FORBIDDEN: 그룹 Discord 채널 사용 금지)"
    ),
    mismatch_guidance="그룹 스킬을 가져오지 말고 관리자에게 대역외 채널로 다시 확인한다",
)


@dataclass(frozen=True, slots=True)
class SignerInstallRequest:
    key_text: str
    principal: str
    target: SignerTarget
    namespaces: str = GIT_SIGNATURE_NAMESPACE


@dataclass(frozen=True, slots=True)
class InstallPlan:
    path: Path
    content: str
    mode: int
    uid: int
    gid: int
    principal: str
    fingerprint: str

    def describe(self) -> str:
        owner = f"{self.uid}:{self.gid}"
        return (
            f"path={self.path}\n"
            f"owner={owner} mode={self.mode:04o}\n"
            f"principal={self.principal}\n"
            f"fingerprint={self.fingerprint}\n"
            f"--- content ---\n{self.content}"
        )


def parse_public_key(text: str) -> PublicKey:
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if len(lines) != 1:
        raise TrustKeyError(
            f"BUNDLED-KEY-NOT-SINGLE: 공개키 줄이 정확히 1개여야 한다 (발견 {len(lines)})"
        )
    fields = lines[0].split(maxsplit=2)
    if len(fields) < 2:
        raise TrustKeyError(
            "BUNDLED-KEY-MALFORMED: '<algorithm> <base64> [comment]' 형식이 아니다"
        )
    algorithm, material = fields[0], fields[1]
    comment = fields[2] if len(fields) == 3 else ""
    if algorithm not in SUPPORTED_KEY_ALGORITHMS:
        supported = ", ".join(sorted(SUPPORTED_KEY_ALGORITHMS))
        raise TrustKeyError(
            f"BUNDLED-KEY-ALGORITHM: {algorithm}는 지원 대상이 아니다 (허용: {supported})"
        )
    blob = _decode_material(material)
    if _blob_algorithm(blob) != algorithm:
        raise TrustKeyError(
            "BUNDLED-KEY-MISMATCH: 선언한 algorithm과 키 blob 내부 이름이 다르다"
        )
    return PublicKey(algorithm, material, comment)


def _decode_material(material: str) -> bytes:
    try:
        return base64.b64decode(material, validate=True)
    except (binascii.Error, ValueError) as error:
        raise TrustKeyError(
            "BUNDLED-KEY-BASE64: 키 본문이 유효한 base64가 아니다"
        ) from error


def _blob_algorithm(blob: bytes) -> str:
    if len(blob) < 4:
        raise TrustKeyError("BUNDLED-KEY-TRUNCATED: 키 blob이 너무 짧다")
    length = int.from_bytes(blob[:4], "big")
    if length == 0 or len(blob) < 4 + length:
        raise TrustKeyError(
            "BUNDLED-KEY-TRUNCATED: 키 blob의 algorithm 필드가 잘렸다"
        )
    try:
        return blob[4 : 4 + length].decode("ascii")
    except UnicodeDecodeError as error:
        raise TrustKeyError(
            "BUNDLED-KEY-TRUNCATED: algorithm 필드가 ASCII가 아니다"
        ) from error


def fingerprint(key: PublicKey) -> str:
    digest = hashlib.sha256(_decode_material(key.material)).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def fingerprints_match(actual: str, expected: str) -> bool:
    return hmac.compare_digest(actual.strip(), expected.strip())


def render_allowed_signers(
    entries: Sequence[SignerEntry], target: SignerTarget
) -> str:
    if not entries:
        raise TrustKeyError(
            "SIGNERS-EMPTY: 빈 allowed_signers는 모든 릴리스를 거부하게 만든다"
        )
    return target.header + "".join(f"{entry.line()}\n" for entry in entries)


def parse_allowed_signers(text: str) -> tuple[SignerEntry, ...]:
    entries: list[SignerEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        principal, _, remainder = line.partition(" ")
        if not principal or not remainder:
            raise TrustKeyError(
                "SIGNERS-MALFORMED: '<principal> [options] <key>' 형식이 아니다"
            )
        namespaces = ""
        if remainder.startswith('namespaces="'):
            scope, _, rest = remainder.removeprefix('namespaces="').partition('"')
            namespaces, remainder = scope, rest.strip()
        entries.append(
            SignerEntry(principal, parse_public_key(remainder), namespaces)
        )
    if not entries:
        raise TrustKeyError("SIGNERS-EMPTY: 서명자 레코드가 없다")
    return tuple(entries)


def plan_signer_install(request: SignerInstallRequest) -> InstallPlan:
    if request.target.path == request.target.forbidden_path:
        raise TrustKeyError(
            "WRONG-FILE: 업데이트 신뢰키와 그룹 스킬 서명키는 서로 다른 파일에 설치해야 한다(D8)"
        )
    if not request.principal or any(
        character.isspace() for character in request.principal
    ):
        raise TrustKeyError(
            "PRINCIPAL-INVALID: principal은 공백 없는 한 토큰이어야 한다"
        )
    key = parse_public_key(request.key_text)
    entry = SignerEntry(request.principal, key, request.namespaces)
    return InstallPlan(
        path=request.target.path,
        content=render_allowed_signers((entry,), request.target),
        mode=REQUIRED_MODE,
        uid=ROOT_UID,
        gid=ROOT_GID,
        principal=request.principal,
        fingerprint=fingerprint(key),
    )
