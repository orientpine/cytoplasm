from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Final

_URL: Final = re.compile(r"https?://\S+", re.IGNORECASE)
_NUMBER: Final = re.compile(r"\d+")
_SPACE: Final = re.compile(r"\s+")


class PipelineExit(IntEnum):
    MOUNTED = 0
    AWAITING_OWNER = 1
    SANDBOX_BLOCKED = 2
    AUTO_HELD = 3
    ERROR = 4
    REVIEW_BLOCKED = 5


class ProposalStatus(StrEnum):
    SUGGESTED = "SUGGESTED"
    AWAITING_OWNER = "AWAITING-OWNER"
    SANDBOX_BLOCKED = "SANDBOX-BLOCKED"
    AUTO_HELD = "AUTO-HELD"
    MOUNTED = "MOUNTED"
    BYPASS_REJECTED = "BYPASS-REJECTED"
    PIPELINE_ERROR = "PIPELINE-ERROR"
    REVIEW_BLOCKED = "REVIEW-BLOCKED"


@dataclass(frozen=True, slots=True)
class Observation:
    timestamp: datetime
    week: str
    pattern_hash: str


class RepetitionDetector:
    def normalize(self, text: str) -> str:
        normalized = _URL.sub("<url>", text.casefold())
        normalized = _NUMBER.sub("<n>", normalized)
        return _SPACE.sub(" ", normalized).strip()[:160]

    def observation(self, text: str, timestamp: datetime) -> Observation:
        normalized = self.normalize(text)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        calendar = timestamp.isocalendar()
        return Observation(timestamp, f"{calendar.year}-W{calendar.week:02d}", digest)

    def reached_threshold(self, observations: tuple[Observation, ...], candidate: Observation) -> bool:
        matches = sum(
            observation.week == candidate.week and observation.pattern_hash == candidate.pattern_hash
            for observation in observations
        )
        return matches >= 3

    def draft_name(self, observation: Observation) -> str:
        return f"auto-{observation.pattern_hash}"
