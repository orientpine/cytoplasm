from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import final

from automation.skill_generation.core import PipelineExit, ProposalStatus, RepetitionDetector
from automation.skill_generation.service import AutoSkillService, PipelineRouter, SkillGenerationPaths


@final
class FakePipeline:
    def __init__(self, result: PipelineExit) -> None:
        self.result: PipelineExit = result
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def run(self, source: Path, command: tuple[str, ...]) -> PipelineExit:
        self.calls.append((source, command))
        return self.result


def _service(tmp_path: Path, pipeline: FakePipeline) -> AutoSkillService:
    paths = SkillGenerationPaths.from_root(tmp_path)
    return AutoSkillService(paths, RepetitionDetector(), PipelineRouter(Path("/repo"), pipeline))


def test_observe_when_same_pattern_seen_three_times_then_creates_one_suggestion(tmp_path: Path) -> None:
    # Given: two current-week observations of the same parameterized task.
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    pipeline = FakePipeline(PipelineExit.AWAITING_OWNER)
    service = _service(tmp_path, pipeline)
    _ = service.observe("매주 보고서 101을 정리해줘", now - timedelta(days=2))
    _ = service.observe("매주 보고서 102을 정리해줘", now - timedelta(days=1))

    # When: the third normalized observation reaches the detector.
    proposal = service.observe("매주 보고서 103을 정리해줘", now)

    # Then: a generated draft exists and is routed to the W1-8 pipeline once.
    assert proposal is not None
    assert proposal.status is ProposalStatus.AWAITING_OWNER
    assert (proposal.draft_dir / "SKILL.md").is_file()
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0][1][0] == "SKILL_PROPOSAL_SOURCE=auto"
    assert pipeline.calls[0][1][1].startswith("SKILL_SRC_DIR=")


def test_observe_when_pattern_repeats_after_proposal_then_does_not_duplicate_suggestion(tmp_path: Path) -> None:
    # Given: a third observation has already created an owner-pending proposal.
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    pipeline = FakePipeline(PipelineExit.AWAITING_OWNER)
    service = _service(tmp_path, pipeline)
    for day in (2, 1, 0):
        _ = service.observe("문헌 목록 7을 요약해줘", now - timedelta(days=day))

    # When: the same task arrives again in the same ISO week.
    duplicate = service.observe("문헌 목록 8을 요약해줘", now)

    # Then: there is no second proposal or second pipeline request.
    assert duplicate is None
    assert len(pipeline.calls) == 1


def test_observe_when_w1_8_returns_weekly_cap_then_registry_marks_auto_held(tmp_path: Path) -> None:
    # Given: the canonical W1-8 router has already consumed its weekly limit.
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    pipeline = FakePipeline(PipelineExit.AUTO_HELD)
    service = _service(tmp_path, pipeline)

    # When: a third matching observation triggers a fourth auto proposal attempt.
    for day in (2, 1):
        _ = service.observe("외부 발표 1을 준비해줘", now - timedelta(days=day))
    proposal = service.observe("외부 발표 2을 준비해줘", now)

    # Then: W1-8 exit 3 is preserved as AUTO-HELD, without a local cap counter.
    assert proposal is not None
    assert proposal.status is ProposalStatus.AUTO_HELD
    assert "AUTO-HELD" in service.paths.registry.read_text(encoding="utf-8")


def test_audit_when_generated_skill_is_copied_without_pipeline_then_removes_and_records_rejection(tmp_path: Path) -> None:
    # Given: a generated-marker draft was copied directly into the mount directory.
    service = _service(tmp_path, FakePipeline(PipelineExit.AWAITING_OWNER))
    bypassed = service.paths.mounted / "auto-bypassed"
    _ = bypassed.mkdir(parents=True)
    _ = (bypassed / "SKILL.md").write_text("---\nautophagy_generated: true\n---\n", encoding="utf-8")

    # When: the runtime mount audit executes before the next agent dispatch.
    rejected = service.audit_mounts()

    # Then: the unmanaged mount is deleted and its rejection is ledgered.
    assert rejected == ("auto-bypassed",)
    assert not bypassed.exists()
    assert "BYPASS-REJECTED" in service.paths.registry.read_text(encoding="utf-8")


