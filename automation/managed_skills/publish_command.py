"""Argument boundary for local and admin-approved submitted publish inputs."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from automation import skill_gate
from automation.group_roster import RosterError, load_roster, roster_path
from automation.interop.approval_surface import ApprovalKind
from automation.managed_skills.announce import announce_release_from_environment
from automation.managed_skills.manifest import ManifestError, ManagedManifest
from automation.managed_skills.publish_core import PublishConfig, PublishError
from automation.managed_skills.publish_release import publish
from automation.managed_skills.publisher_config import (
    PublisherConfigError,
    PublisherIdentity,
    config_path as publisher_config_path,
    load_publisher_identity,
)
from automation.managed_skills.submission_artifact import validate_submission_artifact
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.managed_skills.submission_source import (
    ApprovedSubmissionConfig,
    SubmissionEvidence,
    SubmissionReviewError,
    open_approved_submission,
)
from automation.managed_skills.submission_transport import (
    DiscordSubmissionTransport,
    discord_api,
)
from automation.skill_gate_surface import GateIdentity, surface_for


@dataclass(frozen=True, slots=True)
class _PublishInput:
    source: Path
    changelog: Path | None
    submitted_manifest: ManagedManifest | None


class _Arguments(argparse.Namespace):
    skill: str
    managed_repo: Path
    skills_src: Path | None
    changelog_file: Path | None
    signing_key: Path | None
    injection_file: Path | None
    discord_token_file: Path | None
    publisher_config: Path | None
    submission_tarball: Path | None
    submission_manifest: Path | None
    roster: Path | None
    submission_evidence: str | None
    approve_evidence: str | None
    stage_publish_request: bool
    publish_evidence: str | None

    def __init__(self) -> None:
        super().__init__()
        self.skill = ""
        self.managed_repo = Path()
        self.skills_src = None
        self.changelog_file = None
        self.signing_key = None
        self.injection_file = None
        self.discord_token_file = None
        self.publisher_config = None
        self.submission_tarball = None
        self.submission_manifest = None
        self.roster = None
        self.submission_evidence = None
        self.approve_evidence = None
        self.stage_publish_request = False
        self.publish_evidence = None


def _token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise SubmissionReviewError(f"discord token file unreadable: {path}") from error
    if not token:
        raise SubmissionReviewError("discord token file is empty")
    return token


@contextmanager
def _publish_input(
    args: _Arguments,
    identity: PublisherIdentity,
) -> Iterator[_PublishInput]:
    submission_values = (
        args.submission_tarball,
        args.submission_manifest,
        args.submission_evidence,
    )
    has_submission = any(value is not None for value in submission_values)
    if has_submission and not all(value is not None for value in submission_values):
        raise SubmissionReviewError("submission tarball, manifest, and evidence are all required")
    if has_submission and (args.skills_src is not None or args.changelog_file is not None):
        raise SubmissionReviewError("submitted and local publish inputs are mutually exclusive")
    if not has_submission:
        if args.changelog_file is None:
            raise PublishError("--changelog-file is required for a local publish input")
        source = args.skills_src or Path(__file__).resolve().parents[2] / "skills" / args.skill
        yield _PublishInput(source, args.changelog_file, None)
        return
    if args.discord_token_file is None:
        raise SubmissionReviewError("--discord-token-file is required for a submitted input")
    tarball = args.submission_tarball
    manifest_path = args.submission_manifest
    evidence = args.submission_evidence
    if tarball is None or manifest_path is None or evidence is None:
        raise SubmissionReviewError("submission tarball, manifest, and evidence are all required")
    artifact = validate_submission_artifact(tarball, manifest_path)
    roster = load_roster(args.roster or roster_path())
    token = _token(args.discord_token_file)
    api = discord_api(token)
    gate_identity = GateIdentity(token, api, skill_gate.GATE_DIR, skill_gate.INTEROP_CONFIG)
    transport = DiscordSubmissionTransport(token, api)
    with open_approved_submission(
        ApprovedSubmissionConfig(
            artifact=artifact,
            evidence=SubmissionEvidence.parse(evidence),
            roster=roster,
            surface=surface_for(ApprovalKind.SKILL_SUBMIT, gate_identity),
            transport=transport,
        )
    ) as approved:
        if approved.manifest.publisher != identity.publisher:
            raise SubmissionReviewError("submitted publisher does not match local publisher config")
        yield _PublishInput(approved.source_dir, None, approved.manifest)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--skill", required=True)
    _ = parser.add_argument("--managed-repo", type=Path, required=True)
    for option in (
        "--skills-src",
        "--changelog-file",
        "--signing-key",
        "--injection-file",
        "--discord-token-file",
        "--publisher-config",
        "--submission-tarball",
        "--submission-manifest",
        "--roster",
    ):
        _ = parser.add_argument(option, type=Path)
    _ = parser.add_argument("--submission-evidence")
    _ = parser.add_argument("--approve-evidence")
    mode = parser.add_mutually_exclusive_group()
    _ = mode.add_argument("--stage-publish-request", action="store_true")
    _ = mode.add_argument("--publish-evidence")
    return parser


def main() -> int:
    args = _Arguments()
    _ = _parser().parse_args(namespace=args)
    signing_key = args.signing_key or (
        Path(value) if (value := os.environ.get("MANAGED_SIGNING_KEY")) else None
    )
    if signing_key is None:
        print("PUBLISH-BLOCK: signing key is required", file=sys.stderr)
        return 2
    try:
        identity = load_publisher_identity(args.publisher_config or publisher_config_path())
    except PublisherConfigError as error:
        print(f"PUBLISH-BLOCK: {error}", file=sys.stderr)
        return 2
    try:
        with _publish_input(args, identity) as selected:
            manifest = publish(
                PublishConfig(
                    skill=args.skill,
                    managed_repo=args.managed_repo,
                    skills_src=selected.source,
                    changelog_file=selected.changelog,
                    signing_key=signing_key,
                    identity=identity,
                    approve_evidence=args.approve_evidence,
                    injection_file=args.injection_file,
                    stage_publish_request=args.stage_publish_request,
                    publish_evidence=args.publish_evidence,
                    discord_token_file=args.discord_token_file,
                    submitted_manifest=selected.submitted_manifest,
                )
            )
    except (
        ManifestError,
        PublishError,
        RosterError,
        SubmissionArtifactError,
        SubmissionReviewError,
    ) as error:
        print(f"PUBLISH-BLOCK: {error}", file=sys.stderr)
        return 1
    if args.stage_publish_request:
        return 0
    print(f"PUBLISHED skill={manifest.skill} tag={manifest.skill}/v{manifest.release_sequence}")
    _ = announce_release_from_environment(manifest, f"{manifest.skill}/v{manifest.release_sequence}")
    return 0
