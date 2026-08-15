"""Single memory-storage flow, end to end (B4, t_63de43df).

Every test drives the *real* trio — ``classify_memory_request`` → the four store
adapters → ``classify_then_store`` — so a regression in any one layer, or in the
seam between them, turns this suite red. Every store lives under ``tmp_path``:
the wiki leg spawns the real ``wiki_cli.py draft`` child with an explicit
``env=`` whose HOME/WIKI_ROOT/WIKI_GATE_DIR point into ``tmp_path``, so the real
``~/.hermes/memories/MEMORY.md`` and the real vault are unreachable by
construction. Only the owner's ⛔ verdict is injected — the gate emits it at
confirm time (rc=1) and the ``draft`` verb cannot produce it.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Final, final

from automation.memory_routing.adapters import (
    AdapterResult,
    CommandResult,
    MemoryMdTarget,
    MemoryWrite,
    SkillTarget,
    TasksTarget,
    WikiTarget,
    dedupe_key,
    run_command,
    write_memory_md,
    write_skill,
    write_tasks,
    write_wiki,
)
from automation.memory_routing.flow import (
    MemoryFlowAdapters,
    MemoryFlowResult,
    MemoryRequest,
    classify_then_store,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
WIKI_CLI: Final = REPO_ROOT / "skills" / "wiki" / "scripts" / "wiki_cli.py"
_EXPIRES_AT: Final = "2026-08-04T00:00:00Z"
_OWNER_REJECTED: Final = CommandResult(returncode=1, stdout="")

_PROJECT_KNOWLEDGE: Final = (
    "기억해줘. 우리 프로젝트에서는 실험군 AX-17의 전처리 결과를 기준선으로 사용하고, "
    "샘플별 보정 계수와 장비 교정 이력을 함께 비교하며, 다음 분기 재현성 검토 전까지 "
    "이 분석 규칙과 예외 목록을 프로젝트 지식으로 유지해야 해. 결과 보고서의 표 형식은 "
    "열 순서를 샘플, 배치, 보정값, 판정으로 고정하는 것을 선호해."
)
_STABLE_PREFERENCE: Final = "나는 답변을 짧은 한국어로 받는 것을 항상 선호해. 기억해줘"
_PROCEDURE: Final = "보고서를 만들 때는 초안 검토, 민감도 확인, 승인 요청 순서로 진행하는 절차를 기억해줘"
_TEMPORARY_STATUS: Final = "이번 주 금요일까지 출장 중이라 답장이 늦어. 기억해줘"
_AMBIGUOUS_PHRASINGS: Final = ("기억해", "기억해줘", "앞으로도 이렇게")


@dataclass
class SpawnLog:
    """Records every wiki child spawn; replays the owner's verdict when injected."""

    injected: CommandResult | None = None
    argv: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, argv: tuple[str, ...], *, env: Mapping[str, str]) -> CommandResult:
        self.argv.append(tuple(argv))
        if self.injected is not None:
            return self.injected
        return run_command(tuple(argv), env=env)


@final
class MemorySandbox:
    """The four real stores, rooted in one tmp_path, plus a pending-approval ledger."""

    def __init__(self, root: Path, *, injected: CommandResult | None = None) -> None:
        self.memory_md = root / "memories" / "MEMORY.md"
        self.skill_dir = root / "skill-proposals"
        self.tasks = root / "tasks" / "tasks.jsonl"
        self.gate_dir = root / "wiki-gate"
        self.vault = root / "wiki"
        self.spawns = SpawnLog(injected)
        self.pending: set[str] = set()

    def store(self, request: MemoryRequest) -> MemoryFlowResult:
        """The single entry point under test — classify once, then store."""
        return classify_then_store(
            request,
            MemoryFlowAdapters(
                wiki=self._wiki,
                memory_md=self._memory_md,
                skill=self._skill,
                tasks=self._tasks,
            ),
        )

    def drafts(self) -> list[Path]:
        return sorted((self.gate_dir / "drafts").glob("*.json"))

    def memory_md_lines(self) -> list[str]:
        if not self.memory_md.exists():
            return []
        return self.memory_md.read_text(encoding="utf-8").splitlines()

    def _wiki(self, write: MemoryWrite) -> AdapterResult:
        target = WikiTarget(
            cli_path=WIKI_CLI,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(self.gate_dir.parent),
                "WIKI_ROOT": str(self.vault),
                "WIKI_GATE_DIR": str(self.gate_dir),
            },
            pending_keys=frozenset(self.pending),
            runner=self.spawns,
        )
        result = write_wiki(write, target)
        if result.outcome == "success":
            self.pending.add(dedupe_key(write.body))
        return result

    def _memory_md(self, write: MemoryWrite) -> AdapterResult:
        return write_memory_md(write, MemoryMdTarget(path=self.memory_md))

    def _skill(self, write: MemoryWrite) -> AdapterResult:
        return write_skill(
            write,
            SkillTarget(directory=self.skill_dir, guarded_paths=(self.memory_md,)),
        )

    def _tasks(self, write: MemoryWrite) -> AdapterResult:
        return write_tasks(
            write,
            TasksTarget(
                path=self.tasks,
                expires_at=_EXPIRES_AT,
                guarded_paths=(self.memory_md,),
            ),
        )


