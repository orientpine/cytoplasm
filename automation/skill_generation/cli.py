from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.skill_generation.service import AutoSkillService, SkillGenerationPaths

_RUNTIME = Path(__file__).resolve().parents[2]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


@dataclass(frozen=True, slots=True)
class ObserveCommand:
    text: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class PipelineResultCommand:
    name: str
    exit_code: int
    request_only: bool


@dataclass(frozen=True, slots=True)
class AuditCommand:
    pass


Command = ObserveCommand | PipelineResultCommand | AuditCommand


def _paths() -> "SkillGenerationPaths":
    from automation.skill_generation.service import SkillGenerationPaths

    root = Path.home() / ".hermes" / "skill-generation"
    base = SkillGenerationPaths.from_root(root)
    return SkillGenerationPaths(
        base.root,
        base.observations,
        base.proposals,
        base.drafts,
        Path.home() / ".hermes" / "skills",
        base.registry,
    )


def _service() -> "AutoSkillService":
    from automation.skill_generation.core import RepetitionDetector
    from automation.skill_generation.service import AutoSkillService

    return AutoSkillService(_paths(), RepetitionDetector(), None)


def _parse(arguments: tuple[str, ...]) -> Command:
    match arguments:
        case ("observe", "--text", text):
            return ObserveCommand(text, datetime.now(UTC))
        case ("observe", "--text", text, "--timestamp", raw_timestamp):
            return ObserveCommand(text, datetime.fromisoformat(raw_timestamp).astimezone(UTC))
        case ("record-pipeline-result", name, raw_exit):
            return PipelineResultCommand(name, int(raw_exit), False)
        case ("record-pipeline-result", name, raw_exit, "--request-only"):
            return PipelineResultCommand(name, int(raw_exit), True)
        case ("audit",):
            return AuditCommand()
        case _:
            raise SystemExit("usage: observe --text TEXT [--timestamp ISO] | record-pipeline-result NAME EXIT [--request-only] | audit")


def main() -> int:
    from automation.skill_generation.core import PipelineExit

    service = _service()
    command = _parse(tuple(sys.argv[1:]))
    match command:
        case ObserveCommand(text=text, timestamp=timestamp):
            proposal = service.observe(text, timestamp)
            if proposal is not None:
                print(f"SUGGESTION name={proposal.name} status={proposal.status.value} pipeline=REQUIRED")
            return 0
        case PipelineResultCommand(name=name, exit_code=exit_code, request_only=request_only):
            status_code = PipelineExit.AWAITING_OWNER if request_only and exit_code == PipelineExit.MOUNTED else exit_code
            proposal = service.record_pipeline_result(name, int(status_code))
            print(f"PIPELINE-RESULT name={proposal.name} status={proposal.status.value}")
            return 0
        case AuditCommand():
            for name in service.audit_mounts():
                print(f"BYPASS-REJECTED name={name}")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
