"""Package one verified personal commit as an immutable managed-skill candidate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from automation.managed_skills.manifest import (
    ManagedManifest,
    canonical_json,
    parse_manifest,
)
from automation.managed_skills.release_metadata import ReleaseMetadata
from automation.managed_skills.submission_archive import extract_archive, write_tarball
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.skill_review import skill_digest

_COMMIT: Final = re.compile(r"[0-9a-f]{40}\Z")
_FRONTMATTER_NAME: Final = re.compile(r"^name:\s*(?P<value>[^\r\n]+?)\s*$", re.MULTILINE)


class CommandRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        /,
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True, slots=True)
class SubmissionPackageConfig:
    personal_repo: Path
    managed_skill: str
    publisher: str
    release_sequence: int
    previous_sha256: str | None
    metadata: ReleaseMetadata
    output_dir: Path


@dataclass(frozen=True, slots=True)
class SubmissionArtifact:
    tarball_path: Path
    manifest_path: Path
    manifest: ManagedManifest
    tarball_sha256: str
    manifest_sha256: str

    @property
    def attachment_names(self) -> tuple[str, str]:
        return self.tarball_path.name, self.manifest_path.name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise SubmissionArtifactError(f"submission artifact cannot be read: {path}") from error
    return digest.hexdigest()


def _run(
    runner: CommandRunner,
    args: list[str],
    operation: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(args, capture_output=True, text=True, timeout=120.0)
    except (OSError, subprocess.SubprocessError) as error:
        raise SubmissionArtifactError(f"{operation} invocation failed: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise SubmissionArtifactError(f"{operation} rejected source: {detail}")
    return result


def verified_personal_head(
    repo: Path,
    *,
    approved_head: str = "",
    runner: CommandRunner = subprocess.run,
) -> str:
    """Run W-F4-A's shell guard verbatim and return its committed branch HEAD."""
    guard = Path(__file__).resolve().parents[1] / "deploy_provenance.sh"
    command = 'source "$1"; personal_provenance_check "$2" "$3"'
    result = _run(
        runner,
        ["bash", "-c", command, "personal-submission", str(guard), str(repo), approved_head],
        "personal_provenance_check",
    )
    head = result.stdout.strip()
    if _COMMIT.fullmatch(head) is None:
        raise SubmissionArtifactError("personal_provenance_check returned an unsupported commit id")
    return head


def _rewrite_name(source: Path, personal_name: str, managed_skill: str) -> None:
    path = source / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SubmissionArtifactError("personal skill has no readable SKILL.md") from error
    closing = text.find("\n---\n", 4) if text.startswith("---\n") else -1
    if closing < 0:
        raise SubmissionArtifactError("personal SKILL.md has invalid frontmatter")
    frontmatter = text[: closing + 1]
    matches = tuple(_FRONTMATTER_NAME.finditer(frontmatter))
    if len(matches) != 1:
        raise SubmissionArtifactError("personal SKILL.md must declare exactly one top-level name")
    declared = matches[0].group("value").strip().strip("\"'")
    if declared != personal_name:
        raise SubmissionArtifactError("personal SKILL.md name does not match its repository")
    start, end = matches[0].span()
    updated = f"{frontmatter[:start]}name: {managed_skill}{frontmatter[end:]}{text[closing + 1:]}"
    _ = path.write_text(updated, encoding="utf-8")


def _manifest(config: SubmissionPackageConfig, head: str, source_digest: str) -> ManagedManifest:
    metadata = config.metadata
    payload = {
        "schema_version": 1,
        "publisher": config.publisher,
        "skill": config.managed_skill,
        "release_sequence": config.release_sequence,
        "source_commit": head,
        "skill_sha256": source_digest,
        "previous_sha256": config.previous_sha256,
        "compatibility": metadata.compatibility,
        "breaking": metadata.breaking,
        "revoked_digests": list(metadata.revoked_digests),
        "changelog": metadata.changelog,
        "migration": metadata.migration,
    }
    return parse_manifest(json.dumps(payload))


def _write_or_match(path: Path, payload: bytes) -> None:
    try:
        if path.exists():
            if path.read_bytes() != payload:
                raise SubmissionArtifactError(f"submission output already differs: {path}")
            return
        with path.open("xb") as handle:
            _ = handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise SubmissionArtifactError(f"cannot write submission output: {path}") from error


