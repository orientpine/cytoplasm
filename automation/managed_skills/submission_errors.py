"""Typed failures shared by personal-skill submission boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from automation.typing_compat import override


@dataclass(frozen=True, slots=True)
class SubmissionArtifactError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail
