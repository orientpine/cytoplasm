from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from automation.group_roster.schema import MemberStatus, Roster, RosterAdmin, RosterMember
from automation.interop.approval_surface import ApprovalKind, ChannelFacts
from automation.managed_skills import publish_cli
from automation.managed_skills.publisher_config import PublisherIdentity
from automation.managed_skills.release_metadata import ReleaseMetadata
from automation.managed_skills.submission_approval import (
    SubmissionApprovalConfig,
    request_submission_approval,
)
from automation.managed_skills.submission_artifact import (
    SubmissionPackageConfig,
    package_personal_skill,
)
from automation.managed_skills.submission_message import parse_submission_message
from automation.managed_skills.submission_source import (
    ApprovedSubmissionConfig,
    SubmissionEvidence,
    SubmissionReviewError,
    open_approved_submission,
)
from automation.managed_skills.submission_transport import (
    DiscordSubmissionMessage,
    DiscordUser,
    SubmissionAttachment,
)
from automation.skill_gate_surface import SupplyChainSurface


class _Directory:
    def owner_dm(self) -> str:
        raise AssertionError("submission review must stay on the group surface")

    def skill_approvals(self) -> str:
        return "123"

    def describe(self, channel_id: str) -> ChannelFacts:
        assert channel_id == "123"
        return ChannelFacts(0, "approvals", ())


@dataclass(frozen=True, slots=True)
class _Transport:
    messages: dict[str, DiscordSubmissionMessage] = field(default_factory=dict)
    reactions: dict[tuple[str, str], tuple[DiscordUser, ...]] = field(default_factory=dict)
    posts: list[None] = field(default_factory=list)

    def post_submission(
        self,
        channel_id: str,
        content: str,
        attachments: tuple[SubmissionAttachment, ...],
    ) -> str:
        assert channel_id == "123"
        self.posts.append(None)
        message_id = f"message-{len(self.posts)}"
        self.messages[message_id] = DiscordSubmissionMessage(
            content,
            tuple(attachment.filename for attachment in attachments),
        )
        return message_id

    def fetch_message(self, channel_id: str, message_id: str) -> DiscordSubmissionMessage | None:
        assert channel_id == "123"
        return self.messages.get(message_id)

    def delete_message(self, channel_id: str, message_id: str) -> None:
        assert channel_id == "123"
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


@dataclass(frozen=True, slots=True)
class _PublishRunner:
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(
        self,
        args: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env
        assert capture_output and text and timeout > 0
        command = tuple(args)
        self.calls.append(command)
        if command[-3:-1] == ("tag", "--list"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if "status" in command:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "publish-request" in command:
            return subprocess.CompletedProcess(
                args,
                0,
                '{"message_id":"publish-message","publish_nonce":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
                "",
            )
        return subprocess.CompletedProcess(args, 0, "", "")


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )


def _roster() -> Roster:
    return Roster(
        schema=1,
        group_id="group-a",
        admin=RosterAdmin(
            name="Admin",
            discord_user_id="999",
            publisher_principal="publisher-testlab@autophagy",
            signing_public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnly",
        ),
        members=[RosterMember("Member", "111", "member-a", MemberStatus.ACTIVE)],
    )


def _surface() -> SupplyChainSurface:
    return SupplyChainSurface(ApprovalKind.SKILL_SUBMIT, "111", _Directory())


def _artifact(tmp_path: Path):
    repo = tmp_path / "personal-x"
    (repo / "scripts").mkdir(parents=True)
    _ = (repo / "SKILL.md").write_text(
        "---\nname: personal-x\ndescription: Explicit publish fixture\n---\n",
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
            repo,
            "managed-x",
            "testlab",
            1,
            None,
            ReleaseMetadata("any", False, (), "Promote fixture.", None),
            tmp_path / "submission",
        )
    )


def _submitted(tmp_path: Path, transport: _Transport):
    artifact = _artifact(tmp_path)
    verdict = request_submission_approval(
        SubmissionApprovalConfig(
            artifact,
            "group-a",
            "member-a",
            "999",
            _surface(),
            transport,
            tmp_path / "state",
        )
    )
    assert verdict.posted is not None
    message_id = verdict.posted.message_id
    envelope = parse_submission_message(transport.messages[message_id].content)
    return artifact, SubmissionEvidence(message_id, envelope.nonce)


def test_submission_without_admin_action_never_mutates_managed_repository(tmp_path: Path) -> None:
    # Given / When: a member packages and posts a valid submission, but the admin does nothing.
    transport = _Transport()
    _ = _submitted(tmp_path, transport)

    # Then: only the submission artifacts and approval state exist; no managed release exists.
    assert not (tmp_path / "managed-repo").exists()
    assert len(transport.posts) == 1


def test_approved_submission_when_publish_is_explicit_then_uses_existing_pipeline(
    tmp_path: Path,
) -> None:
    # Given: the group admin approved the bound submission message.
    transport = _Transport()
    artifact, evidence = _submitted(tmp_path, transport)
    transport.reactions[(evidence.message_id, "✅")] = (DiscordUser("999", False),)
    managed_repo = tmp_path / "managed-repo"
    managed_repo.mkdir()
    signing_key = tmp_path / "signing-key"
    _ = signing_key.write_text("test-only\n", encoding="utf-8")
    runner = _PublishRunner()

    # When: the admin explicitly opens that approved input and invokes publish_cli.publish.
    with open_approved_submission(
        ApprovedSubmissionConfig(artifact, evidence, _roster(), _surface(), transport)
    ) as approved:
        manifest = publish_cli.publish(
            publish_cli.PublishConfig(
                skill="managed-x",
                managed_repo=managed_repo,
                skills_src=approved.source_dir,
                changelog_file=None,
                signing_key=signing_key,
                identity=PublisherIdentity("testlab", "publisher-testlab@autophagy"),
                approve_evidence="deploy-message:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                injection_file=None,
                submitted_manifest=approved.manifest,
            ),
            runner,
            {},
        )

    # Then: publication still crosses the existing publish request/check and signed-tag pipeline.
    assert manifest == artifact.manifest
    assert any("publish-request" in call for call in runner.calls)
    assert any("publish-check" in call for call in runner.calls)
    assert any("commit" in call for call in runner.calls)
    assert any("push" in call for call in runner.calls)
    assert (managed_repo / "skills" / "managed-x" / "SKILL.md").is_file()


def test_submission_when_admin_cancel_and_approve_both_exist_then_review_fails_closed(
    tmp_path: Path,
) -> None:
    # Given: the admin has both reactions on the exact submission message.
    transport = _Transport()
    artifact, evidence = _submitted(tmp_path, transport)
    admin = (DiscordUser("999", False),)
    transport.reactions[(evidence.message_id, "✅")] = admin
    transport.reactions[(evidence.message_id, "⛔")] = admin

    # When / Then: cancellation wins and no publish source is yielded.
    with pytest.raises(SubmissionReviewError, match="cancelled"):
        with open_approved_submission(
            ApprovedSubmissionConfig(artifact, evidence, _roster(), _surface(), transport)
        ):
            pytest.fail("cancelled submission must never become a publish input")