def package_personal_skill(
    config: SubmissionPackageConfig,
    runner: CommandRunner = subprocess.run,
) -> SubmissionArtifact:
    """Build a canonical manifest and tarball only from one verified personal commit."""
    head = verified_personal_head(config.personal_repo, runner=runner)
    with tempfile.TemporaryDirectory(prefix="personal-submission-") as temporary:
        work = Path(temporary)
        source_archive = work / "source.tar"
        _ = verified_personal_head(config.personal_repo, approved_head=head, runner=runner)
        _ = _run(
            runner,
            [
                "git",
                "-C",
                str(config.personal_repo),
                "archive",
                "--format=tar",
                "--output",
                str(source_archive),
                head,
            ],
            "git archive",
        )
        source = work / "source"
        source.mkdir()
        extract_archive(source_archive, source)
        _rewrite_name(source, config.personal_repo.name, config.managed_skill)
        manifest = _manifest(config, head, skill_digest(source))
        temporary_tarball = work / "submission.tar.gz"
        write_tarball(source, config.managed_skill, temporary_tarball)
        tarball_sha256 = _sha256(temporary_tarball)
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        config.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        tarball_path = config.output_dir / f"{config.managed_skill}-{head[:12]}.tar.gz"
        manifest_path = config.output_dir / f"{config.managed_skill}.manifest.json"
        _write_or_match(tarball_path, temporary_tarball.read_bytes())
        _write_or_match(manifest_path, manifest_bytes)
    return validate_submission_artifact(
        tarball_path,
        manifest_path,
        expected_tarball_sha256=tarball_sha256,
    )


def validate_submission_artifact(
    tarball_path: Path,
    manifest_path: Path,
    *,
    expected_tarball_sha256: str | None = None,
) -> SubmissionArtifact:
    """Parse both immutable inputs and reject any archive/manifest disagreement.

    **Without an explicit ``expected_tarball_sha256`` this is a consistency
    check, not an authenticity check.** The pin comparison below is skipped when
    it is ``None``, and everything that remains is checked against the submitted
    manifest - which arrived from the same place as the tarball - so a submitter
    who replaces both files consistently passes. Authenticity comes instead from
    the group admin's approval: ``submission_source.open_approved_submission``
    requires the envelope rebuilt from these artifacts to equal the Discord
    message the admin reacted to.

    So an unpinned result must never be treated as trusted or reach a publish
    path alone. Pin at every site that knows which bytes it means; the sole
    exemption is the intake in ``publish_command._publish_input``, which has
    nothing to pin to yet. ``tests/unit/test_submission_pin_conformance.py``
    enforces that boundary - a new caller must be classified there.
    """
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SubmissionArtifactError(f"submission manifest cannot be read: {manifest_path}") from error
    manifest = parse_manifest(manifest_text)
    if manifest_text != canonical_json(manifest):
        raise SubmissionArtifactError("submission manifest is not canonical JSON")
    tarball_sha256 = _sha256(tarball_path)
    if expected_tarball_sha256 is not None and tarball_sha256 != expected_tarball_sha256:
        raise SubmissionArtifactError("submission tarball sha256 differs from its pinned value")
    artifact = SubmissionArtifact(
        tarball_path=tarball_path,
        manifest_path=manifest_path,
        manifest=manifest,
        tarball_sha256=tarball_sha256,
        manifest_sha256=hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
    )
    with extract_submission(artifact, revalidate=False) as source:
        if skill_digest(source) != manifest.skill_sha256:
            raise SubmissionArtifactError("submission source digest differs from its manifest")
    return artifact


@contextmanager
def extract_submission(
    artifact: SubmissionArtifact,
    *,
    revalidate: bool = True,
) -> Iterator[Path]:
    """Yield the sole managed skill root from a safely extracted pinned tarball."""
    if revalidate:
        current = validate_submission_artifact(
            artifact.tarball_path,
            artifact.manifest_path,
            expected_tarball_sha256=artifact.tarball_sha256,
        )
        if current.manifest_sha256 != artifact.manifest_sha256:
            raise SubmissionArtifactError("submission manifest sha256 differs from its pinned value")
    with tempfile.TemporaryDirectory(prefix="approved-submission-") as temporary:
        root = Path(temporary)
        extract_archive(artifact.tarball_path, root)
        entries = tuple(root.iterdir())
        source = root / artifact.manifest.skill
        if entries != (source,) or not source.is_dir() or not (source / "SKILL.md").is_file():
            raise SubmissionArtifactError("submission tarball must contain exactly its managed skill root")
        yield source