def test_long_project_knowledge_recall_only(tmp_path: Path) -> None:
    # Given: long explicit project knowledge — the Recall source, not a global fact.
    sandbox = MemorySandbox(tmp_path)
    request = MemoryRequest(title="프로젝트 분석 규칙", body=_PROJECT_KNOWLEDGE, tags=("memory",))

    # When: the single flow handles the request.
    result = sandbox.store(request)

    # Then: exactly one wiki draft awaits approval and no other store was touched.
    assert result.outcome == "stored"
    assert result.route.canonical == "wiki"
    assert result.route.co_write == ()
    assert len(sandbox.drafts()) == 1
    assert sandbox.memory_md_lines() == []
    assert not sandbox.skill_dir.exists()
    assert not sandbox.tasks.exists()
    # Then: the owner gate still owns the apply step — nothing reached the vault.
    assert not sandbox.vault.exists()


def test_stable_preference_cowrite(tmp_path: Path) -> None:
    # Given: a short, stable, globally useful preference.
    sandbox = MemorySandbox(tmp_path)
    request = MemoryRequest(title="답변 언어 선호", body=_STABLE_PREFERENCE, tags=("memory", "선호"))

    # When: the single flow handles the request.
    result = sandbox.store(request)

    # Then: the wiki note is canonical and MEMORY.md gains exactly one bullet.
    assert result.outcome == "stored"
    assert result.route.co_write == ("memory_md",)
    assert len(sandbox.drafts()) == 1
    assert sandbox.memory_md_lines() == [f"- {_STABLE_PREFERENCE}"]
    # Then: the child argv proves the existing owner-gated CLI did the drafting.
    assert len(sandbox.spawns.argv) == 1
    argv = sandbox.spawns.argv[0]
    assert argv[:3] == (sys.executable, str(WIKI_CLI), "draft")
    assert dict(zip(argv[3::2], argv[4::2], strict=True)) == {
        "--title": "답변 언어 선호",
        "--tags": "memory,선호",
        "--body": _STABLE_PREFERENCE,
        "--channel-id": "dm",
        "--kind": "preference",
        "--authority": "default",
        "--provenance": "stated",
        "--status": "active",
    }


def test_procedure_goes_to_skill_not_memory_md(tmp_path: Path) -> None:
    # Given: a reusable procedure, whose only mandatory home is a skill.
    sandbox = MemorySandbox(tmp_path)
    request = MemoryRequest(title="보고서 작성 절차", body=_PROCEDURE)

    # When: the single flow handles the request.
    result = sandbox.store(request)

    # Then: the procedure is recorded once as a skill proposal.
    assert result.outcome == "stored"
    assert result.canonical is not None
    assert result.canonical.target == "skill"
    assert len(list(sandbox.skill_dir.iterdir())) == 1
    # Then: a rationale wiki note stays optional — the route mandates no co-write —
    # and the procedure is never duplicated into MEMORY.md.
    assert result.route.co_write == ()
    assert sandbox.spawns.argv == []
    assert not sandbox.memory_md.exists()


def test_temporary_status_routes_to_tasks_and_never_persists(tmp_path: Path) -> None:
    # Given: a status that expires within seven days.
    sandbox = MemorySandbox(tmp_path)
    request = MemoryRequest(title="출장", body=_TEMPORARY_STATUS)

    # When: the single flow handles the request.
    result = sandbox.store(request)

    # Then: one expiring task record exists and persistence stayed blocked.
    assert result.outcome == "stored"
    assert result.route.never_persist is True
    records = [json.loads(line) for line in sandbox.tasks.read_text(encoding="utf-8").splitlines()]
    assert [record["expires_at"] for record in records] == [_EXPIRES_AT]
    assert sandbox.spawns.argv == []
    assert sandbox.drafts() == []
    assert not sandbox.memory_md.exists()
    assert not sandbox.skill_dir.exists()


