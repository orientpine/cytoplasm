"""Fail-closed verification of one signed managed-skill release tag."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Protocol, override

from automation.git_tag_signature import (
    SignatureInvocation,
    TagSignatureError,
    TagSignatureRequest,
    verify_tag_signature,
)
from automation.managed_skills.manifest import (
    MAX_SKILL_NAME,
    ManagedManifest,
    ManifestError,
    manifest_digest,
    parse_manifest,
)
from automation.managed_skills.principal import is_publisher_principal
from automation.skill_review import skill_digest

from .fetch import GitRunner

_GIT_TIMEOUT_SECONDS: Final = 120.0
_TAG_NAME: Final = re.compile(r"(?P<skill>managed-[a-z0-9][a-z0-9-]*)/v(?P<sequence>[1-9]\d*)\Z")
_MANIFEST_DIGEST: Final = re.compile(r"^manifest_sha256:([0-9a-f]{64})\s*$", re.MULTILINE)


class VerifyConfig(Protocol):
    """Configuration required to verify a release tag."""

    @property
    def allowed_signers(self) -> Path: ...

    @property
    def publisher_principal(self) -> str: ...

    @property
    def publisher(self) -> str: ...


class VerifyState(Protocol):
    """Read-only per-skill state required for replay and revocation checks."""

    @property
    def highest_sequence(self) -> int: ...

    @property
    def last_verified_digest(self) -> str | None: ...

    @property
    def revoked_digests(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ManagedVerifyError(Exception):
    """A release failed one stable, fail-closed verification category."""

    prefix: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.prefix}: {self.detail}"


@dataclass(frozen=True, slots=True)
class VerifiedRelease:
    """A verified release tree retained in a private temporary directory."""

    skill: str
    sequence: int
    digest: str
    manifest: ManagedManifest
    tree_path: Path
    tag: str


@dataclass(frozen=True, slots=True)
class _Git:
    runner: GitRunner
    environment: dict[str, str]

    def run(self, args: tuple[str, ...], prefix: str) -> subprocess.CompletedProcess[str]:
        """Run one read-only git command or raise its verification category."""
        try:
            result = self.runner(
                list(args),
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ManagedVerifyError(prefix, f"git invocation failed: {error}") from error
        if result.returncode != 0:
            raise ManagedVerifyError(prefix, f"git returned {result.returncode}: {result.stderr.strip()}")
        return result


def _tag_identity(tag: str) -> tuple[str, int]:
    match = _TAG_NAME.fullmatch(tag)
    if match is None or len(match["skill"]) > MAX_SKILL_NAME:
        raise ManagedVerifyError("TAG-MISMATCH", f"invalid release tag: {tag!r}")
    return match["skill"], int(match["sequence"])


def _verify_signature(git: _Git, mirror: Path, tag: str, config: VerifyConfig) -> None:
    expected = config.publisher_principal
    if not is_publisher_principal(expected):
        # No default publisher exists: an install that never declared which
        # principal it trusts must not accept ANY signature.
        raise ManagedVerifyError(
            "WRONG-PRINCIPAL", "no valid publisher principal is configured for this group"
        )
    try:
        verify_tag_signature(
            SignatureInvocation(git.runner, git.environment, _GIT_TIMEOUT_SECONDS),
            TagSignatureRequest(
                repository=mirror,
                tag=tag,
                allowed_signers=config.allowed_signers,
                expected_principal=expected,
            ),
        )
    except TagSignatureError as error:
        raise ManagedVerifyError(error.prefix, error.detail) from error


def _manifest_from_tag(git: _Git, mirror: Path, tag: str, skill: str) -> tuple[str, str]:
    message = git.run(
        ("git", "-C", str(mirror), "tag", "-l", "--format=%(contents)", tag),
        "MANIFEST-BINDING",
    ).stdout
    manifest = git.run(
        ("git", "-C", str(mirror), "show", f"{tag}:manifests/{skill}.json"),
        "MANIFEST-BINDING",
    ).stdout
    return message, manifest


def _parse_manifest(text: str) -> ManagedManifest:
    try:
        return parse_manifest(text)
    except ManifestError as error:
        raise ManagedVerifyError("MANIFEST-SCHEMA", str(error)) from error


def _verify_manifest_binding(message: str, manifest: ManagedManifest) -> None:
    expected_digests = _MANIFEST_DIGEST.findall(message)
    if len(expected_digests) != 1 or expected_digests[0] != manifest_digest(manifest):
        raise ManagedVerifyError("MANIFEST-BINDING", "tag manifest_sha256 does not bind the tagged manifest")


def _verify_publisher(manifest: ManagedManifest, config: VerifyConfig) -> None:
    if manifest.publisher != config.publisher:
        raise ManagedVerifyError(
            "WRONG-PUBLISHER", "manifest publisher differs from managed-sync config"
        )


def _verify_metadata(
    manifest: ManagedManifest,
    tag_skill: str,
    tag_sequence: int,
    state: VerifyState,
    allow_rollback: bool,
) -> None:
    if manifest.skill != tag_skill or manifest.release_sequence != tag_sequence:
        raise ManagedVerifyError("TAG-MISMATCH", "tag skill or sequence differs from manifest")
    if manifest.release_sequence <= state.highest_sequence and not allow_rollback:
        raise ManagedVerifyError("SEQUENCE-REPLAY", "release sequence is not newer than verified state")
    # An owner-requested rollback deliberately breaks forward history; the cron cannot set it.
    # Signature, binding, schema, identity, digest, and revocation checks still gate the release.
    if not allow_rollback and manifest.previous_sha256 != state.last_verified_digest:
        raise ManagedVerifyError("CHAIN-BREAK", "previous manifest digest does not match verified state")


def _extract_tree(git: _Git, mirror: Path, tag: str, skill: str) -> Path:
    try:
        directory = Path(tempfile.mkdtemp(prefix="managed-verify-"))
    except OSError as error:
        raise ManagedVerifyError("DIGEST-MISMATCH", f"temporary extraction failed: {error}") from error
    try:
        archive_path = directory / "release.tar"
        _ = git.run(
            (
                "git",
                "-C",
                str(mirror),
                "archive",
                "--format=tar",
                "--output",
                str(archive_path),
                tag,
                f"skills/{skill}",
            ),
            "DIGEST-MISMATCH",
        )
        _extract_archive(archive_path, directory)
        tree_path = directory / "skills" / skill
        if not tree_path.is_dir():
            raise ManagedVerifyError("DIGEST-MISMATCH", "tag archive does not contain the declared skill tree")
    except ManagedVerifyError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return tree_path


def _extract_archive(archive_path: Path, directory: Path) -> None:
    try:
        with tarfile.open(archive_path) as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile()):
                    raise ManagedVerifyError("DIGEST-MISMATCH", "tag archive contains an unsafe member")
                archive.extract(member, directory, numeric_owner=False, filter="data")
    except (OSError, tarfile.TarError) as error:
        raise ManagedVerifyError("DIGEST-MISMATCH", f"tag archive cannot be extracted: {error}") from error


def _discard_tree(tree_path: Path) -> None:
    shutil.rmtree(tree_path.parent.parent, ignore_errors=True)


def verify_release(
    mirror: Path,
    tag: str,
    config: VerifyConfig,
    state: VerifyState,
    runner: GitRunner = subprocess.run,
    *,
    allow_rollback: bool = False,
) -> VerifiedRelease:
    """Verify one signed release without mutating sync state or live skill storage."""
    tag_skill, tag_sequence = _tag_identity(tag)
    git = _Git(runner=runner, environment=dict(os.environ))
    _verify_signature(git, mirror, tag, config)
    message, text = _manifest_from_tag(git, mirror, tag, tag_skill)
    manifest = _parse_manifest(text)
    _verify_manifest_binding(message, manifest)
    _verify_publisher(manifest, config)
    _verify_metadata(manifest, tag_skill, tag_sequence, state, allow_rollback)
    tree_path = _extract_tree(git, mirror, tag, manifest.skill)
    try:
        digest = skill_digest(tree_path)
    except OSError as error:
        _discard_tree(tree_path)
        raise ManagedVerifyError("DIGEST-MISMATCH", f"skill digest could not be computed: {error}") from error
    if digest != manifest.skill_sha256:
        _discard_tree(tree_path)
        raise ManagedVerifyError("DIGEST-MISMATCH", "tag archive digest differs from manifest")
    if digest in set(state.revoked_digests).union(manifest.revoked_digests):
        _discard_tree(tree_path)
        raise ManagedVerifyError("REVOKED", "source digest is revoked")
    return VerifiedRelease(manifest.skill, manifest.release_sequence, digest, manifest, tree_path, tag)
