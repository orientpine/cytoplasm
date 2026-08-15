"""Fail-closed filesystem installation and validation for allowed-signers files."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from automation.install.allowed_signers import (
    REQUIRED_MODE,
    ROOT_GID,
    ROOT_UID,
    InstallPlan,
    SignerTarget,
    TrustKeyError,
    fingerprint,
    fingerprints_match,
    parse_allowed_signers,
)
from automation.install.checks import CheckResult, Status

TRUST_DIRECTORY_MODE: Final = 0o755
_UNTRUSTED_WRITE_BITS: Final = stat.S_IWGRP | stat.S_IWOTH
ROOT_UIDS: Final = frozenset({ROOT_UID})
ROOT_GIDS: Final = frozenset({ROOT_GID})


class TrustKeyFilesystem(Protocol):
    def lstat(self, path: Path) -> os.stat_result: ...

    def read_text(self, path: Path) -> str: ...

    def write_atomic(self, path: Path, content: str, mode: int) -> None: ...

    def set_ownership(self, path: Path, uid: int, gid: int) -> None: ...


@dataclass(frozen=True, slots=True)
class RealFilesystem:
    def lstat(self, path: Path) -> os.stat_result:
        return path.lstat()

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_atomic(self, path: Path, content: str, mode: int) -> None:
        if not path.parent.exists():
            path.parent.mkdir(parents=True)
            os.chmod(path.parent, TRUST_DIRECTORY_MODE)
        descriptor, temporary = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
        )
        temporary_path = Path(temporary)
        replaced = False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                _ = stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, mode)
            os.replace(temporary_path, path)
            replaced = True
        finally:
            if not replaced:
                temporary_path.unlink(missing_ok=True)

    def set_ownership(self, path: Path, uid: int, gid: int) -> None:
        os.chown(path, uid, gid)


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    path: Path
    target: SignerTarget
    expected_fingerprint: str | None = None
    trusted_uids: frozenset[int] = ROOT_UIDS
    trusted_gids: frozenset[int] = ROOT_GIDS


def apply_install(plan: InstallPlan, filesystem: TrustKeyFilesystem) -> None:
    filesystem.write_atomic(plan.path, plan.content, plan.mode)
    filesystem.set_ownership(plan.path, plan.uid, plan.gid)


def verify_installed(
    request: VerificationRequest,
    filesystem: TrustKeyFilesystem,
) -> tuple[CheckResult, ...]:
    try:
        file_stat = filesystem.lstat(request.path)
        parent_stat = filesystem.lstat(request.path.parent)
    except OSError:
        return (
            CheckResult(
                f"{request.target.check_prefix}.file",
                Status.FAIL,
                f"TRUST-KEY-MISSING: {request.path}가 없어 새 릴리스 검증을 fail-closed 거부한다",
            ),
        )
    return (
        _check_file_shape(request, file_stat, parent_stat),
        _check_content(request, filesystem),
    )


def _check_file_shape(
    request: VerificationRequest,
    file_stat: os.stat_result,
    parent_stat: os.stat_result,
) -> CheckResult:
    name = f"{request.target.check_prefix}.file"
    path = request.path
    if not stat.S_ISREG(file_stat.st_mode):
        return CheckResult(
            name,
            Status.FAIL,
            f"TRUST-KEY-NOT-A-FILE: {path}가 정규 파일이 아니다(심링크 포함)",
        )
    if not stat.S_ISDIR(parent_stat.st_mode):
        return CheckResult(
            name,
            Status.FAIL,
            f"TRUST-KEY-PARENT: {path.parent}가 디렉터리가 아니다",
        )
    if (
        file_stat.st_uid not in request.trusted_uids
        or file_stat.st_gid not in request.trusted_gids
    ):
        return CheckResult(
            name,
            Status.FAIL,
            f"TRUST-KEY-WRONG-OWNER: {file_stat.st_uid}:{file_stat.st_gid} — root:root이어야 한다",
        )
    if parent_stat.st_uid not in request.trusted_uids:
        return CheckResult(
            name,
            Status.FAIL,
            f"TRUST-KEY-PARENT-OWNER: {path.parent}가 root 소유가 아니다",
        )
    if (
        file_stat.st_mode & _UNTRUSTED_WRITE_BITS
        or parent_stat.st_mode & _UNTRUSTED_WRITE_BITS
    ):
        return CheckResult(
            name,
            Status.FAIL,
            "TRUST-KEY-WRITABLE: 파일 또는 부모에 group/other 쓰기 비트가 있다 — "
            + "신뢰 근원을 비-root가 바꿀 수 있으면 서명 검증 전체가 무의미해진다",
        )
    actual_mode = stat.S_IMODE(file_stat.st_mode)
    if actual_mode != REQUIRED_MODE:
        return CheckResult(
            name,
            Status.FAIL,
            f"TRUST-KEY-WRONG-MODE: {actual_mode:04o} — {REQUIRED_MODE:04o}이어야 한다",
        )
    return CheckResult(
        name,
        Status.PASS,
        f"{path} root:root {REQUIRED_MODE:04o} 정규 파일",
    )


def _check_content(
    request: VerificationRequest,
    filesystem: TrustKeyFilesystem,
) -> CheckResult:
    name = f"{request.target.check_prefix}.fingerprint"
    try:
        entries = parse_allowed_signers(filesystem.read_text(request.path))
    except (OSError, TrustKeyError) as error:
        return CheckResult(
            name,
            Status.FAIL,
            f"TRUST-KEY-UNREADABLE: {error}",
        )
    actual = ", ".join(
        f"{entry.principal}={fingerprint(entry.key)}" for entry in entries
    )
    expected = request.expected_fingerprint
    if expected is None:
        return CheckResult(
            name,
            Status.WARN,
            f"설치된 지문 {actual} — {request.target.comparison_guidance}",
        )
    if any(fingerprints_match(fingerprint(entry.key), expected) for entry in entries):
        return CheckResult(
            name,
            Status.PASS,
            f"공지 지문과 일치 — {expected}",
        )
    return CheckResult(
        name,
        Status.FAIL,
        f"TRUST-KEY-FINGERPRINT-MISMATCH: 설치본 {actual} != 공지 {expected}. "
        + request.target.mismatch_guidance,
    )