def test_sensitive_blocked_before_approval(tmp_path: Path) -> None:
    # Given: sensitive content, no owner approval, and a MEMORY.md that already exists.
    sandbox = MemorySandbox(tmp_path)
    sandbox.memory_md.parent.mkdir(parents=True)
    sandbox.memory_md.write_text("- 기존 사실\n", encoding="utf-8")
    before = sha256(sandbox.memory_md.read_bytes()).hexdigest()
    request = MemoryRequest(
        title="민감 사실",
        body=_STABLE_PREFERENCE,
        sensitivity=frozenset({"patent-sensitive"}),
    )

    # When: the single flow handles the request.
    result = sandbox.store(request)

    # Then: the block happens before any external effect — no child, no draft.
    assert result.outcome == "sensitive_rejected"
    assert result.canonical is not None
    assert result.canonical.outcome == "not_attempted"
    assert [item.outcome for item in result.co_writes] == ["not_attempted"]
    assert sandbox.spawns.argv == []
    assert sandbox.drafts() == []
    # Then: the plaintext file is byte-identical.
    assert sha256(sandbox.memory_md.read_bytes()).hexdigest() == before


def test_sensitive_rejection_reports_nothing_stored(tmp_path: Path) -> None:
    # Given: the same sensitive content with approval supplied, but the owner-gated
    # wiki CLI answers with the rejection verdict (rc=1, confirmation absent).
    sandbox = MemorySandbox(tmp_path, injected=_OWNER_REJECTED)
    request = MemoryRequest(
        title="민감 사실",
        body=_STABLE_PREFERENCE,
        sensitivity=frozenset({"patent-sensitive"}),
        approved_sensitive=True,
    )

    # When: the single flow handles the request.
    result = sandbox.store(request)

    # Then: approval let the request reach the gate, and the gate's refusal stopped it.
    assert len(sandbox.spawns.argv) == 1
    assert result.outcome == "store_rejected"
    assert result.canonical is not None
    assert result.canonical.outcome == "rejected"
    # Then: the co-write is explicitly unattempted and nothing was stored anywhere.
    assert [item.outcome for item in result.co_writes] == ["not_attempted"]
    assert not sandbox.memory_md.exists()
    assert sandbox.drafts() == []


def test_duplicate_request_is_idempotent(tmp_path: Path) -> None:
    # Given: the identical stable preference was already stored once.
    sandbox = MemorySandbox(tmp_path)
    request = MemoryRequest(title="답변 언어 선호", body=_STABLE_PREFERENCE, tags=("memory",))
    first = sandbox.store(request)

    # When: the very same request arrives again.
    second = sandbox.store(request)

    # Then: both legs report duplicate and no second approval message is requested.
    assert first.outcome == "stored"
    assert second.outcome == "duplicate"
    assert second.canonical is not None
    assert second.canonical.outcome == "duplicate"
    assert [item.outcome for item in second.co_writes] == ["duplicate"]
    assert len(sandbox.spawns.argv) == 1
    assert len(sandbox.drafts()) == 1
    assert sandbox.memory_md_lines() == [f"- {_STABLE_PREFERENCE}"]
    assert second.idempotency_key == first.idempotency_key


def test_secondary_failure_preserves_canonical_success(tmp_path: Path) -> None:
    # Given: MEMORY.md is unwritable because its parent path is a regular file.
    sandbox = MemorySandbox(tmp_path)
    sandbox.memory_md.parent.write_text("blocker", encoding="utf-8")
    request = MemoryRequest(title="답변 언어 선호", body=_STABLE_PREFERENCE, tags=("memory",))

    # When: the single flow handles the request.
    result = sandbox.store(request)

    # Then: the canonical success survives verbatim beside the secondary failure.
    assert result.outcome == "partial_failure"
    assert result.canonical is not None
    assert result.canonical.target == "wiki"
    assert result.canonical.outcome == "success"
    assert [item.outcome for item in result.co_writes] == ["retryable_failure"]
    # Then: the canonical write is not rolled back — the draft is still on disk.
    assert len(sandbox.drafts()) == 1


def test_ambiguous_input_conservative(tmp_path: Path) -> None:
    for phrasing in _AMBIGUOUS_PHRASINGS:
        # Given: an explicit but content-free Korean memory request.
        sandbox = MemorySandbox(tmp_path / f"case-{_AMBIGUOUS_PHRASINGS.index(phrasing)}")
        request = MemoryRequest(title="기억 요청", body=phrasing)

        # When: the single flow handles the request.
        result = sandbox.store(request)

        # Then: the conservative wiki draft is the only effect — MEMORY.md is never guessed.
        assert result.route.reason == "uncertain-conservative", phrasing
        assert result.route.co_write == (), phrasing
        assert len(sandbox.drafts()) == 1, phrasing
        assert not sandbox.memory_md.exists(), phrasing
        assert not sandbox.skill_dir.exists(), phrasing
        assert not sandbox.tasks.exists(), phrasing
