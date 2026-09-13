"""수리 pending의 JSON 경계 자료형과 동결 승인 표면 파서."""
from __future__ import annotations

import json
from typing import Protocol, TypeAlias

from automation.interop.approval_surface import ApprovalKind, ApprovalSurface

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class JsonLoader(Protocol):
    """Narrow json.loads at the trust boundary before pending-state parsing."""

    def __call__(self, s: str) -> JsonValue: ...


JSON_LOADS: JsonLoader = json.loads


class PendingApprovalError(RuntimeError):
    """A pending repair approval could not be safely persisted or decoded."""


def decode_binding(
    decoded: dict[str, JsonValue],
) -> tuple[ApprovalKind | None, ApprovalSurface | None, str | None, int | None]:
    raw_kind = decoded.get("kind")
    raw_surface = decoded.get("surface")
    raw_channel_id = decoded.get("channel_id")
    raw_policy_version = decoded.get("policy_version")
    match raw_kind, raw_surface, raw_channel_id, raw_policy_version:
        case None, None, None, None:
            return None, None, None, None
        case str() as kind, str() as surface, str() as channel_id, int() as policy_version if not isinstance(policy_version, bool):
            try:
                return ApprovalKind(kind), ApprovalSurface(surface), channel_id, policy_version
            except ValueError as error:
                raise PendingApprovalError("pending repair approval binding is invalid") from error
        case _:
            raise PendingApprovalError("pending repair approval binding is incomplete")
