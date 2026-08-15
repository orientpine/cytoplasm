"""Memory storage adapters (B2, t_18eb4fda) — offline, fully injected.

Every test runs without network, without a real Discord surface and without
touching ``~/.hermes``: the wiki child process is a recording fake and every
file target is a pytest tmp_path.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

import pytest

from automation.memory_routing.adapters import (
    CommandResult,
    MemoryMdTarget,
    MemoryWrite,
    SkillTarget,
    TasksTarget,
    WikiTarget,
    dedupe_key,
    write_memory_md,
    write_skill,
    write_tasks,
    write_wiki,
)
from automation.memory_routing.classifier import MemoryRoute

REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI_CLI = REPO_ROOT / "skills" / "wiki" / "scripts" / "wiki_cli.py"

WIKI_ROUTE = MemoryRoute(
    canonical="wiki",
    co_write=(),
    never_persist=False,
    needs_sensitive_approval=False,
    reason="explicit-memory-wiki",
)
PREFERENCE_ROUTE = MemoryRoute(
    canonical="wiki",
    co_write=("memory_md",),
    never_persist=False,
    needs_sensitive_approval=False,
    reason="stable-global-preference",
)
SENSITIVE_ROUTE = MemoryRoute(
    canonical="wiki",
    co_write=("memory_md",),
    never_persist=False,
    needs_sensitive_approval=True,
    reason="sensitive-needs-approval",
)
PROCEDURE_ROUTE = MemoryRoute(
    canonical="skill",
    co_write=(),
    never_persist=False,
    needs_sensitive_approval=False,
    reason="reusable-procedure",
)
TEMPORARY_ROUTE = MemoryRoute(
    canonical="tasks",
    co_write=(),
    never_persist=True,
    needs_sensitive_approval=False,
    reason="temporary-status",
)


@dataclass
class RecordingRunner:
    """Fake child-process runner: records argv + env, replays a canned result."""

    result: CommandResult = field(default_factory=lambda: CommandResult(0, ""))
    error: OSError | None = None
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = field(default_factory=list)

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        env: dict[str, str],
    ) -> CommandResult:
        self.calls.append((tuple(argv), dict(env)))
        if self.error is not None:
            raise self.error
        return self.result


def _wiki_target(runner: RecordingRunner, *, pending: frozenset[str] = frozenset()) -> WikiTarget:
    return WikiTarget(
        cli_path=WIKI_CLI,
        env={"PATH": "/usr/bin", "WIKI_ROOT": "/tmp/wiki", "DISCORD_BOT_TOKEN": "DUMMY-VALUE"},
        channel_id="dm",
        pending_keys=pending,
        runner=runner,
    )


# --------------------------------------------------------------------------- wiki


def test_wiki_write_delegates_to_the_owner_gated_cli_without_reimplementing_it() -> None:
    # Given: an explicit memory request routed to the wiki, and a recording child runner.
    runner = RecordingRunner(CommandResult(0, "DRAFT-CREATED id=d-42 action=create slug=s sha256=ab"))
    write = MemoryWrite(
        route=WIKI_ROUTE,
        title="샘플 식별자 접두사",
        body="이 프로젝트의 샘플 식별자 접두사는 AX.",
        tags=("memory", "프로젝트"),
    )

    # When: the wiki adapter runs.
    result = write_wiki(write, _wiki_target(runner))

    # Then: exactly one child process ran, and it is the real wiki draft CLI.
    assert WIKI_CLI.exists()
    assert len(runner.calls) == 1
    argv, env = runner.calls[0]
    assert argv == (
        sys.executable,
        str(WIKI_CLI),
        "draft",
        "--title",
        "샘플 식별자 접두사",
        "--tags",
        "memory,프로젝트",
        "--body",
        "이 프로젝트의 샘플 식별자 접두사는 AX.",
        "--channel-id",
        "dm",
        "--kind",
        "note",
        "--authority",
        "default",
        "--provenance",
        "stated",
        "--status",
        "active",
    )
    # Then: credentials are propagated explicitly to the child (watcher rule b-2).
    assert env["DISCORD_BOT_TOKEN"] == "DUMMY-VALUE"
    assert env["WIKI_ROOT"] == "/tmp/wiki"
    # Then: the draft is only drafted — confirm/apply stay with the existing gate.
    assert "confirm" not in argv
    assert result.outcome == "success"
    assert "d-42" in result.detail


def test_wiki_write_marks_a_stable_preference_with_the_twin_preference_kind() -> None:
    # Given: a stable global preference routed to the wiki.
    runner = RecordingRunner(CommandResult(0, "DRAFT-CREATED id=d-7 action=create slug=s sha256=ab"))
    write = MemoryWrite(route=PREFERENCE_ROUTE, title="호칭", body="나를 '차'라고 불러줘")

    # When: the wiki adapter runs.
    result = write_wiki(write, _wiki_target(runner))

    # Then: the whitelisted twin kind is `preference`, not `note`.
    argv, _ = runner.calls[0]
    assert argv[argv.index("--kind") + 1] == "preference"
    assert result.outcome == "success"

def test_wiki_write_argv_is_accepted_by_the_real_wiki_cli(tmp_path: Path) -> None:
    """Integration rung: the real owner-gated CLI, no fake, no network."""
    # Given: the real wiki CLI pointed at a sandbox vault and gate directory.
    write = MemoryWrite(
        route=PREFERENCE_ROUTE,
        title="호칭 선호",
        body="나를 '차'라고 불러줘",
        tags=("memory", "선호"),
    )
    target = WikiTarget(
        cli_path=WIKI_CLI,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "WIKI_ROOT": str(tmp_path / "wiki"),
            "WIKI_GATE_DIR": str(tmp_path / "wiki-gate"),
        },
    )

    # When: the adapter spawns the real CLI (default runner).
    result = write_wiki(write, target)

    # Then: the frontmatter schema accepted the argv and one draft awaits approval.
    assert result.outcome == "success", result.detail
    drafts = sorted((tmp_path / "wiki-gate" / "drafts").glob("*.json"))
    assert len(drafts) == 1
    # Then: nothing was applied to the vault — the owner gate still owns that step.
    assert not (tmp_path / "wiki").exists()


def test_wiki_write_is_rejected_when_the_cli_refuses_the_frontmatter_schema() -> None:
    # Given: the wiki CLI exits 2 (frontmatter schema rejected, nothing saved).
    runner = RecordingRunner(CommandResult(2, ""))
    write = MemoryWrite(route=WIKI_ROUTE, title="t", body="b", tags=("bad tag",))

    # When: the wiki adapter runs.
    result = write_wiki(write, _wiki_target(runner))

    # Then: the schema verdict is surfaced as a rejection, never as a success.
    assert result.outcome == "rejected"


def test_wiki_write_is_retryable_when_the_child_environment_is_broken() -> None:
    # Given: the wiki CLI exits 3 (config/env error).
    runner = RecordingRunner(CommandResult(3, ""))
    write = MemoryWrite(route=WIKI_ROUTE, title="t", body="b")

    # When: the wiki adapter runs.
    result = write_wiki(write, _wiki_target(runner))

    # Then: an environment fault is retryable, not a content rejection.
    assert result.outcome == "retryable_failure"


def test_wiki_write_is_retryable_when_the_child_cannot_be_spawned() -> None:
    # Given: spawning the child fails at the OS level.
    runner = RecordingRunner(error=OSError(2, "No such file or directory"))
    write = MemoryWrite(route=WIKI_ROUTE, title="t", body="b")

    # When: the wiki adapter runs.
    result = write_wiki(write, _wiki_target(runner))

    # Then: the fault is retryable and no exception escapes the adapter.
    assert result.outcome == "retryable_failure"


def test_wiki_write_is_duplicate_when_an_identical_draft_already_awaits_approval() -> None:
    # Given: the same fact is already pending owner approval.
    runner = RecordingRunner(CommandResult(0, "DRAFT-CREATED id=d-1 action=create slug=s sha256=ab"))
    body = "이 프로젝트의 샘플 식별자 접두사는 AX."
    target = _wiki_target(runner, pending=frozenset({dedupe_key(body)}))
    write = MemoryWrite(route=WIKI_ROUTE, title="t", body=body)

    # When: the wiki adapter runs.
    result = write_wiki(write, target)

    # Then: no second approval message is requested (single-approval-message rule).
    assert result.outcome == "duplicate"
    assert runner.calls == []


def test_wiki_write_is_rejected_before_spawning_when_sensitive_approval_is_missing() -> None:
    # Given: sensitive content whose owner approval was not supplied.
    runner = RecordingRunner()
    write = MemoryWrite(route=SENSITIVE_ROUTE, title="t", body="특허 관련 사실")

    # When: the wiki adapter runs.
    result = write_wiki(write, _wiki_target(runner))

    # Then: nothing is spawned at all — fail-closed before any external effect.
    assert result.outcome == "rejected"
    assert runner.calls == []


# ----------------------------------------------------------------------- memory_md


def test_memory_md_appends_one_short_stable_fact(tmp_path: Path) -> None:
    # Given: a stable global preference and an injected MEMORY.md path.
    path = tmp_path / "memories" / "MEMORY.md"
    write = MemoryWrite(route=PREFERENCE_ROUTE, title="호칭", body="나를 '차'라고 불러줘")

    # When: the MEMORY.md adapter runs.
    result = write_memory_md(write, MemoryMdTarget(path=path))

    # Then: exactly one bullet line is appended.
    assert result.outcome == "success"
    assert path.read_text(encoding="utf-8").splitlines() == ["- 나를 '차'라고 불러줘"]


def test_memory_md_returns_duplicate_when_the_fact_differs_only_in_spacing_and_case(
    tmp_path: Path,
) -> None:
    # Given: MEMORY.md already records the fact under a different formatting.
    path = tmp_path / "MEMORY.md"
    path.write_text("- Answer In Korean\n", encoding="utf-8")
    write = MemoryWrite(route=PREFERENCE_ROUTE, title="언어", body="  answer   in korean  ")

    # When: the same fact is written again.
    result = write_memory_md(write, MemoryMdTarget(path=path))

    # Then: it is reported as a duplicate and the file gains no second line.
    assert result.outcome == "duplicate"
    assert path.read_text(encoding="utf-8") == "- Answer In Korean\n"


def test_memory_md_rejects_unapproved_sensitive_content_leaving_the_file_byte_identical(
    tmp_path: Path,
) -> None:
    # Given: an existing MEMORY.md and sensitive content without owner approval.
    path = tmp_path / "MEMORY.md"
    path.write_text("- 기존 사실\n", encoding="utf-8")
    before = sha256(path.read_bytes()).hexdigest()
    write = MemoryWrite(route=SENSITIVE_ROUTE, title="t", body="민감한 사실")

    # When: the MEMORY.md adapter runs.
    result = write_memory_md(write, MemoryMdTarget(path=path))

    # Then: nothing sensitive reaches the plaintext file.
    assert result.outcome == "rejected"
    assert sha256(path.read_bytes()).hexdigest() == before


def test_memory_md_accepts_sensitive_content_once_the_owner_approved_it(tmp_path: Path) -> None:
    # Given: the same sensitive content, this time with owner approval supplied.
    path = tmp_path / "MEMORY.md"
    write = MemoryWrite(
        route=SENSITIVE_ROUTE,
        title="t",
        body="민감한 사실",
        approved_sensitive=True,
    )

    # When: the MEMORY.md adapter runs.
    result = write_memory_md(write, MemoryMdTarget(path=path))

    # Then: the approved fact is appended.
    assert result.outcome == "success"
    assert path.read_text(encoding="utf-8") == "- 민감한 사실\n"


def test_memory_md_rejects_a_fact_too_long_to_be_a_stable_one_liner(tmp_path: Path) -> None:
    # Given: a long narrative that belongs in the wiki, not in MEMORY.md.
    path = tmp_path / "MEMORY.md"
    write = MemoryWrite(route=PREFERENCE_ROUTE, title="t", body="가" * 201)

    # When: the MEMORY.md adapter runs.
    result = write_memory_md(write, MemoryMdTarget(path=path))

    # Then: it is rejected and no file is created.
    assert result.outcome == "rejected"
    assert not path.exists()


def test_memory_md_rejects_a_route_that_never_selected_it(tmp_path: Path) -> None:
    # Given: a wiki-only route (co_write is empty).
    path = tmp_path / "MEMORY.md"
    write = MemoryWrite(route=WIKI_ROUTE, title="t", body="프로젝트 지식")

    # When: the MEMORY.md adapter runs anyway.
    result = write_memory_md(write, MemoryMdTarget(path=path))

    # Then: the deterministic route guard refuses the write.
    assert result.outcome == "rejected"
    assert not path.exists()


def test_memory_md_preserves_an_existing_file_that_lacks_a_trailing_newline(
    tmp_path: Path,
) -> None:
    # Given: a legacy MEMORY.md whose last line has no trailing newline.
    path = tmp_path / "MEMORY.md"
    path.write_text("# 기억\n\n- 기존 사실", encoding="utf-8")
    write = MemoryWrite(route=PREFERENCE_ROUTE, title="t", body="새 사실")

    # When: a new fact is appended.
    result = write_memory_md(write, MemoryMdTarget(path=path))

    # Then: the legacy content survives verbatim and the new bullet is its own line.
    assert result.outcome == "success"
    assert path.read_text(encoding="utf-8").splitlines() == [
        "# 기억",
        "",
        "- 기존 사실",
        "- 새 사실",
    ]


def test_memory_md_is_retryable_when_the_path_cannot_be_created(tmp_path: Path) -> None:
    # Given: the parent of the MEMORY.md path is an existing regular file.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    write = MemoryWrite(route=PREFERENCE_ROUTE, title="t", body="새 사실")

    # When: the MEMORY.md adapter runs.
    result = write_memory_md(write, MemoryMdTarget(path=blocker / "MEMORY.md"))

    # Then: the I/O fault is retryable, not a rejection.
    assert result.outcome == "retryable_failure"


# --------------------------------------------------------------------------- skill


def test_skill_records_the_procedure_and_never_copies_it_into_memory_md(
    tmp_path: Path,
) -> None:
    # Given: a reusable procedure and a MEMORY.md that must stay untouched.
    memory_md = tmp_path / "MEMORY.md"
    directory = tmp_path / "skill-proposals"
    write = MemoryWrite(
        route=PROCEDURE_ROUTE,
        title="세미나 출장 신청 절차",
        body="1. 양식 작성\n2. 승인 요청\n3. 제출",
    )

    # When: the skill adapter runs.
    result = write_skill(write, SkillTarget(directory=directory, guarded_paths=(memory_md,)))

    # Then: the procedure is stored once, and MEMORY.md was never created.
    assert result.outcome == "success"
    assert len(list(directory.iterdir())) == 1
    assert not memory_md.exists()


def test_skill_returns_duplicate_for_the_same_procedure(tmp_path: Path) -> None:
    # Given: the procedure was already recorded.
    directory = tmp_path / "skill-proposals"
    target = SkillTarget(directory=directory)
    write = MemoryWrite(route=PROCEDURE_ROUTE, title="절차", body="1. 양식 작성")
    assert write_skill(write, target).outcome == "success"

    # When: the same procedure is recorded again with different spacing.
    again = MemoryWrite(route=PROCEDURE_ROUTE, title="절차", body="1.   양식 작성  ")
    result = write_skill(again, target)

    # Then: it is a duplicate and no second file appears.
    assert result.outcome == "duplicate"
    assert len(list(directory.iterdir())) == 1


def test_skill_is_rejected_when_its_directory_would_swallow_memory_md(tmp_path: Path) -> None:
    # Given: a skill directory that contains the MEMORY.md path.
    directory = tmp_path / "memories"
    memory_md = directory / "MEMORY.md"
    write = MemoryWrite(route=PROCEDURE_ROUTE, title="절차", body="1. 양식 작성")

    # When: the skill adapter runs.
    result = write_skill(write, SkillTarget(directory=directory, guarded_paths=(memory_md,)))

    # Then: the collision with the permanent memory store is refused.
    assert result.outcome == "rejected"
    assert not directory.exists()


# --------------------------------------------------------------------------- tasks


def test_tasks_records_temporary_state_with_an_expiry(tmp_path: Path) -> None:
    # Given: a temporary status routed to tasks.
    path = tmp_path / "tasks.jsonl"
    write = MemoryWrite(route=TEMPORARY_ROUTE, title="출장", body="이번 주까지 출장 중")

    # When: the tasks adapter runs.
    result = write_tasks(write, TasksTarget(path=path, expires_at="2026-08-04T00:00:00Z"))

    # Then: one record with the expiry is stored even though never_persist is set.
    assert result.outcome == "success"
    assert path.read_text(encoding="utf-8").count("\n") == 1
    assert "2026-08-04T00:00:00Z" in path.read_text(encoding="utf-8")


def test_tasks_refuses_to_write_into_a_permanent_store(tmp_path: Path) -> None:
    # Given: a tasks path that points inside the permanent memory store.
    permanent = tmp_path / "memories"
    path = permanent / "tasks.jsonl"
    write = MemoryWrite(route=TEMPORARY_ROUTE, title="출장", body="이번 주까지 출장 중")

    # When: the tasks adapter runs.
    result = write_tasks(
        write,
        TasksTarget(path=path, expires_at="2026-08-04T00:00:00Z", guarded_paths=(permanent,)),
    )

    # Then: the permanent store is refused and nothing is written.
    assert result.outcome == "rejected"
    assert not path.exists()


def test_tasks_is_rejected_without_an_expiry(tmp_path: Path) -> None:
    # Given: temporary state with no expiry supplied.
    path = tmp_path / "tasks.jsonl"
    write = MemoryWrite(route=TEMPORARY_ROUTE, title="출장", body="이번 주까지 출장 중")

    # When: the tasks adapter runs.
    result = write_tasks(write, TasksTarget(path=path))

    # Then: state that cannot expire is not temporary — fail-closed.
    assert result.outcome == "rejected"
    assert not path.exists()


def test_tasks_returns_duplicate_for_the_same_temporary_state(tmp_path: Path) -> None:
    # Given: the temporary state was already recorded.
    path = tmp_path / "tasks.jsonl"
    target = TasksTarget(path=path, expires_at="2026-08-04T00:00:00Z")
    write = MemoryWrite(route=TEMPORARY_ROUTE, title="출장", body="이번 주까지 출장 중")
    assert write_tasks(write, target).outcome == "success"

    # When: the same state is recorded again with different spacing.
    again = MemoryWrite(route=TEMPORARY_ROUTE, title="출장", body="이번 주까지  출장 중")
    result = write_tasks(again, target)

    # Then: it is a duplicate and only one record exists.
    assert result.outcome == "duplicate"
    assert path.read_text(encoding="utf-8").count("\n") == 1


# ------------------------------------------------------------------- never_persist


@pytest.mark.parametrize("target_name", ["wiki", "memory_md", "skill"])
def test_never_persist_state_reaches_no_permanent_store(tmp_path: Path, target_name: str) -> None:
    # Given: temporary status content aimed at each permanent adapter.
    runner = RecordingRunner()
    memory_md = tmp_path / "MEMORY.md"
    directory = tmp_path / "skill-proposals"
    write = MemoryWrite(route=TEMPORARY_ROUTE, title="출장", body="이번 주까지 출장 중")
    calls = {
        "wiki": lambda: write_wiki(write, _wiki_target(runner)),
        "memory_md": lambda: write_memory_md(write, MemoryMdTarget(path=memory_md)),
        "skill": lambda: write_skill(write, SkillTarget(directory=directory)),
    }

    # When: the permanent adapter is invoked.
    result = calls[target_name]()

    # Then: it refuses, spawns nothing and creates nothing.
    assert result.outcome == "rejected"
    assert runner.calls == []
    assert not memory_md.exists()
    assert not directory.exists()
