from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Protocol

import pytest

from automation.interop.external_effect_gate import (
    ApprovalBinding,
    ApprovalContext,
    SignedApprovalEvent,
    approval_challenge,
    record_signed_e2e_approval,
)
from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.obsidian_write import (
    READ_ONLY_MIRROR_DIR,
    NotePlan,
    ObsidianWriteConfig,
    ObsidianWriteError,
    WriteReceipt,
    plan_note,
    write_note,
)
from automation.obsidian_write import gate_binding

_OWNER_ID = "owner"
_E2E_SECRET = b"obsidian-write-tests"


@dataclass(frozen=True, slots=True)
class CapturedCall:
    argv: tuple[str, ...]
    cwd: Path | None
    environment: dict[str, str]
    timeout: float | None


class FakeGitRunner:
    def __init__(
        self,
        clone_dir: Path,
        *,
        failure_at: int | None = None,
        remote_content: str | None = None,
    ) -> None:
        self.calls: list[CapturedCall] = []
        self._clone_dir: Path = clone_dir
        self._failure_at: int | None = failure_at
        self._remote_content: str | None = remote_content

    def __call__(
        self,
        argv: list[str],
        /,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert env is not None, "git invocations must carry explicit env"
        assert capture_output is True
        assert text is True
        self.calls.append(CapturedCall(tuple(argv), cwd, dict(env), timeout))

        if tuple(argv[1:2]) == ("clone",):
            _ = self._clone_dir.mkdir(mode=0o700)
            _ = (self._clone_dir / ".git").mkdir()

        if self._failure_at == len(self.calls):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="failed")

        if tuple(argv[1:2]) == ("show",):
            remote_content = self._remote_content
            if remote_content is None:
                remote_spec = argv[-1]
                _remote_ref, separator, relpath = remote_spec.partition(":")
                assert separator == ":"
                remote_content = (self._clone_dir / PurePosixPath(relpath)).read_text(
                    encoding="utf-8"
                )
            return subprocess.CompletedProcess(argv, 0, stdout=remote_content, stderr="")

        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


