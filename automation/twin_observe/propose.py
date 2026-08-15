from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Final, Protocol

from automation.twin_observe.aggregate import ActionTally

_ADVISORY_AUTHORITY: Final = "advisory"
_OBSERVED_PROVENANCE: Final = "observed"
_PRINCIPLE_KIND: Final = "principle"
_REJECT_THRESHOLD: Final = 3


@dataclass(frozen=True, slots=True)
class AdvisoryAuthorityError(Exception):
    requested_authority: str

    def __str__(self) -> str:
        return "observed candidates must remain advisory"


@dataclass(frozen=True, slots=True)
class CandidateThresholdError(Exception):
    rejects: int
    approves: int

    def __str__(self) -> str:
        return "observed candidates require at least three rejects and no approvals"


@dataclass(frozen=True, slots=True)
class WikiDraftError(Exception):
    exit_code: int

    def __str__(self) -> str:
        return f"wiki draft subprocess failed with exit code {self.exit_code}"


@dataclass(frozen=True, slots=True)
class ObservedCandidate:
    tally: ActionTally
    authority: str = _ADVISORY_AUTHORITY

    def __post_init__(self) -> None:
        if self.authority != _ADVISORY_AUTHORITY:
            raise AdvisoryAuthorityError(self.authority)
        if self.tally.rejects < _REJECT_THRESHOLD or self.tally.approves != 0:
            raise CandidateThresholdError(self.tally.rejects, self.tally.approves)


@dataclass(frozen=True, slots=True)
class DraftPayload:
    title: str
    body: str
    kind: str
    authority: str
    provenance: str


@dataclass(frozen=True, slots=True)
class WikiDraftRequest:
    wiki_cli: Path
    channel_id: str
    environment: Mapping[str, str]


class WikiDraftRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        environment: Mapping[str, str],
    ) -> CompletedProcess[str]: ...


def build_candidates(tallies: tuple[ActionTally, ...]) -> tuple[ObservedCandidate, ...]:
    return tuple(
        ObservedCandidate(tally)
        for tally in tallies
        if tally.rejects >= _REJECT_THRESHOLD and tally.approves == 0
    )


def render_draft(candidate: ObservedCandidate) -> DraftPayload:
    tally = candidate.tally
    evidence = "\n".join(
        f"- ledger={event.ledger} timestamp={event.ts}" for event in tally.events
    )
    body = "\n".join(
        (
            "## Trigger",
            f"{tally.skill}.{tally.action}에서 거절 기록 {tally.rejects}건이 관찰되었습니다.",
            "",
            "## Rule",
            "경향일까요? 이 관찰은 자문 전용이며 cha의 확인 전에는 실행 권한을 만들지 않습니다.",
            "",
            "## Exceptions",
            "승인 기록이 생기거나 cha가 판단을 수정하면 이 제안을 재검토합니다.",
            "",
            "## Evidence",
            evidence,
            "",
        )
    )
    return DraftPayload(
        title=f"관찰 제안: {tally.skill}.{tally.action}",
        body=body,
        kind=_PRINCIPLE_KIND,
        authority=_ADVISORY_AUTHORITY,
        provenance=_OBSERVED_PROVENANCE,
    )


def _run_wiki_draft(
    argv: tuple[str, ...], environment: Mapping[str, str]
) -> CompletedProcess[str]:
    return subprocess.run(argv, check=False, env=dict(environment), text=True)


def _draft_argv(payload: DraftPayload, request: WikiDraftRequest) -> tuple[str, ...]:
    return (
        sys.executable,
        str(request.wiki_cli),
        "draft",
        "--title",
        payload.title,
        "--body",
        payload.body,
        "--channel-id",
        request.channel_id,
        "--kind",
        payload.kind,
        "--authority",
        _ADVISORY_AUTHORITY,
        "--provenance",
        _OBSERVED_PROVENANCE,
    )


def submit_candidates(
    candidates: tuple[ObservedCandidate, ...],
    request: WikiDraftRequest,
    runner: WikiDraftRunner = _run_wiki_draft,
) -> None:
    for candidate in candidates:
        result = runner(_draft_argv(render_draft(candidate), request), request.environment)
        if result.returncode != 0:
            raise WikiDraftError(result.returncode)
