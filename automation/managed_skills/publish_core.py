"""Gate-verified publisher for immutable managed-skill git releases."""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, TypeAlias
from automation.typing_compat import override

from automation.managed_skills.manifest import (
    MANAGED_PREFIX,
    MAX_SKILL_NAME,
    ManagedManifest,
    parse_manifest,
)
from automation.managed_skills.publisher_config import PublisherIdentity
from automation.managed_skills.release_metadata import (
    ReleaseMetadataError,
    load_release_metadata,
)
from automation.skill_review import skill_digest


APPROVAL_EVIDENCE: Final = re.compile(r"(?P<message_id>[^:\s]+):(?P<nonce>[0-9a-f]{32})\Z")
_RELEASE_TAG: Final = re.compile(r"v(?P<sequence>[1-9]\d*)\Z")
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class PublishError(Exception): ...


@dataclass(frozen=True, slots=True)
class SelfDigestReclaimError(PublishError):
    """A candidate release attempts to reclaim its own source digest."""

    skill: str
    digest: str

    @override
    def __str__(self) -> str:
        return f"SELF-DIGEST-RECLAIM: {self.skill} cannot reclaim own digest {self.digest}"


@dataclass(frozen=True, slots=True)
class PublishConfig:
    skill: str
    managed_repo: Path
    skills_src: Path
    changelog_file: Path | None
    signing_key: Path
    identity: PublisherIdentity
    approve_evidence: str | None
    injection_file: Path | None
    stage_publish_request: bool = False
    publish_evidence: str | None = None
    discord_token_file: Path | None = None
    submitted_manifest: ManagedManifest | None = None


class Runner(Protocol):
    def __call__(self, args: list[str], /, *, env: dict[str, str], capture_output: bool, text: bool, timeout: float) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True, slots=True)
class PublishProcess:
    runner: Runner
    environment: dict[str, str]

    def run(self, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                list(args),
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=120.0,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PublishError(f"subprocess failed: {' '.join(args)}: {error}") from error

    def require(self, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        result = self.run(args)
        if result.returncode != 0:
            raise PublishError(f"subprocess returned {result.returncode}: {' '.join(args)}")
        return result


def verify_deploy_approval(config: PublishConfig, process: PublishProcess) -> None:
    if config.approve_evidence is not None:
        if APPROVAL_EVIDENCE.fullmatch(config.approve_evidence) is None:
            raise PublishError("approve evidence must be MESSAGE_ID:DEPLOY_NONCE")
        return
    deployment_process = PublishProcess(
        process.runner,
        {**process.environment, "SKILL_SRC_DIR": str(config.skills_src)},
    )
    result = deployment_process.require(
        (str(Path(__file__).resolve().parents[2] / "automation" / "deploy-skill.sh"), config.skill, "--approve-only")
    )
    if sum(APPROVAL_EVIDENCE.fullmatch(line) is not None for line in result.stdout.splitlines()) != 1:
        raise PublishError("approve-only did not emit exactly one MESSAGE_ID:DEPLOY_NONCE line")


def verify_publish_preconditions(config: PublishConfig, environment: Mapping[str, str]) -> None:
    if config.stage_publish_request and config.publish_evidence is not None:
        raise PublishError("stage-publish-request and publish-evidence are mutually exclusive")
    if (config.changelog_file is None) == (config.submitted_manifest is None):
        raise PublishError("provide exactly one local changelog or submitted manifest")
    if "DISCORD_BOT_TOKEN" in environment:
        raise PublishError("publisher refuses agent-runtime environment")
    if not config.skill.startswith(MANAGED_PREFIX) or len(config.skill) > MAX_SKILL_NAME:
        raise PublishError("skill must use the managed prefix and fit the name limit")
    if not config.managed_repo.is_dir():
        raise PublishError(f"managed repository missing: {config.managed_repo}")
    if not (config.skills_src / "SKILL.md").is_file():
        raise PublishError(f"skill source missing SKILL.md: {config.skills_src}")
    if not config.signing_key.is_file():
        raise PublishError(f"signing key path is not a file: {config.signing_key}")
    if config.skills_src.resolve().is_relative_to(config.managed_repo.resolve()):
        raise PublishError("source tree must not be inside the managed repository")
    if any(path.is_symlink() for path in config.skills_src.rglob("*")):
        raise PublishError("source tree contains a symlink")


def _release_state(config: PublishConfig, process: PublishProcess) -> tuple[int, str | None]:
    tags = process.require(
        ("git", "-C", str(config.managed_repo), "tag", "--list", f"{config.skill}/v*")
    ).stdout.splitlines()
    releases = [
        (int(match.group("sequence")), tag)
        for tag in tags
        if (match := _RELEASE_TAG.fullmatch(tag.removeprefix(f"{config.skill}/"))) is not None
    ]
    if not releases:
        return 1, None
    sequence, tag = max(releases)
    previous = parse_manifest(
        process.require(
            ("git", "-C", str(config.managed_repo), "show", f"{tag}:manifests/{config.skill}.json")
        ).stdout
    )
    if previous.skill != config.skill or previous.release_sequence != sequence:
        raise PublishError("previous release tag and manifest disagree")
    return sequence + 1, previous.skill_sha256


def _source_commit(config: PublishConfig, process: PublishProcess) -> str | None:
    result = process.run(("git", "-C", str(config.skills_src.parent.parent), "rev-parse", "HEAD"))
    return result.stdout.strip() if result.returncode == 0 else None


def build_manifest(config: PublishConfig, process: PublishProcess) -> ManagedManifest:
    sequence, previous_sha256 = _release_state(config, process)
    source_digest = skill_digest(config.skills_src)
    submitted = config.submitted_manifest
    if submitted is not None:
        expected = (
            config.identity.publisher,
            config.skill,
            sequence,
            previous_sha256,
            source_digest,
        )
        actual = (
            submitted.publisher,
            submitted.skill,
            submitted.release_sequence,
            submitted.previous_sha256,
            submitted.skill_sha256,
        )
        if actual != expected or submitted.source_commit is None:
            raise PublishError("submitted manifest disagrees with publisher, release state, or source")
        if source_digest in submitted.revoked_digests:
            raise SelfDigestReclaimError(skill=config.skill, digest=source_digest)
        return submitted
    changelog_path = config.changelog_file
    if changelog_path is None:
        raise PublishError("local release metadata is missing")
    try:
        changelog = load_release_metadata(changelog_path)
    except ReleaseMetadataError as error:
        raise PublishError(str(error)) from error
    payload: dict[str, JsonValue] = {
        "schema_version": 1,
        "publisher": config.identity.publisher,
        "skill": config.skill,
        "release_sequence": sequence,
        "source_commit": _source_commit(config, process),
        "skill_sha256": source_digest,
        "previous_sha256": previous_sha256,
        "changelog": changelog.changelog,
        "breaking": changelog.breaking,
        "compatibility": changelog.compatibility,
        "migration": changelog.migration,
        "revoked_digests": list(changelog.revoked_digests),
    }
    manifest = parse_manifest(json.dumps(payload))
    if source_digest in manifest.revoked_digests:
        raise SelfDigestReclaimError(skill=config.skill, digest=source_digest)
    return manifest