def test_audit_when_mounted_proposal_dir_appears_in_writable_root_then_still_removes_it(tmp_path: Path) -> None:
    # Given: a formerly governed generated skill is copied into the writable primary root.
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    service = _service(tmp_path, FakePipeline(PipelineExit.MOUNTED))
    _ = service.observe("자동 스킬 1을 만들어줘", now - timedelta(days=2))
    _ = service.observe("자동 스킬 2을 만들어줘", now - timedelta(days=1))
    proposal = service.observe("자동 스킬 3을 만들어줘", now)
    assert proposal is not None
    bypassed = service.paths.mounted / proposal.name
    _ = bypassed.mkdir(parents=True)
    _ = (bypassed / "SKILL.md").write_text("---\nautophagy_generated: true\n---\n", encoding="utf-8")

    # When: the audit observes the generated marker in the writable root.
    rejected = service.audit_mounts()

    # Then: the copy is removed and the append-only ledger records the bypass rejection.
    assert rejected == (proposal.name,)
    assert not bypassed.exists()
    assert "BYPASS-REJECTED" in service.paths.proposals.read_text(encoding="utf-8")


def test_audit_when_dir_has_no_generated_marker_then_it_is_never_touched(tmp_path: Path) -> None:
    # Given: self-authored skills and internal state entries in the writable primary root.
    service = _service(tmp_path, FakePipeline(PipelineExit.AWAITING_OWNER))
    self_authored = service.paths.mounted / "research-helper"
    archive = service.paths.mounted / ".archive"
    hub = service.paths.mounted / ".hub"
    hidden_state = service.paths.mounted / ".state.json"
    _ = self_authored.mkdir(parents=True)
    _ = archive.mkdir()
    _ = hub.mkdir()
    _ = (self_authored / "SKILL.md").write_text("---\nname: research-helper\n---\n", encoding="utf-8")
    _ = hidden_state.write_text("{}\n", encoding="utf-8")

    # When: the audit executes.
    rejected = service.audit_mounts()

    # Then: only generated-marker drafts are in scope; all unmarked entries remain untouched.
    assert rejected == ()
    assert self_authored.exists()
    assert archive.exists()
    assert hub.exists()
    assert hidden_state.exists()


def test_router_when_auto_draft_routes_then_uses_only_w1_8_with_auto_source(tmp_path: Path) -> None:
    # Given: a generated draft and a pipeline spy.
    source = tmp_path / "auto-draft"
    _ = source.mkdir()
    _ = (source / "SKILL.md").write_text("---\nname: auto-draft\n---\n", encoding="utf-8")
    pipeline = FakePipeline(PipelineExit.AWAITING_OWNER)
    router = PipelineRouter(Path("/srv/autophagy-agents"), pipeline)

    # When: the supervisor requests deployment.
    result = router.route(source)

    # Then: it uses deploy-skill.sh with SKILL_PROPOSAL_SOURCE=auto, never a mount command.
    assert result is PipelineExit.AWAITING_OWNER
    _, command = pipeline.calls[0]
    assert command == (
        "SKILL_PROPOSAL_SOURCE=auto",
        f"SKILL_SRC_DIR={source}",
        "/srv/autophagy-agents/automation/deploy-skill.sh",
        "auto-draft",
    )


def test_dispatch_when_remote_deploy_script_is_not_executable_then_invokes_it_with_bash() -> None:
    # Given: the production checkout can be group-readable without an executable mode bit.
    dispatch = Path("automation/skill_generation/dispatch.sh").read_text(encoding="utf-8")

    # When: the dispatcher builds its remote W1-8 command.

    # Then: Bash interprets an agent-read staged copy rather than requiring caller access to /srv.
    assert 'bash "$stage/repo/automation/deploy-skill.sh"' in dispatch
    assert "automation/skill_review.py" in dispatch


def test_record_pipeline_result_when_review_blocks_then_records_review_blocked(tmp_path: Path) -> None:
    # Given: an auto-generated draft that reached the normal owner-approval stage.
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    service = _service(tmp_path, FakePipeline(PipelineExit.AWAITING_OWNER))
    for day in (2, 1):
        _ = service.observe("회의록 1을 정리해줘", now - timedelta(days=day))
    proposal = service.observe("회의록 2을 정리해줘", now)
    assert proposal is not None

    # When: deploy-skill.sh returns its new review-gate exit code.
    result = service.record_pipeline_result(proposal.name, 5)

    # Then: the supervisory registry distinguishes review rejection from a generic error.
    assert result.status is ProposalStatus.REVIEW_BLOCKED
