"""Derive proposal versions from delta snapshots without rendering them.

Classifier contract
-------------------
Each INDEX record and its raw bytes are treated strictly as data. The classifier never imports,
evaluates, or executes delta content. An explicit JSON ``classification`` field is honored when it
names a supported policy; otherwise deterministic Korean/English markers and numeric-assignment
patterns select the policy. Mixed markers take the union of their impacts. Unrecognized content is
classified as ``ambiguous`` and takes the widest closure rather than silently regenerating too
little.

The policy is: style-only notes remain paragraph patches; background/prior-research updates target
section 1 and the prior-research table; target/KPI/TRL/numeric updates target sections 0, 2, and 4
and the KPI table; methodology/WP/schedule updates target section 3 and the Gantt table;
market/utilization updates target sections 0 and 4; title or goal-statement changes target every
section, every table, and every figure prompt. Unaffected figure PNGs are copied into the child
version, so reuse is observable through an unchanged ``png_sha256`` and a local file in ``images``.
Published HWPX/PDF outputs are never copied: the downstream pipeline always renders from its seed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

_SCRIPT_DIR = Path(__file__).absolute().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(_SCRIPT_DIR.parents[1]))
    __package__ = "proposal.scripts"

from . import proposal_delta, proposal_version  # noqa: E402

DELTA_CONFLICT_UNRESOLVED_EXIT: Final = 7
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_VERSION: Final = re.compile(r"^v[0-9]{6}$")
_NUMBER: Final = re.compile(
    r"(?<![A-Za-z0-9])[-+]?\d+(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s*(?:%|[A-Za-zµμ°/²³]+|[가-힣]+))?",
)
_ARTIFACT_SECTION: Final = re.compile(
    r"(?:^|[-_/])(?:section[-_]?|s)([0-4])(?:$|[-_. /])", re.IGNORECASE
)
_ASSIGNMENT: Final = re.compile(r"^\s*([^:=\n]{1,100}?)\s*[:=]\s*(.*?)\s*$")
_NATURAL_NUMBER: Final = re.compile(
    r"^\s*([가-힣A-Za-z][가-힣A-Za-z0-9 _/-]{0,60}?)(?:은|는|을|를)\s*"
    r"([-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:%|[A-Za-zµμ°/²³]+|[가-힣]+))?)"
)
_ALL_SECTIONS: Final = frozenset(range(5))
_ALL_TABLES: Final = frozenset({"prior-research", "tech-gap", "kpi", "gantt"})
_POLICY_ORDER: Final = (
    "style",
    "background",
    "targets",
    "methodology",
    "market",
    "title-goal",
    "ambiguous",
)
_POLICY_SECTIONS: Final = {
    "style": frozenset(),
    "background": frozenset({1}),
    "targets": frozenset({0, 2, 4}),
    "methodology": frozenset({3}),
    "market": frozenset({0, 4}),
    "title-goal": _ALL_SECTIONS,
    "ambiguous": _ALL_SECTIONS,
}
_POLICY_TABLES: Final = {
    "style": frozenset(),
    "background": frozenset({"prior-research"}),
    "targets": frozenset({"kpi"}),
    "methodology": frozenset({"gantt"}),
    "market": frozenset(),
    "title-goal": _ALL_TABLES,
    "ambiguous": _ALL_TABLES,
}
_CLASS_ALIASES: Final = {
    "style": "style",
    "style-only": "style",
    "wording": "style",
    "background": "background",
    "prior-research": "background",
    "research": "background",
    "target": "targets",
    "targets": "targets",
    "kpi": "targets",
    "trl": "targets",
    "numeric": "targets",
    "method": "methodology",
    "methodology": "methodology",
    "wp": "methodology",
    "schedule": "methodology",
    "market": "market",
    "utilization": "market",
    "title": "title-goal",
    "title-goal": "title-goal",
    "goal-statement": "title-goal",
    "ambiguous": "ambiguous",
}
_INDEX_REQUIRED: Final = frozenset({"source_key", "sha256", "collected_at", "sections"})
_INDEX_OPTIONAL: Final = frozenset(
    {"assertions", "classification", "source_type", "supersedes"}
)
_NAME_MARKERS: Final = (
    "과제명",
    "기관명",
    "연구책임자",
    "책임자",
    "담당자",
    "이름",
    "organization",
    "owner",
    "name",
    "title",
)


class ImproveError(RuntimeError):
    """The improvement transaction could not complete."""

    exit_code: int = 1


class ImproveInputError(ImproveError):
    """A parent version, directive, or delta snapshot is malformed."""

    exit_code = 2


@dataclass(frozen=True, slots=True)
class ConflictValue:
    source_key: str
    sha256: str
    value: str

    def payload(self) -> dict[str, str]:
        return {
            "sha256": self.sha256,
            "source_key": self.source_key,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class DeltaConflict:
    slot: str
    first: ConflictValue
    second: ConflictValue

    def payload(self) -> dict[str, object]:
        return {
            "first": self.first.payload(),
            "second": self.second.payload(),
            "slot": self.slot,
        }


class DeltaConflictUnresolved(ImproveError):
    """Contradictory values lack an explicit owner resolution."""

    exit_code = DELTA_CONFLICT_UNRESOLVED_EXIT

    def __init__(self, conflicts: Sequence[DeltaConflict]) -> None:
        self.conflicts = tuple(conflicts)
        details = "; ".join(
            f"slot={item.slot} source_a={item.first.source_key} "
            f"value_a={item.first.value} source_b={item.second.source_key} "
            f"value_b={item.second.value}"
            for item in self.conflicts
        )
        super().__init__(f"DELTA_CONFLICT_UNRESOLVED {details}")


@dataclass(frozen=True, slots=True)
class DeltaRecord:
    source_key: str
    sha256: str
    collected_at: str
    content: bytes
    raw_name: str
    index_sections: tuple[int, ...]
    supersedes: tuple[str, ...]
    index_assertions: tuple[tuple[str, str], ...]
    explicit_classification: str | None


@dataclass(frozen=True, slots=True)
class Assertion:
    slot: str
    value: str
    source_key: str
    sha256: str
    supersedes: tuple[str, ...] = ()
    parent: bool = False

    @property
    def comparable_value(self) -> str:
        normalized = unicodedata.normalize("NFKC", self.value).strip().casefold()
        if _NUMBER.fullmatch(normalized):
            return re.sub(r"[\s,]", "", normalized)
        return " ".join(normalized.split())

    def conflict_value(self) -> ConflictValue:
        return ConflictValue(self.source_key, self.sha256, self.value)


@dataclass(frozen=True, slots=True)
class DeltaClassification:
    source_key: str
    sha256: str
    classification: str
    policy_classes: tuple[str, ...]
    sections: frozenset[int]
    tables: frozenset[str]
    patch_only: bool
    figure_prompts: bool
    assertions: tuple[Assertion, ...]

    def payload(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "figure_prompts": self.figure_prompts,
            "patch_only": self.patch_only,
            "policy_classes": list(self.policy_classes),
            "sections": sorted(self.sections),
            "sha256": self.sha256,
            "source_key": self.source_key,
            "tables": sorted(self.tables),
        }


@dataclass(frozen=True, slots=True)
class RegenerationPlan:
    sections: frozenset[int]
    figures: frozenset[str]
    tables: frozenset[str]
    patch_only: tuple[str, ...]
    figure_prompts: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "figure_prompts": self.figure_prompts,
            "figures": sorted(self.figures),
            "patch_only": list(self.patch_only),
            "sections": sorted(self.sections),
            "tables": sorted(self.tables),
        }


@dataclass(frozen=True, slots=True)
class ImproveResult:
    slug: str
    parent: str
    version: str
    run_key: str
    reused: bool
    plan: RegenerationPlan
    reused_figures: int
    delta_sha256: tuple[str, ...]
    resolved_conflicts: tuple[dict[str, object], ...]

    def payload(self) -> dict[str, object]:
        return {
            "delta_sha256": list(self.delta_sha256),
            "parent": self.parent,
            "regeneration": self.plan.payload(),
            "resolved_conflicts": list(self.resolved_conflicts),
            "reused": self.reused,
            "reused_figures": self.reused_figures,
            "run_key": self.run_key,
            "slug": self.slug,
            "version": self.version,
        }


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ImproveInputError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_json(path: Path, description: str) -> object:
    if path.is_symlink() or not path.is_file():
        raise ImproveInputError(f"{description} is not a regular file: {path}")
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ImproveInputError(f"{description} is malformed: {path}") from error


def _mapping(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ImproveInputError(f"{description} must be an object")
    return cast(dict[str, object], value)


def _version_path(
    store: proposal_version.VersionStore, slug: str, version: str
) -> Path:
    if _VERSION.fullmatch(version) is None:
        raise ImproveInputError(f"invalid proposal version: {version}")
    path = store.resolve_slug_dir(slug) / "versions" / version
    if path.is_symlink() or not path.is_dir():
        raise ImproveInputError(f"proposal version does not exist: {version}")
    return path


def _manifest(path: Path) -> dict[str, object]:
    return _mapping(_read_json(path / "manifest.json", "manifest"), "manifest")


def _sections(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ImproveInputError("delta INDEX sections must be a list")
    result: list[int] = []
    for item in cast(list[object], value):
        if (
            not isinstance(item, int)
            or isinstance(item, bool)
            or item not in _ALL_SECTIONS
        ):
            raise ImproveInputError(
                "delta INDEX sections must contain integers from 0 through 4"
            )
        result.append(item)
    if len(set(result)) != len(result):
        raise ImproveInputError("delta INDEX sections must not contain duplicates")
    return tuple(result)


def _supersedes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    items: list[object] = [value] if isinstance(value, str) else []
    if isinstance(value, list):
        items = cast(list[object], value)
    if not items or not all(isinstance(item, str) and item for item in items):
        raise ImproveInputError(
            "delta INDEX supersedes must be a source key, SHA, or list"
        )
    return tuple(sorted(set(cast(list[str], items))))


def _assertion_items(value: object) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    assertions = _mapping(value, "delta assertions")
    result: list[tuple[str, str]] = []
    for slot, raw_value in assertions.items():
        if (
            not slot.strip()
            or not isinstance(raw_value, (str, int, float))
            or isinstance(raw_value, bool)
        ):
            raise ImproveInputError("delta assertions must map names to scalar values")
        result.append((slot, str(raw_value).strip()))
    return tuple(sorted(result))


def _raw_files(raw_dir: Path) -> dict[str, tuple[str, bytes]]:
    if not raw_dir.exists():
        return {}
    if raw_dir.is_symlink() or not raw_dir.is_dir():
        raise ImproveInputError("delta raw directory is invalid")
    result: dict[str, tuple[str, bytes]] = {}
    for path in sorted(raw_dir.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            raise ImproveInputError(
                f"delta raw entry is not a regular file: {path.name}"
            )
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest in result:
            raise ImproveInputError(f"duplicate delta raw content: {digest}")
        result[digest] = (path.name, content)
    return result


def _load_delta_records(
    store: proposal_version.VersionStore,
    slug: str,
    since_version: str,
    report: proposal_delta.DeltaReport,
) -> tuple[DeltaRecord, ...]:
    if report.slug != slug or report.since_version != since_version:
        raise ImproveInputError("delta report does not match the improve request")
    destination = report.destination.expanduser().resolve()
    slug_root = store.resolve_slug_dir(slug).resolve()
    try:
        destination.relative_to(slug_root)
    except ValueError as error:
        raise ImproveInputError(
            "delta report escapes the proposal workspace"
        ) from error
    if destination.is_symlink():
        raise ImproveInputError("delta report destination must not be a symlink")
    index_path = destination / "delta" / "INDEX.json"
    value = _read_json(index_path, "delta INDEX")
    if not isinstance(value, list):
        raise ImproveInputError("delta INDEX must contain a list")
    raw_by_sha = _raw_files(destination / "delta" / "raw")
    records: list[DeltaRecord] = []
    source_keys: set[str] = set()
    hashes: set[str] = set()
    for raw_entry in cast(list[object], value):
        entry = _mapping(raw_entry, "delta INDEX entry")
        fields = set(entry)
        if (
            not _INDEX_REQUIRED.issubset(fields)
            or fields - _INDEX_REQUIRED - _INDEX_OPTIONAL
        ):
            raise ImproveInputError("delta INDEX entry fields are invalid")
        source_key = entry.get("source_key")
        digest = entry.get("sha256")
        collected_at = entry.get("collected_at")
        if (
            not isinstance(source_key, str)
            or not source_key
            or len(source_key) > 512
            or source_key in source_keys
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or digest in hashes
            or not isinstance(collected_at, str)
            or not collected_at
        ):
            raise ImproveInputError("delta INDEX entry values are invalid")
        if digest not in raw_by_sha:
            raise ImproveInputError(
                f"delta INDEX SHA has no matching raw bytes: {digest}"
            )
        explicit = entry.get("classification")
        if explicit is not None and not isinstance(explicit, str):
            raise ImproveInputError("delta INDEX classification must be a string")
        source_type = entry.get("source_type")
        if source_type is not None and not isinstance(source_type, str):
            raise ImproveInputError("delta INDEX source_type must be a string")
        raw_name, content = raw_by_sha[digest]
        records.append(
            DeltaRecord(
                source_key,
                digest,
                collected_at,
                content,
                raw_name,
                _sections(entry["sections"]),
                _supersedes(entry.get("supersedes")),
                _assertion_items(entry.get("assertions")),
                explicit,
            )
        )
        source_keys.add(source_key)
        hashes.add(digest)
    if set(raw_by_sha) != hashes:
        raise ImproveInputError("delta raw directory contains unindexed content")
    return tuple(records)


def _structured_delta(content: bytes) -> dict[str, object]:
    try:
        value = cast(object, json.loads(content.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return {}
    return cast(dict[str, object], value)


def _contains(text: str, markers: Iterable[str]) -> bool:
    return any(marker.casefold() in text for marker in markers)


def _policy_classes(record: DeltaRecord) -> tuple[str, ...]:
    text = record.content.decode("utf-8", errors="replace").casefold()
    structured = _structured_delta(record.content)
    explicit_values = [record.explicit_classification, structured.get("classification")]
    selected: set[str] = set()
    unknown_explicit = False
    for explicit in explicit_values:
        if explicit is None:
            continue
        if (
            not isinstance(explicit, str)
            or explicit.strip().casefold() not in _CLASS_ALIASES
        ):
            unknown_explicit = True
            continue
        selected.add(_CLASS_ALIASES[explicit.strip().casefold()])
    title_change = bool(
        re.search(
            r"(?:제목|과제명|목표문|목표 문장)\s*(?:을|를|의)?\s*"
            r"(?:변경|수정|교체|갱신)|(?:title|goal statement)\s*(?:change|update)",
            text,
        )
    )
    if title_change:
        return ("title-goal",)
    if _contains(
        text, ("배경", "선행연구", "관련 연구", "문헌", "background", "prior research")
    ):
        selected.add("background")
    if _contains(
        text,
        (
            "kpi",
            "trl",
            "목표",
            "수치",
            "정량",
            "성능",
            "오차",
            "정확도",
            "target",
            "metric",
        ),
    ) or _NUMBER.search(text):
        selected.add("targets")
    if _contains(
        text,
        (
            "방법론",
            "방법",
            "워크패키지",
            "일정",
            "간트",
            "마일스톤",
            "work package",
            "schedule",
            "methodology",
            " wp ",
        ),
    ):
        selected.add("methodology")
    if _contains(
        text, ("시장", "활용", "사업화", "수요", "market", "commercial", "utilization")
    ):
        selected.add("market")
    if _contains(
        text, ("문체", "표현", "맞춤법", "오탈자", "윤문", "wording", "typo", "style")
    ):
        selected.add("style")
    if "style" in selected and len(selected) > 1:
        selected.remove("style")
    if unknown_explicit or not selected:
        return ("ambiguous",)
    return tuple(item for item in _POLICY_ORDER if item in selected)


def _normalize_slot(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"^[\s*#>\-–—•◦□]+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    stripped = re.sub(r"^(?:kpi|목표|정량|수치|변경)\s+", "", normalized)
    return stripped.strip(" -_/()[]") or normalized.strip(" -_/()[]")


def _value_for_line(slot: str, raw_value: str) -> str | None:
    numeric = _NUMBER.search(raw_value)
    if numeric is not None:
        return numeric.group(0).strip()
    normalized_slot = _normalize_slot(slot)
    if any(marker in normalized_slot for marker in _NAME_MARKERS):
        value = " ".join(raw_value.strip().split())
        return value or None
    return None


def _content_assertion_items(record: DeltaRecord) -> tuple[tuple[str, str], ...]:
    structured = _structured_delta(record.content)
    items = list(record.index_assertions)
    if "assertions" in structured:
        items.extend(_assertion_items(structured.get("assertions")))
    text = record.content.decode("utf-8", errors="replace")
    for line in text.splitlines():
        assignment = _ASSIGNMENT.match(line)
        if assignment is not None:
            slot = _normalize_slot(assignment.group(1))
            value = _value_for_line(slot, assignment.group(2))
            if slot and value is not None:
                items.append((slot, value))
            continue
        natural = _NATURAL_NUMBER.match(line)
        if natural is not None:
            items.append((_normalize_slot(natural.group(1)), natural.group(2).strip()))
    unique: dict[tuple[str, str], None] = {}
    for slot, value in items:
        normalized_slot = _normalize_slot(slot)
        if normalized_slot and value:
            unique[(normalized_slot, value)] = None
    return tuple(unique)


def _raw_supersedes(record: DeltaRecord) -> tuple[str, ...]:
    structured = _structured_delta(record.content)
    raw_value = structured.get("supersedes")
    embedded = _supersedes(raw_value) if raw_value is not None else ()
    return tuple(sorted(set((*record.supersedes, *embedded))))


def classify_delta(record: DeltaRecord) -> DeltaClassification:
    """Classify one validated delta and calculate its direct impact."""
    policies = _policy_classes(record)
    sections = frozenset(
        section for policy in policies for section in _POLICY_SECTIONS[policy]
    )
    tables = frozenset(table for policy in policies for table in _POLICY_TABLES[policy])
    patch_only = policies == ("style",)
    classification = policies[0] if len(policies) == 1 else "mixed"
    supersedes = _raw_supersedes(record)
    assertions = tuple(
        Assertion(slot, value, record.source_key, record.sha256, supersedes)
        for slot, value in _content_assertion_items(record)
    )
    return DeltaClassification(
        record.source_key,
        record.sha256,
        classification,
        policies,
        sections,
        tables,
        patch_only,
        "title-goal" in policies or "ambiguous" in policies,
        assertions,
    )


def _parent_assertions(
    parent_manifest: dict[str, object], parent_version: str
) -> tuple[Assertion, ...]:
    raw = parent_manifest.get("assertions", parent_manifest.get("recorded_values", {}))
    if raw is None:
        return ()
    values = _mapping(raw, "parent assertions")
    result: list[Assertion] = []
    for raw_slot, raw_record in values.items():
        slot = _normalize_slot(raw_slot)
        if isinstance(raw_record, dict):
            record = _mapping(raw_record, "parent assertion")
            value = record.get("value")
            source_key = record.get("source_key", f"parent:{parent_version}")
            digest = record.get("sha256", "")
        else:
            value = raw_record
            source_key = f"parent:{parent_version}"
            digest = ""
        if (
            not slot
            or not isinstance(value, (str, int, float))
            or isinstance(value, bool)
            or not isinstance(source_key, str)
            or not source_key
            or not isinstance(digest, str)
            or digest
            and _SHA256.fullmatch(digest) is None
        ):
            raise ImproveInputError("parent assertions are malformed")
        result.append(
            Assertion(slot, str(value).strip(), source_key, digest, parent=True)
        )
    return tuple(sorted(result, key=lambda item: item.slot))


def _validate_resolutions(
    resolutions: Mapping[str, str], records: Sequence[DeltaRecord]
) -> dict[str, str]:
    known = {record.sha256 for record in records}
    result: dict[str, str] = {}
    for digest, action in resolutions.items():
        if _SHA256.fullmatch(digest) is None or action != "accept":
            raise ImproveInputError("resolution must use <delta-sha256>=accept")
        if digest not in known:
            raise ImproveInputError(f"resolution references an unknown delta: {digest}")
        result[digest] = action
    return dict(sorted(result.items()))


def _candidate_sort_key(item: Assertion) -> tuple[int, str, str]:
    return (1 if item.parent else 0, item.source_key, item.sha256)


def _is_superseded(item: Assertion, candidates: Sequence[Assertion]) -> bool:
    return any(
        other.comparable_value != item.comparable_value
        and (
            item.source_key in other.supersedes
            or bool(item.sha256)
            and item.sha256 in other.supersedes
        )
        for other in candidates
    )


def _resolution_payload(
    slot: str,
    accepted: Assertion,
    candidates: Sequence[Assertion],
    via: str,
) -> dict[str, object]:
    superseded = sorted(
        (
            item.conflict_value().payload()
            for item in candidates
            if item.comparable_value != accepted.comparable_value
        ),
        key=lambda item: (item["source_key"], item["sha256"]),
    )
    return {
        "accepted": accepted.conflict_value().payload(),
        "slot": slot,
        "superseded": superseded,
        "via": via,
    }


def _resolve_assertions(
    parent: Sequence[Assertion],
    classifications: Sequence[DeltaClassification],
    resolutions: Mapping[str, str],
) -> tuple[dict[str, dict[str, str]], tuple[dict[str, object], ...]]:
    by_slot: dict[str, list[Assertion]] = {}
    for assertion in (
        *parent,
        *(
            item
            for classification in classifications
            for item in classification.assertions
        ),
    ):
        by_slot.setdefault(assertion.slot, []).append(assertion)
    merged: dict[str, dict[str, str]] = {}
    resolved: list[dict[str, object]] = []
    conflicts: list[DeltaConflict] = []
    for slot in sorted(by_slot):
        candidates = sorted(by_slot[slot], key=_candidate_sort_key)
        values = {item.comparable_value for item in candidates}
        accepted: Assertion | None = None
        via = "agreement"
        if len(values) == 1:
            accepted = candidates[0]
        else:
            directed = [item for item in candidates if item.sha256 in resolutions]
            directed_values = {item.comparable_value for item in directed}
            if directed and len(directed_values) == 1:
                accepted = sorted(directed, key=_candidate_sort_key)[0]
                via = "directive"
            elif not directed:
                survivors = [
                    item for item in candidates if not _is_superseded(item, candidates)
                ]
                survivor_values = {item.comparable_value for item in survivors}
                if survivors and len(survivor_values) == 1:
                    accepted = sorted(survivors, key=_candidate_sort_key)[0]
                    via = "supersedes"
            if accepted is None:
                representatives: dict[str, Assertion] = {}
                basis = directed if len(directed_values) > 1 else candidates
                for item in basis:
                    representatives.setdefault(item.comparable_value, item)
                ordered = sorted(representatives.values(), key=_candidate_sort_key)
                for index, first in enumerate(ordered):
                    for second in ordered[index + 1 :]:
                        conflicts.append(
                            DeltaConflict(
                                slot, first.conflict_value(), second.conflict_value()
                            )
                        )
                continue
            resolved.append(_resolution_payload(slot, accepted, candidates, via))
        merged[slot] = {
            "sha256": accepted.sha256,
            "source_key": accepted.source_key,
            "value": accepted.value,
        }
    if conflicts:
        raise DeltaConflictUnresolved(conflicts)
    return merged, tuple(resolved)


def _figure_ids(parent_path: Path) -> tuple[str, ...]:
    path = parent_path / "figures.json"
    if not path.exists():
        return ()
    value = _read_json(path, "figures.json")
    if not isinstance(value, list):
        raise ImproveInputError("figures.json must contain a list")
    result: list[str] = []
    for item in cast(list[object], value):
        record = _mapping(item, "figure record")
        figure_id = record.get("figure_id")
        if not isinstance(figure_id, str) or not figure_id or figure_id in result:
            raise ImproveInputError("figure identifiers are invalid")
        result.append(figure_id)
    return tuple(result)


def regeneration_plan(
    classifications: Sequence[DeltaClassification], figure_ids: Sequence[str]
) -> RegenerationPlan:
    """Take the deterministic union/closure of all classified delta impacts."""
    sections = frozenset(
        section
        for classification in classifications
        for section in classification.sections
    )
    tables = frozenset(
        table for classification in classifications for table in classification.tables
    )
    figure_prompts = any(
        classification.figure_prompts for classification in classifications
    )
    figures = frozenset(figure_ids) if figure_prompts else frozenset()
    patch_only = tuple(
        sorted(
            classification.source_key
            for classification in classifications
            if classification.patch_only
        )
    )
    return RegenerationPlan(sections, figures, tables, patch_only, figure_prompts)


def _copy_file(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ImproveInputError(f"reusable artifact is not a regular file: {source}")
    _atomic_write(destination, source.read_bytes())


def _copy_tree(
    source: Path,
    destination: Path,
    *,
    excluded: Callable[[Path, Path], bool] | None = None,
) -> None:
    if not source.exists():
        return
    if source.is_symlink() or not source.is_dir():
        raise ImproveInputError(f"reusable artifact directory is invalid: {source}")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(source)
        if path.is_symlink():
            raise ImproveInputError(
                f"reusable artifact symlink is forbidden: {relative}"
            )
        if excluded is not None and excluded(relative, path):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.chmod(0o700)
        elif path.is_file():
            _copy_file(path, target)
        else:
            raise ImproveInputError(f"reusable artifact is invalid: {relative}")


def _artifact_section(relative: Path) -> int | None:
    match = _ARTIFACT_SECTION.search(relative.as_posix())
    return int(match.group(1)) if match is not None else None


def _copy_reusable_artifacts(
    parent_path: Path, staging_path: Path, plan: RegenerationPlan
) -> None:
    _copy_tree(parent_path / "inputs", staging_path / "inputs")
    _copy_tree(parent_path / "corpus", staging_path / "corpus")

    def exclude_output(relative: Path, path: Path) -> bool:
        if path.is_file() and path.suffix.casefold() in {".hwp", ".hwpx", ".pdf"}:
            return True
        section = _artifact_section(relative)
        return section is not None and section in plan.sections

    parent_out = parent_path / "out"
    _copy_tree(parent_out, staging_path / "out", excluded=exclude_output)
    refined_drafts = parent_out / "drafts.refined.json"
    if refined_drafts.exists():
        _copy_file(refined_drafts, staging_path / "out" / "drafts.json")


def _figure_evidence_sha(record: dict[str, object]) -> str:
    source_ids = record.get("source_claim_ids")
    if not isinstance(source_ids, list) or not all(
        isinstance(item, str) for item in cast(list[object], source_ids)
    ):
        raise ImproveInputError("figure source_claim_ids are invalid")
    material = _canonical_json(sorted(cast(list[str], source_ids))).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _prepare_figures(
    parent_path: Path, staging_path: Path, plan: RegenerationPlan
) -> tuple[RegenerationPlan, tuple[dict[str, str], ...]]:
    figures_path = parent_path / "figures.json"
    if not figures_path.exists():
        return plan, ()
    value = _read_json(figures_path, "figures.json")
    if not isinstance(value, list):
        raise ImproveInputError("figures.json must contain a list")
    records: list[dict[str, object]] = []
    impacted = set(plan.figures)
    reused: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in cast(list[object], value):
        record = dict(_mapping(item, "figure record"))
        figure_id = record.get("figure_id")
        png_sha = record.get("png_sha256")
        if (
            not isinstance(figure_id, str)
            or not figure_id
            or figure_id in seen
            or not isinstance(png_sha, str)
            or png_sha
            and _SHA256.fullmatch(png_sha) is None
        ):
            raise ImproveInputError("figure record values are invalid")
        seen.add(figure_id)
        source = parent_path / "images" / f"{figure_id}.png"
        if (
            figure_id not in impacted
            and png_sha
            and source.is_file()
            and not source.is_symlink()
        ):
            content = source.read_bytes()
            actual_sha = hashlib.sha256(content).hexdigest()
            if content.startswith(b"\x89PNG\r\n\x1a\n") and actual_sha == png_sha:
                _atomic_write(staging_path / "images" / source.name, content)
                reused.append(
                    {
                        "evidence_sha256": _figure_evidence_sha(record),
                        "figure_id": figure_id,
                        "png_sha256": actual_sha,
                    }
                )
            else:
                impacted.add(figure_id)
                record["png_sha256"] = ""
        else:
            impacted.add(figure_id)
            record["png_sha256"] = ""
        records.append(record)
    updated_plan = RegenerationPlan(
        plan.sections,
        frozenset(impacted),
        plan.tables,
        plan.patch_only,
        plan.figure_prompts,
    )
    _atomic_write(staging_path / "figures.json", _json_bytes(records))
    return updated_plan, tuple(reused)


def _prepare_tables(
    parent_path: Path, staging_path: Path, plan: RegenerationPlan
) -> None:
    path = parent_path / "tables.json"
    if not path.exists():
        return
    value = _read_json(path, "tables.json")
    if not isinstance(value, list):
        raise ImproveInputError("tables.json must contain a list")
    reusable: list[dict[str, object]] = []
    for item in cast(list[object], value):
        record = _mapping(item, "table record")
        kind = record.get("kind")
        if not isinstance(kind, str) or kind not in _ALL_TABLES:
            raise ImproveInputError("table kind is invalid")
        if kind not in plan.tables:
            reusable.append(dict(record))
    _atomic_write(staging_path / "tables.json", _json_bytes(reusable))


def _record_sections(
    classification: DeltaClassification, record: DeltaRecord
) -> list[int]:
    if classification.patch_only:
        return sorted(record.index_sections)
    return sorted(classification.sections)


def _stage_delta_snapshot(
    staging_path: Path,
    records: Sequence[DeltaRecord],
    classifications: Sequence[DeltaClassification],
) -> None:
    by_sha = {item.sha256: item for item in classifications}
    entries: list[dict[str, object]] = []
    for record in records:
        classification = by_sha[record.sha256]
        entry: dict[str, object] = {
            "collected_at": record.collected_at,
            "sections": _record_sections(classification, record),
            "sha256": record.sha256,
            "source_key": record.source_key,
        }
        if record.supersedes:
            entry["supersedes"] = (
                record.supersedes[0]
                if len(record.supersedes) == 1
                else list(record.supersedes)
            )
        if record.index_assertions:
            entry["assertions"] = dict(record.index_assertions)
        _atomic_write(staging_path / "delta" / "raw" / record.raw_name, record.content)
        entries.append(entry)
    _atomic_write(staging_path / "delta" / "INDEX.json", _json_bytes(entries))


def _request_settings(
    parent_manifest: dict[str, object],
    *,
    profile: str | None,
    template_sha256: str | None,
    pins: object | None,
) -> tuple[str, str, object]:
    raw_request = parent_manifest.get("request", {})
    request = raw_request if isinstance(raw_request, dict) else {}
    inherited_profile = request.get(
        "profile", parent_manifest.get("profile", "30-page")
    )
    selected_profile = profile or inherited_profile
    if selected_profile not in {"30-page", "10-page"}:
        raise ImproveInputError("profile must be 30-page or 10-page")
    inherited_template = request.get(
        "template_sha256", parent_manifest.get("template_sha256", "")
    )
    selected_template = (
        inherited_template if template_sha256 is None else template_sha256
    )
    if not isinstance(selected_template, str):
        raise ImproveInputError("template_sha256 must be a string")
    inherited_pins = request.get("pins", parent_manifest.get("pins", {}))
    selected_pins = inherited_pins if pins is None else pins
    try:
        _canonical_json(selected_pins)
    except (TypeError, ValueError) as error:
        raise ImproveInputError("pins must be JSON-serializable") from error
    return cast(str, selected_profile), selected_template, selected_pins


def _directives(
    resolutions: Mapping[str, str], records: Sequence[DeltaRecord] = ()
) -> dict[str, object]:
    directives: dict[str, object] = {"resolve": dict(sorted(resolutions.items()))}
    by_reference = {
        reference: record.sha256
        for record in records
        for reference in (record.source_key, record.sha256)
    }
    supersedes = sorted(
        {
            f"supersedes:{winner.sha256}>{by_reference[loser]}"
            for winner in records
            for loser in _raw_supersedes(winner)
            if loser in by_reference and by_reference[loser] != winner.sha256
        }
    )
    if supersedes:
        directives["supersedes"] = supersedes
    return directives


def _regeneration_from_value(value: object) -> RegenerationPlan:
    plan = _mapping(value, "regeneration plan")
    sections = _sections(plan.get("sections", []))
    figures = plan.get("figures", [])
    tables = plan.get("tables", [])
    patch_only = plan.get("patch_only", [])
    figure_prompts = plan.get("figure_prompts", False)
    if (
        not isinstance(figures, list)
        or not all(isinstance(item, str) for item in cast(list[object], figures))
        or not isinstance(tables, list)
        or not all(
            isinstance(item, str) and item in _ALL_TABLES
            for item in cast(list[object], tables)
        )
        or not isinstance(patch_only, list)
        or not all(isinstance(item, str) for item in cast(list[object], patch_only))
        or not isinstance(figure_prompts, bool)
    ):
        raise ImproveInputError("regeneration plan is malformed")
    return RegenerationPlan(
        frozenset(sections),
        frozenset(cast(list[str], figures)),
        frozenset(cast(list[str], tables)),
        tuple(cast(list[str], patch_only)),
        figure_prompts,
    )


def _changes(manifest: dict[str, object]) -> list[str]:
    delta_hashes = manifest.get("delta_sha256", [])
    plan = _regeneration_from_value(manifest.get("regeneration_plan", {}))
    count = len(delta_hashes) if isinstance(delta_hashes, list) else 0
    sections = ",".join(str(item) for item in sorted(plan.sections)) or "none"
    tables = ",".join(sorted(plan.tables)) or "none"
    figures = ",".join(sorted(plan.figures)) or "none"
    return [
        f"Applied {count} delta(s)",
        f"Regenerate sections: {sections}",
        f"Regenerate tables: {tables}",
        f"Regenerate figures: {figures}",
    ]


def _changelog_entry(manifest: dict[str, object]) -> dict[str, object]:
    version = manifest.get("version")
    parent = manifest.get("parent")
    if not isinstance(version, str) or not isinstance(parent, str):
        raise ImproveInputError("promoted improve manifest is malformed")
    return {
        "changes": _changes(manifest),
        "conflicts_resolved": manifest.get("conflicts_resolved", []),
        "delta_classifications": manifest.get("delta_classifications", []),
        "delta_sha256": manifest.get("delta_sha256", []),
        "parent": parent,
        "regeneration_plan": manifest.get("regeneration_plan", {}),
        "run_key": manifest.get("run_key", ""),
        "version": version,
    }


def _ensure_changelog(
    store: proposal_version.VersionStore, slug: str, manifest: dict[str, object]
) -> None:
    slug_dir = store.resolve_slug_dir(slug)
    path = slug_dir / "changelog.json"
    entries: list[object] = []
    if path.exists():
        value = _read_json(path, "changelog.json")
        if not isinstance(value, list):
            raise ImproveInputError("changelog.json must contain a list")
        entries = cast(list[object], value)
    version = manifest.get("version")
    matching = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and cast(dict[object, object], entry).get("version") == version
    ]
    expected = _changelog_entry(manifest)
    if matching:
        if len(matching) != 1 or matching[0] != expected:
            raise ImproveInputError(
                f"changelog entry disagrees with manifest: {version}"
            )
        store.regenerate_changelog(slug)
        return
    store.append_changelog(slug, expected)


def _result_from_manifest(
    slug: str, manifest: dict[str, object], *, reused: bool
) -> ImproveResult:
    version = manifest.get("version")
    parent = manifest.get("parent")
    run_key = manifest.get("run_key")
    delta_hashes = manifest.get("delta_sha256", [])
    reused_figures = manifest.get("reused_figures", 0)
    resolved = manifest.get("conflicts_resolved", [])
    if (
        not isinstance(version, str)
        or not isinstance(parent, str)
        or not isinstance(run_key, str)
        or _SHA256.fullmatch(run_key) is None
        or not isinstance(delta_hashes, list)
        or not all(
            isinstance(item, str) and _SHA256.fullmatch(item) is not None
            for item in cast(list[object], delta_hashes)
        )
        or not isinstance(reused_figures, int)
        or isinstance(reused_figures, bool)
        or not isinstance(resolved, list)
        or not all(isinstance(item, dict) for item in cast(list[object], resolved))
    ):
        raise ImproveInputError("improve manifest result fields are malformed")
    return ImproveResult(
        slug,
        parent,
        version,
        run_key,
        reused,
        _regeneration_from_value(manifest.get("regeneration_plan", {})),
        reused_figures,
        tuple(cast(list[str], delta_hashes)),
        tuple(cast(list[dict[str, object]], resolved)),
    )


def _reused_result(
    store: proposal_version.VersionStore, slug: str, version: str
) -> ImproveResult:
    manifest = _manifest(_version_path(store, slug, version))
    _ensure_changelog(store, slug, manifest)
    return _result_from_manifest(slug, manifest, reused=True)


def improve_from_report(
    slug: str,
    *,
    since_version: str,
    report: proposal_delta.DeltaReport,
    resolutions: Mapping[str, str] | None = None,
    profile: str | None = None,
    template_sha256: str | None = None,
    pins: object | None = None,
    store: proposal_version.VersionStore | None = None,
) -> ImproveResult:
    """Validate one collector report, derive its plan, and atomically promote a child."""
    active_store = store or proposal_version.VersionStore.from_environment()
    parent_path = _version_path(active_store, slug, since_version)
    parent_manifest = _manifest(parent_path)
    records = _load_delta_records(active_store, slug, since_version, report)
    if not records:
        raise ImproveInputError("delta report contains no unapplied deltas")
    resolution_map = _validate_resolutions(resolutions or {}, records)
    classifications = tuple(classify_delta(record) for record in records)
    assertions, resolved = _resolve_assertions(
        _parent_assertions(parent_manifest, since_version),
        classifications,
        resolution_map,
    )
    plan = regeneration_plan(classifications, _figure_ids(parent_path))
    selected_profile, selected_template, selected_pins = _request_settings(
        parent_manifest,
        profile=profile,
        template_sha256=template_sha256,
        pins=pins,
    )
    delta_hashes = tuple(sorted(record.sha256 for record in records))
    directives = _directives(resolution_map, records)
    parent_sha = active_store.manifest_sha256(slug, since_version)
    run_key = active_store.compute_run_key(
        parent_sha,
        delta_hashes,
        directives,
        selected_template,
        selected_profile,
        selected_pins,
    )
    reused = active_store.find_by_run_key(slug, run_key)
    if reused is not None:
        return _reused_result(active_store, slug, reused)
    if active_store.head(slug) != since_version:
        raise proposal_version.HeadCasConflict(
            f"HEAD is not requested parent {since_version}; rerun with the current HEAD"
        )
    staged = active_store.begin(slug, run_key)
    if isinstance(staged, proposal_version.Reused):
        return _reused_result(active_store, slug, staged.version)
    # A run-key directory may be residue from a process killed before promotion. Never promote it
    # in place: discard it and rebuild every artifact from validated inputs.
    active_store.abort(slug, staged)
    clean = active_store.begin(slug, run_key)
    if isinstance(clean, proposal_version.Reused):
        return _reused_result(active_store, slug, clean.version)
    marker = clean.path / ".improve-staging.json"
    _atomic_write(marker, _json_bytes({"parent": since_version, "run_key": run_key}))
    promoted = False
    try:
        _copy_reusable_artifacts(parent_path, clean.path, plan)
        plan, reused_records = _prepare_figures(parent_path, clean.path, plan)
        _prepare_tables(parent_path, clean.path, plan)
        _stage_delta_snapshot(clean.path, records, classifications)
        request = {
            "delta_hashes": list(delta_hashes),
            "directives": directives,
            "pins": selected_pins,
            "profile": selected_profile,
            "template_sha256": selected_template,
        }
        manifest: dict[str, object] = {
            "assertions": assertions,
            "conflicts_resolved": list(resolved),
            "delta_classifications": [item.payload() for item in classifications],
            "delta_sha256": list(delta_hashes),
            "improve": {"since": since_version},
            "parent": since_version,
            "regeneration_plan": plan.payload(),
            "request": request,
            "reused_figure_records": list(reused_records),
            "reused_figures": len(reused_records),
            "schema_version": 2,
        }
        marker.unlink()
        version = active_store.promote(slug, clean, manifest)
        promoted = True
    except BaseException:
        if not promoted and clean.path.exists():
            active_store.abort(slug, clean)
        raise
    published_manifest = _manifest(_version_path(active_store, slug, version))
    _ensure_changelog(active_store, slug, published_manifest)
    return _result_from_manifest(slug, published_manifest, reused=False)


def _ledger_snapshot(store: proposal_version.VersionStore, slug: str) -> bytes | None:
    path = store.resolve_slug_dir(slug) / "delta-ledger.json"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ImproveInputError("delta ledger is invalid")
    return path.read_bytes()


def _restore_ledger(
    store: proposal_version.VersionStore, slug: str, snapshot: bytes | None
) -> None:
    path = store.resolve_slug_dir(slug) / "delta-ledger.json"
    if snapshot is None:
        if path.is_symlink():
            raise ImproveInputError("delta ledger became a symlink")
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, snapshot)


def _ancestor_delta_hashes(
    store: proposal_version.VersionStore, slug: str, version: str
) -> set[str]:
    hashes: set[str] = set()
    current: str | None = version
    visited: set[str] = set()
    while current is not None:
        if current in visited:
            raise ImproveInputError("proposal version ancestry contains a cycle")
        visited.add(current)
        manifest = _manifest(_version_path(store, slug, current))
        value = manifest.get("delta_sha256", [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) and _SHA256.fullmatch(item) is not None
            for item in cast(list[object], value)
        ):
            raise ImproveInputError("ancestor delta hashes are malformed")
        hashes.update(cast(list[str], value))
        parent = manifest.get("parent")
        if parent is not None and not isinstance(parent, str):
            raise ImproveInputError("proposal version parent is malformed")
        current = cast(str | None, parent)
    return hashes


def _is_delta_inbox(path: Path) -> bool:
    if path.name.startswith(".") or path.is_symlink() or not path.is_dir():
        return False
    if (path / ".improve-staging.json").exists() or not (
        path / "delta" / "INDEX.json"
    ).is_file():
        return False
    for candidate in path.rglob("*"):
        if candidate.is_file() and "delta" not in candidate.relative_to(path).parts[:1]:
            return False
    return True


def _pending_records(
    store: proposal_version.VersionStore,
    slug: str,
    since_version: str,
    scratch: Path,
) -> tuple[tuple[DeltaRecord, ...], tuple[Path, ...]]:
    staging_root = store.resolve_slug_dir(slug) / "staging"
    records: list[DeltaRecord] = []
    used: list[Path] = []
    for path in sorted(staging_root.iterdir(), key=lambda item: item.name):
        if path == scratch or not _is_delta_inbox(path):
            continue
        report = proposal_delta.DeltaReport(slug, since_version, path, (), ())
        records.extend(_load_delta_records(store, slug, since_version, report))
        used.append(path)
    return tuple(records), tuple(used)


def _deduplicate_records(records: Iterable[DeltaRecord]) -> tuple[DeltaRecord, ...]:
    by_hash: dict[str, DeltaRecord] = {}
    for record in sorted(records, key=lambda item: (item.sha256, item.source_key)):
        prior = by_hash.get(record.sha256)
        if prior is None:
            by_hash[record.sha256] = record
            continue
        if prior.content != record.content:
            raise ImproveInputError(f"delta SHA collision: {record.sha256}")
        # Prefer the record carrying an explicit owner supersession directive.
        if record.supersedes and not prior.supersedes:
            by_hash[record.sha256] = record
    return tuple(by_hash[digest] for digest in sorted(by_hash))


def _write_merged_report(destination: Path, records: Sequence[DeltaRecord]) -> None:
    delta = destination / "delta"
    if delta.exists():
        if delta.is_symlink() or not delta.is_dir():
            raise ImproveInputError("delta scratch directory is invalid")
        shutil.rmtree(delta)
    entries: list[dict[str, object]] = []
    for record in records:
        _atomic_write(delta / "raw" / record.raw_name, record.content)
        entry: dict[str, object] = {
            "collected_at": record.collected_at,
            "sections": list(record.index_sections),
            "sha256": record.sha256,
            "source_key": record.source_key,
        }
        if record.supersedes:
            entry["supersedes"] = (
                record.supersedes[0]
                if len(record.supersedes) == 1
                else list(record.supersedes)
            )
        if record.index_assertions:
            entry["assertions"] = dict(record.index_assertions)
        if record.explicit_classification is not None:
            entry["classification"] = record.explicit_classification
        entries.append(entry)
    _atomic_write(delta / "INDEX.json", _json_bytes(entries))


def _run_key_for_hashes(
    store: proposal_version.VersionStore,
    slug: str,
    since_version: str,
    hashes: Sequence[str],
    resolutions: Mapping[str, str],
    records: Sequence[DeltaRecord],
    *,
    profile: str | None,
    template_sha256: str | None,
    pins: object | None,
) -> str:
    parent = _manifest(_version_path(store, slug, since_version))
    selected_profile, selected_template, selected_pins = _request_settings(
        parent,
        profile=profile,
        template_sha256=template_sha256,
        pins=pins,
    )
    return store.compute_run_key(
        store.manifest_sha256(slug, since_version),
        hashes,
        _directives(resolutions, records),
        selected_template,
        selected_profile,
        selected_pins,
    )


def _reused_child_without_new_deltas(
    store: proposal_version.VersionStore,
    slug: str,
    since_version: str,
    resolutions: Mapping[str, str],
    *,
    profile: str | None,
    template_sha256: str | None,
    pins: object | None,
) -> ImproveResult | None:
    parent_manifest = _manifest(_version_path(store, slug, since_version))
    selected_profile, selected_template, selected_pins = _request_settings(
        parent_manifest,
        profile=profile,
        template_sha256=template_sha256,
        pins=pins,
    )
    expected_directives = _directives(resolutions)
    versions_dir = store.resolve_slug_dir(slug) / "versions"
    matches: list[str] = []
    for path in sorted(versions_dir.iterdir(), key=lambda item: item.name):
        if (
            path.is_symlink()
            or not path.is_dir()
            or _VERSION.fullmatch(path.name) is None
        ):
            continue
        manifest = _manifest(path)
        request_value = manifest.get("request")
        if not isinstance(request_value, dict):
            continue
        request = cast(dict[object, object], request_value)
        if (
            manifest.get("parent") == since_version
            and request.get("directives") == expected_directives
            and request.get("profile") == selected_profile
            and request.get("template_sha256") == selected_template
            and request.get("pins") == selected_pins
        ):
            matches.append(path.name)
    if len(matches) > 1:
        raise ImproveInputError("multiple child versions match an empty delta rerun")
    return _reused_result(store, slug, matches[0]) if matches else None


def improve_proposal(
    slug: str,
    *,
    since_version: str,
    resolutions: Mapping[str, str] | None = None,
    profile: str | None = None,
    template_sha256: str | None = None,
    pins: object | None = None,
    sources: Iterable[proposal_delta.DeltaSource] | None = None,
    knowledge: object | None = None,
) -> ImproveResult:
    """Collect accumulated deltas, consume clean delta inboxes, and derive the next version."""
    store = proposal_version.VersionStore.from_environment()
    _version_path(store, slug, since_version)
    slug_root = store.resolve_slug_dir(slug)
    staging_root = slug_root / "staging"
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    scratch = Path(tempfile.mkdtemp(prefix=".improve-collect-", dir=staging_root))
    ledger_before = _ledger_snapshot(store, slug)
    completed = False
    pending_paths: tuple[Path, ...] = ()
    try:
        report = proposal_delta.collect_deltas(
            slug,
            since_version=since_version,
            dest_dir=scratch,
            knowledge=knowledge,
            sources=sources,
        )
        fresh = _load_delta_records(store, slug, since_version, report)
        pending, pending_paths = _pending_records(store, slug, since_version, scratch)
        baseline = _ancestor_delta_hashes(store, slug, since_version)
        records = _deduplicate_records(
            record for record in (*pending, *fresh) if record.sha256 not in baseline
        )
        duplicate_hashes = {
            item.sha256
            for item in report.skipped
            if item.reason == "DUPLICATE-DELTA" and item.sha256 not in baseline
        }
        requested_hashes = tuple(
            sorted({record.sha256 for record in records} | duplicate_hashes)
        )
        resolution_map = dict(sorted((resolutions or {}).items()))
        if requested_hashes:
            run_key = _run_key_for_hashes(
                store,
                slug,
                since_version,
                requested_hashes,
                resolution_map,
                records,
                profile=profile,
                template_sha256=template_sha256,
                pins=pins,
            )
            reused = store.find_by_run_key(slug, run_key)
            if reused is not None:
                result = _reused_result(store, slug, reused)
                completed = True
                return result
        missing = set(requested_hashes) - {record.sha256 for record in records}
        if missing:
            joined = ",".join(sorted(missing))
            raise ImproveInputError(
                f"duplicate delta raw snapshot is unavailable: {joined}"
            )
        if not records:
            prior = _reused_child_without_new_deltas(
                store,
                slug,
                since_version,
                resolution_map,
                profile=profile,
                template_sha256=template_sha256,
                pins=pins,
            )
            if prior is None:
                raise ImproveInputError("no unapplied proposal deltas were collected")
            completed = True
            return prior
        _write_merged_report(scratch, records)
        merged = proposal_delta.DeltaReport(slug, since_version, scratch, (), ())
        result = improve_from_report(
            slug,
            since_version=since_version,
            report=merged,
            resolutions=resolution_map,
            profile=profile,
            template_sha256=template_sha256,
            pins=pins,
            store=store,
        )
        completed = True
        return result
    finally:
        if not completed:
            _restore_ledger(store, slug, ledger_before)
        if completed:
            for path in pending_paths:
                if path.exists() and _is_delta_inbox(path):
                    shutil.rmtree(path)
        if scratch.exists() and not scratch.is_symlink():
            shutil.rmtree(scratch)


def version_status(
    store: proposal_version.VersionStore, slug: str
) -> dict[str, object]:
    """Return the immutable version identifiers and current HEAD."""
    head = store.head(slug)
    versions_dir = store.resolve_slug_dir(slug) / "versions"
    versions: list[str] = []
    for path in sorted(versions_dir.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            raise ImproveInputError(f"symlink version is forbidden: {path.name}")
        if path.is_dir() and _VERSION.fullmatch(path.name):
            _manifest(path)
            versions.append(path.name)
    published = (
        head is not None
        and (versions_dir / head / "publish-receipt.json").is_file()
        and not (versions_dir / head / "publish-receipt.json").is_symlink()
    )
    return {
        "head": head,
        "slug": slug,
        "state": "published" if published else "versioned",
        "version": head,
        "versions": versions,
    }


def _parse_resolution_values(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        digest, separator, action = value.partition("=")
        if not separator or _SHA256.fullmatch(digest) is None or action != "accept":
            raise ImproveInputError("--resolve must use <delta-sha256>=accept")
        if digest in result and result[digest] != action:
            raise ImproveInputError(f"conflicting resolution directive: {digest}")
        result[digest] = action
    return dict(sorted(result.items()))


def version_command(args: argparse.Namespace) -> int:
    try:
        payload = version_status(
            proposal_version.VersionStore.from_environment(), args.slug
        )
        print(
            json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            if args.json
            else payload
        )
        return 0
    except (ImproveError, proposal_version.VersionError, OSError) as error:
        print(f"PROPOSAL-VERSION-ERROR {error}", file=sys.stderr)
        return getattr(error, "exit_code", 1)


def improve_command(args: argparse.Namespace) -> int:
    try:
        result = improve_proposal(
            args.slug,
            since_version=args.since,
            resolutions=_parse_resolution_values(args.resolve),
            profile=args.profile,
        )
        payload = result.payload()
        print(
            json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            if args.json
            else payload
        )
        return 0
    except DeltaConflictUnresolved as error:
        print(str(error), file=sys.stderr)
        return DELTA_CONFLICT_UNRESOLVED_EXIT
    except (
        ImproveError,
        proposal_delta.DeltaError,
        proposal_version.VersionError,
        OSError,
    ) as error:
        print(f"PROPOSAL-IMPROVE-ERROR {error}", file=sys.stderr)
        return getattr(error, "exit_code", 1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    version = commands.add_parser("version")
    version.add_argument("--slug", required=True)
    version.add_argument("--json", action="store_true")
    version.set_defaults(func=version_command)
    improve = commands.add_parser("improve")
    improve.add_argument("--slug", required=True)
    improve.add_argument("--since", required=True)
    improve.add_argument("--resolve", action="append", default=[])
    improve.add_argument("--profile", choices=("30-page", "10-page"))
    improve.add_argument("--json", action="store_true")
    improve.set_defaults(func=improve_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
