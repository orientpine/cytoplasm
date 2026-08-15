from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, final

from automation.skill_generation.core import Observation, PipelineExit, ProposalStatus, RepetitionDetector

_OBSERVATION_ROW: Final = re.compile(r'^\{"pattern_hash": "([0-9a-f]{16})", "timestamp": "([^"]+)", "week": "(\d{4}-W\d{2})"\}$')
_PROPOSAL_ROW: Final = re.compile(r'^\{"draft_dir": "([^"]+)", "name": "(auto-[0-9a-f]{16})", "pattern_hash": "([0-9a-f]{16})", "status": "([A-Z-]+)", "week": "([^"]+)"\}$')


@dataclass(frozen=True, slots=True)
class SkillGenerationPaths:
    root: Path
    observations: Path
    proposals: Path
    drafts: Path
    mounted: Path
    registry: Path

    @classmethod
    def from_root(cls, root: Path) -> SkillGenerationPaths:
        return cls(root, root / "observations.jsonl", root / "proposals.jsonl", root / "drafts", root / "mounted", root / "AUTO-GENERATED.md")


@dataclass(frozen=True, slots=True)
class Proposal:
    name: str
    week: str
    pattern_hash: str
    draft_dir: Path
    status: ProposalStatus


class Pipeline(Protocol):
    def run(self, source: Path, command: tuple[str, ...]) -> PipelineExit: ...


@dataclass(frozen=True, slots=True)
class SubprocessPipeline:
    def run(self, source: Path, command: tuple[str, ...]) -> PipelineExit:
        del source
        source_assignment, proposal_assignment, *argv = command
        environment = dict(os.environ)
        for assignment in (source_assignment, proposal_assignment):
            name, value = assignment.split("=", maxsplit=1)
            environment[name] = value
        completed = subprocess.run(argv, check=False, env=environment)
        try:
            return PipelineExit(completed.returncode)
        except ValueError:
            return PipelineExit.ERROR


@dataclass(frozen=True, slots=True)
class PipelineRouter:
    repo_root: Path
    pipeline: Pipeline

    def route(self, source: Path) -> PipelineExit:
        if not (source / "SKILL.md").is_file():
            return PipelineExit.ERROR
        command = (
            "SKILL_PROPOSAL_SOURCE=auto",
            f"SKILL_SRC_DIR={source}",
            str(self.repo_root / "automation/deploy-skill.sh"),
            source.name,
        )
        return self.pipeline.run(source, command)