class GitRunner(Protocol):
    def __call__(
        self,
        argv: list[str],
        /,
        *,
        cwd: Path | None,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


@pytest.fixture
def configured_clone(tmp_path: Path) -> ObsidianWriteConfig:
    # Given: an isolated write clone and a readable, non-production key path.
    clone_dir = tmp_path / "obsidian-write"
    _ = clone_dir.mkdir(mode=0o700)
    _ = (clone_dir / ".git").mkdir()
    key_path = tmp_path / "obsidian-write-key"
    _ = key_path.write_text("test key material", encoding="utf-8")
    _ = key_path.chmod(0o600)
    return ObsidianWriteConfig(
        repo_url="git@example.invalid:owner/vault.git",
        clone_dir=clone_dir,
        ssh_key_path=key_path,
        branch="main",
    )


def write_approved(
    plan: NotePlan,
    config: ObsidianWriteConfig,
    runner: GitRunner,
) -> WriteReceipt:
    context = ApprovalContext(config.clone_dir.parent / "approvals.jsonl", _OWNER_ID, True)
    decision = gate_binding.evaluate(plan, context=context)
    event = InboundEvent(
        event_id="obsidian-write-approval",
        user_id=_OWNER_ID,
        channel_id="approvals",
        text=approval_challenge(decision.action_hash, decision.target_id),
    )
    assert record_signed_e2e_approval(
        context,
        ApprovalBinding(decision.action_hash, decision.target_id),
        SignedApprovalEvent(event, sign_event(event, _E2E_SECRET), _E2E_SECRET),
    )
    return write_note(plan, config, runner, approval_context=context)


def test_write_note_fetches_upserts_commits_only_target_pushes_and_verifies_remote(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("실험 기록", "관찰 결과", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir)

    # When
    receipt = write_approved(plan, configured_clone, runner)

    # Then
    target = configured_clone.clone_dir / plan.relpath
    assert receipt.relpath == plan.relpath
    assert receipt.content_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert target.read_text(encoding="utf-8").startswith("# 실험 기록\n\n>[!info]\n")
    assert "> Author:" in target.read_text(encoding="utf-8")
    assert "> Created:" in target.read_text(encoding="utf-8")
    assert "> Modified:" in target.read_text(encoding="utf-8")
    assert "> Location:" in target.read_text(encoding="utf-8")
    assert "> Tag:" in target.read_text(encoding="utf-8")
    relpath = plan.relpath.as_posix()
    assert [call.argv for call in runner.calls] == [
        ("git", "fetch", "origin", "main"),
        ("git", "reset", "--hard", "origin/main"),
        ("git", "add", "--", relpath),
        (
            "git",
            "commit",
            "--only",
            "-m",
            f"obsidian-write: upsert {plan.relpath.name}",
            "--",
            relpath,
        ),
        ("git", "push", "origin", "HEAD:main"),
        ("git", "fetch", "origin", "main"),
        ("git", "show", f"origin/main:{relpath}"),
    ]


def test_write_note_updates_the_deterministic_target_without_duplicate(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("같은 제목", "새 본문", institutional=False, bucket_hint="resource")
    target = configured_clone.clone_dir / plan.relpath
    _ = target.parent.mkdir(parents=True)
    _ = target.write_text("old content", encoding="utf-8")
    runner = FakeGitRunner(configured_clone.clone_dir)

    # When
    _ = write_approved(plan, configured_clone, runner)

    # Then
    assert target.read_text(encoding="utf-8") != "old content"
    assert tuple(configured_clone.clone_dir.rglob("*.md")) == (target,)


@pytest.mark.parametrize(
    ("institutional", "bucket_hint"),
    ((False, "project"), (False, None), (True, "archive")),
)
def test_write_note_never_invokes_subprocess_with_read_only_mirror_path(
    configured_clone: ObsidianWriteConfig,
    institutional: bool,
    bucket_hint: str | None,
) -> None:
    # Given
    plan = plan_note("mirror isolation", "body", institutional=institutional, bucket_hint=bucket_hint)
    runner = FakeGitRunner(configured_clone.clone_dir)

    # When
    _ = write_approved(plan, configured_clone, runner)

    # Then: this is a property over personal, inbox, and institutional placements.
    mirror_path = str(READ_ONLY_MIRROR_DIR)
    assert all(
        mirror_path not in argument
        for call in runner.calls
        for argument in (*call.argv, "" if call.cwd is None else str(call.cwd))
    )


def test_write_note_refuses_read_only_mirror_clone_before_subprocess(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    mirror_config = ObsidianWriteConfig(
        repo_url=configured_clone.repo_url,
        clone_dir=READ_ONLY_MIRROR_DIR,
        ssh_key_path=configured_clone.ssh_key_path,
    )
    runner = FakeGitRunner(configured_clone.clone_dir)
    plan = plan_note("blocked", "body", institutional=False, bucket_hint="area")

    # When / Then
    with pytest.raises(ObsidianWriteError, match="read-only mirror") as captured:
        _ = write_note(plan, mirror_config, runner)
    assert captured.value.retryable is False
    assert runner.calls == []


def test_write_note_refuses_missing_key_before_subprocess(tmp_path: Path) -> None:
    # Given
    config = ObsidianWriteConfig(
        repo_url="git@example.invalid:owner/vault.git",
        clone_dir=tmp_path / "obsidian-write",
        ssh_key_path=tmp_path / "missing-key",
    )
    runner = FakeGitRunner(config.clone_dir)
    plan = plan_note("blocked", "body", institutional=False, bucket_hint="area")

    # When / Then
    with pytest.raises(ObsidianWriteError, match="deploy key") as captured:
        _ = write_note(plan, config, runner)
    assert captured.value.retryable is False
    assert runner.calls == []


def test_write_note_marks_failed_push_retryable(configured_clone: ObsidianWriteConfig) -> None:
    # Given
    plan = plan_note("push failure", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir, failure_at=5)

    # When / Then
    with pytest.raises(ObsidianWriteError, match="push") as captured:
        _ = write_approved(plan, configured_clone, runner)
    assert captured.value.retryable is True
    assert len(runner.calls) == 5


def test_write_note_refuses_mismatched_remote_readback(configured_clone: ObsidianWriteConfig) -> None:
    # Given
    plan = plan_note("readback mismatch", "expected body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir, remote_content="unexpected body")

    # When / Then
    with pytest.raises(ObsidianWriteError, match="verification") as captured:
        _ = write_approved(plan, configured_clone, runner)
    assert captured.value.retryable is True
    assert len(runner.calls) == 7


def test_write_note_calls_push_guard_immediately_before_push(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("gate boundary", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir)
    guard_calls: list[tuple[str, int]] = []

    def push_guard() -> None:
        guard_calls.append(("before-push", len(runner.calls)))

    # When
    _ = write_approved(plan, replace(configured_clone, push_guard=push_guard), runner)

    # Then
    assert guard_calls == [("before-push", 4)]
    assert runner.calls[4].argv[:2] == ("git", "push")
