"""Shared SSH-signed Git tag verification and release-ref parsing."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, override


_VERIFIED_PRINCIPAL = re.compile(r'Good "git" signature for (\S+) with ')
_MAX_DETACHED_INPUT: Final = 16_384
OBJECT_ID: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
HEAD_REF: Final = "refs/heads/main"
_TAG_PREFIX: Final = "refs/tags/"
_PEELED_SUFFIX: Final = "^{}"


class GitRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


class DetachedSignatureRunner(Protocol):
    def __call__(
        self,
        args: tuple[str, ...],
        /,
        *,
        env: dict[str, str],
        input: bytes,
        pass_fds: tuple[int, ...],
        capture_output: bool,
        check: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]: ...


@dataclass(frozen=True, slots=True)
class SignatureInvocation:
    runner: GitRunner
    environment: dict[str, str]
    timeout: float


@dataclass(frozen=True, slots=True)
class TagSignatureRequest:
    repository: Path
    tag: str
    allowed_signers: Path
    expected_principal: str


@dataclass(frozen=True, slots=True)
class DetachedSignatureInvocation:
    runner: DetachedSignatureRunner
    environment: dict[str, str]
    timeout: float


@dataclass(frozen=True, slots=True)
class DetachedSignatureRequest:
    message: bytes
    signature: bytes
    allowed_signers: bytes
    expected_principal: str
    namespace: str


@dataclass(frozen=True, slots=True)
class TagSignatureError(Exception):
    prefix: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.prefix}: {self.detail}"


@dataclass(frozen=True, slots=True)
class RemoteRefError(Exception):
    prefix: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.prefix}: {self.detail}"


@dataclass(frozen=True, slots=True)
class RemoteReleaseTag:
    name: str
    object_sha: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class RemoteRelease:
    tags: tuple[RemoteReleaseTag, ...]


def parse_remote_release_refs(text: str) -> RemoteRelease:
    head_sha = ""
    tag_objects: dict[str, str] = {}
    peeled_commits: dict[str, str] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or OBJECT_ID.fullmatch(fields[0]) is None:
            raise RemoteRefError("REMOTE-REFS", "origin returned a malformed ref advertisement")
        sha, ref = fields
        if ref == HEAD_REF:
            if head_sha and head_sha != sha:
                raise RemoteRefError("REMOTE-REFS", "origin advertised multiple main targets")
            head_sha = sha
        elif ref.startswith(_TAG_PREFIX):
            tag_ref = ref.removeprefix(_TAG_PREFIX)
            if tag_ref.endswith(_PEELED_SUFFIX):
                peeled_commits[tag_ref.removesuffix(_PEELED_SUFFIX)] = sha
            else:
                tag_objects[tag_ref] = sha
    if not head_sha:
        raise RemoteRefError("REMOTE-UNRESOLVED", "origin/main is absent or unreachable")
    tags = tuple(
        sorted(
            (
                RemoteReleaseTag(name, object_sha, peeled_commits[name])
                for name, object_sha in tag_objects.items()
                if peeled_commits.get(name) == head_sha
            ),
            key=lambda tag: tag.name,
            reverse=True,
        )
    )
    if not tags:
        raise RemoteRefError(
            "UNSIGNED-HEAD",
            "origin/main is not the commit of an annotated release tag",
        )
    return RemoteRelease(tags=tags)


def verify_tag_signature(invocation: SignatureInvocation, request: TagSignatureRequest) -> None:
    try:
        result = invocation.runner(
            [
                "git",
                "-C",
                str(request.repository),
                "-c",
                f"gpg.ssh.allowedSignersFile={request.allowed_signers}",
                "verify-tag",
                request.tag,
            ],
            env=invocation.environment,
            capture_output=True,
            text=True,
            timeout=invocation.timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TagSignatureError("BAD-SIGNATURE", f"git invocation failed: {error}") from error
    if result.returncode != 0:
        raise TagSignatureError(
            "BAD-SIGNATURE",
            f"git returned {result.returncode}: {result.stderr.strip()}",
        )
    principal = _VERIFIED_PRINCIPAL.search(f"{result.stdout}\n{result.stderr}")
    if principal is None or principal.group(1) != request.expected_principal:
        raise TagSignatureError(
            "WRONG-PRINCIPAL",
            f"verified principal is not {request.expected_principal}",
        )


def _write_pipe(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(descriptor, payload[offset:])


def verify_detached_signature(
    invocation: DetachedSignatureInvocation,
    request: DetachedSignatureRequest,
) -> bool:
    """Verify one SSHSIG message without materializing an agent-writable trust file."""
    if not request.message or not request.signature or not request.allowed_signers:
        return False
    if len(request.signature) > _MAX_DETACHED_INPUT or len(request.allowed_signers) > _MAX_DETACHED_INPUT:
        return False
    allowed_read, allowed_write = os.pipe()
    signature_read, signature_write = os.pipe()
    try:
        _write_pipe(allowed_write, request.allowed_signers)
        _write_pipe(signature_write, request.signature)
        os.close(allowed_write)
        allowed_write = -1
        os.close(signature_write)
        signature_write = -1
        result = invocation.runner(
            (
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                f"/proc/self/fd/{allowed_read}",
                "-I",
                request.expected_principal,
                "-n",
                request.namespace,
                "-s",
                f"/proc/self/fd/{signature_read}",
            ),
            env=invocation.environment,
            input=request.message,
            pass_fds=(allowed_read, signature_read),
            capture_output=True,
            check=False,
            timeout=invocation.timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        for descriptor in (allowed_read, allowed_write, signature_read, signature_write):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    continue
    expected = f'Good "{request.namespace}" signature for {request.expected_principal} with '
    return result.returncode == 0 and expected.encode("utf-8") in result.stdout
