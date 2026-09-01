"""Single destination-aware guard for proposal payload routing."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias, cast

from . import proposal_sensitivity


Destination: TypeAlias = Literal["image-api", "refine-host", "render", "drive"]
Classification: TypeAlias = Literal["public", "owner-private", "patent-sensitive"]
PayloadKind: TypeAlias = Literal["content", "index"]

_DESTINATIONS: Final = frozenset({"image-api", "refine-host", "render", "drive"})
_CLASSIFICATIONS: Final = frozenset({"public", "owner-private", "patent-sensitive"})
_OWNER_SOURCE_PREFIXES: Final = ("obsidian:", "wiki:", "note:")
_OWNER_MARKER: Final = re.compile(r"(?im)^\s*(?:obsidian|wiki|note):")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_INDEX_FIELDS: Final = frozenset({"source_key", "sha256", "collected_at", "sections"})
OWNER_CONTROLLED_REFINE_HOSTS: Final = frozenset(
    {"codex", "codex-oauth", "openai-codex", "hermes-codex"}
)


@dataclass(frozen=True, slots=True)
class RouteDecision:
    allowed: bool
    reason: str


class RouteRefused(RuntimeError):
    """A payload may not cross the requested proposal boundary."""


def _rules_path() -> Path:
    skill_root = Path(__file__).resolve().parents[1]
    return Path(
        os.environ.get(
            "PROPOSAL_RULES_PATH",
            str(skill_root / "configs" / "sensitivity-rules.yaml"),
        )
    ).expanduser()


def classify(payload: str, *, source_keys: tuple[str, ...] = ()) -> str:
    """Classify with patent sensitivity taking precedence over private-note provenance."""
    route = proposal_sensitivity.route_proposal(
        payload, proposal_sensitivity.load_rules(_rules_path())
    )
    if route.sensitive:
        return "patent-sensitive"
    private_source = any(
        key.strip().lower().startswith(_OWNER_SOURCE_PREFIXES) for key in source_keys
    )
    if private_source or _OWNER_MARKER.search(payload) is not None:
        return "owner-private"
    return "public"


def _allow(reason: str) -> RouteDecision:
    return RouteDecision(True, reason)


def _refuse(reason: str) -> RouteDecision:
    raise RouteRefused(reason)


def _owner_controlled_refine_hosts() -> frozenset[str]:
    configured = os.environ.get("PROPOSAL_REFINE_ALLOWED_HOSTS")
    if configured is None:
        return OWNER_CONTROLLED_REFINE_HOSTS
    return frozenset(host.strip().lower() for host in configured.split(",") if host.strip())


def _field_within_limit(value: object) -> bool:
    if isinstance(value, str):
        return len(value) <= 512
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) <= 512


def _is_well_formed_index(payload: str) -> bool:
    try:
        document = cast(object, json.loads(payload))
    except (json.JSONDecodeError, TypeError):
        return False

    entries: list[object]
    if isinstance(document, list):
        entries = cast(list[object], document)
    elif isinstance(document, dict):
        entries = [cast(dict[object, object], document)]
    else:
        return False

    if not entries:
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        raw_entry = cast(dict[object, object], entry)
        if not all(isinstance(key, str) for key in raw_entry):
            return False
        index_entry = cast(dict[str, object], raw_entry)
        if not {"source_key", "sha256"}.issubset(index_entry) or not set(
            index_entry
        ).issubset(_INDEX_FIELDS):
            return False
        if not all(_field_within_limit(value) for value in index_entry.values()):
            return False
        source_key = index_entry["source_key"]
        digest = index_entry["sha256"]
        if not isinstance(source_key, str) or not source_key:
            return False
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            return False
        if "collected_at" in index_entry and not isinstance(
            index_entry["collected_at"], str
        ):
            return False
        if "sections" in index_entry:
            sections = index_entry["sections"]
            if not isinstance(sections, list) or not all(
                isinstance(section, str) for section in cast(list[object], sections)
            ):
                return False
    return True


def assert_route_allowed(
    payload: str,
    destination: Destination,
    *,
    host: str | None = None,
    payload_kind: PayloadKind = "content",
    classification: str | None = None,
    source_keys: tuple[str, ...] = (),
) -> RouteDecision:
    """Enforce the complete proposal classification/destination truth table."""
    if destination not in _DESTINATIONS:
        return _refuse("unknown destination")
    if payload_kind not in {"content", "index"}:
        return _refuse("unknown payload kind")
    if destination == "drive" and payload_kind == "index" and not _is_well_formed_index(payload):
        return _refuse("index-shape-invalid")
    selected = (
        classify(payload, source_keys=source_keys) if classification is None else classification
    )
    if selected not in _CLASSIFICATIONS:
        return _refuse("unknown classification")

    if selected == "public":
        return _allow("public payload is allowed")
    if selected == "patent-sensitive":
        if destination == "drive":
            return _allow("patent-sensitive payload is allowed on owner-only Drive")
        if destination == "refine-host":
            descriptor = (host or "").strip().lower()
            if descriptor in _owner_controlled_refine_hosts():
                return _allow("patent-sensitive payload is allowed on an owner-controlled host")
            return _refuse("patent-sensitive refinement requires an owner-controlled host")
        return _refuse("patent-sensitive payload is denied at this destination")

    if destination == "drive" and payload_kind == "index":
        return _allow("owner-private source index is allowed on owner-only Drive")
    return _refuse("owner-private raw content is denied at this destination")
