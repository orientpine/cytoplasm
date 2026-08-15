from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from automation.interop.approval_lifecycle import Outcome
from automation.interop.approval_surface import (
    ApprovalKind,
    ChannelFacts,
)
from automation.managed_skills.release_metadata import ReleaseMetadata
from automation.managed_skills.submission_approval import (
    SubmissionApprovalConfig,
    request_submission_approval,
)
from automation.managed_skills.submission_artifact import (
    SubmissionPackageConfig,
    package_personal_skill,
)
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.managed_skills.submission_message import parse_submission_message
from automation.managed_skills.submission_transport import (
    DiscordSubmissionMessage,
    DiscordUser,
    SubmissionAttachment,
)
from automation.skill_gate_surface import SupplyChainSurface


class _Directory:
    def owner_dm(self) -> str:
        raise AssertionError("submission review must not resolve an owner DM")

    def skill_approvals(self) -> str:
        return "123"

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == "123"
        return ChannelFacts(channel_type=0, name="approvals", recipient_ids=())


@dataclass(frozen=True, slots=True)
class _Transport:
    messages: dict[str, DiscordSubmissionMessage] = field(default_factory=dict)
    posts: list[tuple[str, str, tuple[SubmissionAttachment, ...]]] = field(default_factory=list)
    reactions: dict[tuple[str, str], tuple[DiscordUser, ...]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)

    def post_submission(
        self,
        channel_id: str,
        content: str,
        attachments: tuple[SubmissionAttachment, ...],
    ) -> str:
        message_id = f"message-{len(self.posts) + 1}"
        self.posts.append((channel_id, content, attachments))
        self.messages[message_id] = DiscordSubmissionMessage(
            content=content,
            attachment_names=tuple(attachment.filename for attachment in attachments),
        )
        return message_id

    def fetch_message(self, channel_id: str, message_id: str) -> DiscordSubmissionMessage | None:
        assert channel_id == "123"
        return self.messages.get(message_id)

    def delete_message(self, channel_id: str, message_id: str) -> None:
        assert channel_id == "123"
        self.deleted.append(message_id)
        _ = self.messages.pop(message_id, None)

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        assert channel_id == "123"
        assert message_id in self.messages
        assert emoji in {"✅", "⛔"}

    def reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[DiscordUser, ...]:
        assert channel_id == "123"
        return self.reactions.get((message_id, emoji), ())


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )


def _artifact(tmp_path: Path):
    repo = tmp_path / "personal-x"
    (repo / "scripts").mkdir(parents=True)
    _ = (repo / "SKILL.md").write_text(
        "---\nname: personal-x\ndescription: Personal submission fixture\n---\n",
        encoding="utf-8",
    )
    scenario = repo / "scripts" / "scenario.sh"
    _ = scenario.write_text(
        "#!/usr/bin/env bash\nprintf 'SCENARIO-PASS\\n'\n",
        encoding="utf-8",
    )
    _ = scenario.chmod(0o755)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "member@example.invalid")
    _git(repo, "config", "user.name", "member")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Add fixture")
    return package_personal_skill(
        SubmissionPackageConfig(
            personal_repo=repo,
            managed_skill="managed-x",
            publisher="testlab",
            release_sequence=1,
            previous_sha256=None,
            metadata=ReleaseMetadata("any", False, (), "Promote fixture.", None),
            output_dir=tmp_path / "submission",
        )
    )


def _config(tmp_path: Path, transport: _Transport) -> SubmissionApprovalConfig:
    return SubmissionApprovalConfig(
        artifact=_artifact(tmp_path),
        group_id="group-a",
        submitter="member-a",
        reviewer_id="999",
        surface=SupplyChainSurface(
            ApprovalKind.SKILL_SUBMIT,
            owner="111",
            directory=_Directory(),
        ),
        transport=transport,
        state_root=tmp_path / "state",
    )


def test_submit_when_artifact_is_valid_then_shared_lifecycle_posts_once_and_persists_binding(
    tmp_path: Path,
) -> None:
    # Given: a verified artifact and the existing group approval surface.
    transport = _Transport()
    config = _config(tmp_path, transport)

    # When: the same logical submission is requested twice.
    first = request_submission_approval(config)
    second = request_submission_approval(config)

    # Then: lifecycle deduplication retains one bound request on the existing surface.
    assert first.outcome is Outcome.POSTED
    assert second.outcome is Outcome.PENDING
    assert len(transport.posts) == 1
    channel_id, content, attachments = transport.posts[0]
    envelope = parse_submission_message(content)
    assert channel_id == "123"
    assert envelope.skill == config.artifact.manifest.skill
    assert envelope.tarball_sha256 == config.artifact.tarball_sha256
    assert {attachment.filename for attachment in attachments} == set(envelope.attachment_names)
    record = next((tmp_path / "state" / "pending").glob("*.json")).read_text(encoding="utf-8")
    assert '"kind":"skill-submit"' in record
    assert '"surface":"skill-approvals"' in record
    assert '"channel_id":"123"' in record


def test_submit_when_artifact_changes_after_packaging_then_rejects_before_discord(
    tmp_path: Path,
) -> None:
    # Given: an artifact selected for submission, then changed before lifecycle posting.
    transport = _Transport()
    config = _config(tmp_path, transport)
    with config.artifact.tarball_path.open("ab") as handle:
        _ = handle.write(b"tampered")

    # When / Then: the gate fails closed before the admin can see the request.
    with pytest.raises(SubmissionArtifactError, match="tarball sha256"):
        _ = request_submission_approval(config)
    assert transport.posts == []


def test_submission_producer_has_no_publication_or_mount_capability() -> None:
    # Given / When: the member-facing producer is inspected as Python structure.
    path = Path(__file__).resolve().parents[2] / "automation" / "managed_skills" / "submission_cli.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }

    # Then: submissions can package and request review, but cannot import, publish, deploy, or mount.
    assert not any("publish_cli" in name or "skill_store" in name for name in imports)
    assert calls.isdisjoint({"publish", "activate", "mount", "copytree"})