@final
class AutoSkillService:
    def __init__(self, paths: SkillGenerationPaths, detector: RepetitionDetector, router: PipelineRouter | None) -> None:
        self.paths: SkillGenerationPaths = paths
        self.detector: RepetitionDetector = detector
        self.router: PipelineRouter | None = router
        self._initialize()

    def observe(self, text: str, timestamp: datetime) -> Proposal | None:
        candidate = self.detector.observation(text, timestamp)
        observations = self._observations()
        self._append_json(self.paths.observations, self._observation_row(candidate))
        all_observations = (*observations, candidate)
        if not self.detector.reached_threshold(all_observations, candidate):
            return None
        name = self.detector.draft_name(candidate)
        existing = self._latest(name)
        if existing is not None:
            return None
        draft_dir = self._write_draft(name, candidate)
        proposal = Proposal(name, candidate.week, candidate.pattern_hash, draft_dir, ProposalStatus.SUGGESTED)
        self._record(proposal)
        if self.router is None:
            return proposal
        return self.route(name)

    def route(self, name: str) -> Proposal:
        proposal = self._latest(name)
        if proposal is None or self.router is None:
            raise ValueError("unknown proposal or unavailable pipeline router")
        result = self.router.route(proposal.draft_dir)
        routed = Proposal(proposal.name, proposal.week, proposal.pattern_hash, proposal.draft_dir, self._status_for(result))
        self._record(routed)
        return routed

    def record_pipeline_result(self, name: str, exit_code: int) -> Proposal:
        proposal = self._latest(name)
        if proposal is None:
            raise ValueError("unknown proposal")
        try:
            result = PipelineExit(exit_code)
        except ValueError:
            result = PipelineExit.ERROR
        routed = Proposal(proposal.name, proposal.week, proposal.pattern_hash, proposal.draft_dir, self._status_for(result))
        self._record(routed)
        return routed

    def audit_mounts(self) -> tuple[str, ...]:
        rejected: list[str] = []
        if not self.paths.mounted.is_dir():
            return ()
        for skill_dir in sorted(path for path in self.paths.mounted.iterdir() if path.is_dir()):
            marker = skill_dir / "SKILL.md"
            if "autophagy_generated: true" not in self._read(marker):
                continue
            proposal = self._latest(skill_dir.name)
            if proposal is not None and proposal.status is ProposalStatus.MOUNTED:
                continue
            shutil.rmtree(skill_dir)
            rejected.append(skill_dir.name)
            if proposal is not None:
                self._record(Proposal(proposal.name, proposal.week, proposal.pattern_hash, proposal.draft_dir, ProposalStatus.BYPASS_REJECTED))
            else:
                self._record(Proposal(skill_dir.name, "unknown", "unknown", skill_dir, ProposalStatus.BYPASS_REJECTED))
        return tuple(rejected)

    def _initialize(self) -> None:
        self.paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.paths.drafts.mkdir(mode=0o700, exist_ok=True)
        self.paths.mounted.mkdir(mode=0o700, exist_ok=True)
        if not self.paths.registry.exists():
            _ = self.paths.registry.write_text("# AUTO-GENERATED\n\n| UTC | Skill | Trigger | Status |\n| --- | --- | --- | --- |\n", encoding="utf-8")
            _ = self.paths.registry.chmod(0o600)

    def _observations(self) -> tuple[Observation, ...]:
        observations: list[Observation] = []
        for line in self._lines(self.paths.observations):
            matched = _OBSERVATION_ROW.fullmatch(line)
            if matched is None:
                continue
            try:
                timestamp = datetime.fromisoformat(matched.group(2))
            except ValueError:
                continue
            observations.append(Observation(timestamp, matched.group(3), matched.group(1)))
        return tuple(observations)

    def _latest(self, name: str) -> Proposal | None:
        matches = [proposal for proposal in self._proposals() if proposal.name == name]
        return matches[-1] if matches else None

    def _record(self, proposal: Proposal) -> None:
        self._append_json(self.paths.proposals, self._proposal_row(proposal))
        with self.paths.registry.open("a", encoding="utf-8") as handle:
            _ = handle.write(f"| {proposal.week} | `{proposal.name}` | `sha256:{proposal.pattern_hash}` | {proposal.status.value} |\n")
        _ = self.paths.registry.chmod(0o600)

    def _write_draft(self, name: str, observation: Observation) -> Path:
        draft_dir = self.paths.drafts / name
        scripts = draft_dir / "scripts"
        scripts.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ = (draft_dir / "SKILL.md").write_text(self._skill_markdown(name, observation), encoding="utf-8")
        scenario = scripts / "scenario.sh"
        _ = scenario.write_text(self._scenario(), encoding="utf-8")
        _ = scenario.chmod(0o700)
        return draft_dir

    def _proposals(self) -> tuple[Proposal, ...]:
        proposals: list[Proposal] = []
        for line in self._lines(self.paths.proposals):
            matched = _PROPOSAL_ROW.fullmatch(line)
            if matched is None:
                continue
            try:
                status = ProposalStatus(matched.group(4))
            except ValueError:
                continue
            proposals.append(Proposal(matched.group(2), matched.group(5), matched.group(3), Path(matched.group(1)), status))
        return tuple(proposals)

    def _lines(self, path: Path) -> tuple[str, ...]:
        if not path.exists():
            return ()
        try:
            return tuple(path.read_text(encoding="utf-8").splitlines())
        except OSError:
            return ()

    def _append_json(self, path: Path, row: dict[str, str]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        _ = path.chmod(0o600)

    def _observation_row(self, observation: Observation) -> dict[str, str]:
        return {"timestamp": observation.timestamp.isoformat(), "week": observation.week, "pattern_hash": observation.pattern_hash}

    def _proposal_row(self, proposal: Proposal) -> dict[str, str]:
        return {"name": proposal.name, "week": proposal.week, "pattern_hash": proposal.pattern_hash, "draft_dir": str(proposal.draft_dir), "status": proposal.status.value}

    def _status_for(self, exit_code: PipelineExit) -> ProposalStatus:
        return {
            PipelineExit.MOUNTED: ProposalStatus.MOUNTED,
            PipelineExit.AWAITING_OWNER: ProposalStatus.AWAITING_OWNER,
            PipelineExit.SANDBOX_BLOCKED: ProposalStatus.SANDBOX_BLOCKED,
            PipelineExit.AUTO_HELD: ProposalStatus.AUTO_HELD,
            PipelineExit.ERROR: ProposalStatus.PIPELINE_ERROR,
            PipelineExit.REVIEW_BLOCKED: ProposalStatus.REVIEW_BLOCKED,
        }[exit_code]

    def _read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def _skill_markdown(self, name: str, observation: Observation) -> str:
        return (
            f"---\nname: {name}\ndescription: \"반복 작업 감지기가 만든 안전한 스킬 초안이다. 소유자 승인 전 자동 장착하지 않는다.\"\nautophagy_generated: true\nmetadata:\n  hermes:\n    tags: [AutoGenerated, Draft]\n---\n\n"
            f"# {name}\n\n반복 패턴 `sha256:{observation.pattern_hash}`에서 생성된 초안이다. 실행 동작은 비어 있으며, "
            "소유자가 검토·보완한 뒤 W1-8 샌드박스와 본인 승인을 통과해야 한다.\n"
        )

    def _scenario(self) -> str:
        return "#!/usr/bin/env bash\nset -euo pipefail\nwork=\"$(mktemp -d)\"\ntrap 'rm -rf \"$work\"' EXIT\ncd \"$work\"\n[[ \"${AUTOPHAGY_DEMO_SECRET:-}\" == DUMMY-* ]]\n[[ \"${AUTOPHAGY_DEMO_SECRET:-}\" != *sk-* && \"${AUTOPHAGY_DEMO_SECRET:-}\" != *ghp_* && \"${AUTOPHAGY_DEMO_SECRET:-}\" != *\"Bot \"* ]]\npython3 -I -c 'print(\"SCENARIO-PASS generated-draft=true\")'\n"
