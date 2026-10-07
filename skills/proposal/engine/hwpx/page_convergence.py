"""Bounded page-budget convergence with explicit measurement provenance.

Only paragraphs declared in ``sections[].optional_paragraphs`` may be added or
removed. Mandatory prose, including its numbers, units, quotations, and
citations, is immutable across rounds. PDF conversion is a smoke-tier
measurement; the structural estimator is always labeled as an estimate.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, cast
from xml.etree import ElementTree as ET

from ..contracts.layout_profile import (
    DEFAULT_CALIBRATED_CPP,
    LayoutProfile,
    LayoutSpec,
    SectionLayout,
    estimate_pages,
)

SECTION_RE: Final = re.compile(r"Contents/section\d+\.xml")
IMMUTABLE_RE: Final = re.compile(
    r"""https?://\S+|doi:\s*\S+|\[[^\]\n]+\]|\([^()\n]*\b(?:19|20)\d{2}\b[^()\n]*\)|\d+(?:[.,]\d+)*(?:\s?(?:%|mm|cm|km|kg|mg|g|m|L|ml|kW|MW|GHz|MHz|원|억원|만원|년|개월|일|시간|분|초|쪽|페이지|건|명|개|회|배|도))?""",
    re.IGNORECASE,
)
FIGURE_WIDTHS_MM: Final = (140, 130, 120)

MeasurementStatus = Literal["OK", "MEASUREMENT-UNAVAILABLE"]
ConvergenceStatus = Literal["converged", "PAGE_BUDGET_UNMET"]


class ImmutableContentError(ValueError):
    """A rendered candidate changed mandatory numeric/citation content."""

    round_number: int | None = None
    candidate_path: str | None = None
    candidate_status: Literal["refused"] = "refused"


@dataclass(frozen=True, slots=True)
class PageMeasurement:
    status: MeasurementStatus
    pages: int | None
    kind: str
    detail: str = ""


class PageMeasurer(Protocol):
    kind: str

    def measure(self, path: str | Path) -> PageMeasurement: ...


@dataclass(frozen=True, slots=True)
class OptionalParagraph:
    paragraph_id: str
    text: str
    priority: int = 100
    included: bool = False
    section_index: int = 0

    def __post_init__(self) -> None:
        if not self.paragraph_id.strip():
            raise ValueError("optional paragraph id must not be empty")
        if not self.text.strip():
            raise ValueError(f"optional paragraph {self.paragraph_id!r} must not be empty")


@dataclass(frozen=True, slots=True)
class CandidateState:
    enabled_optional_ids: frozenset[str] = frozenset()
    figure_width_mm: int = FIGURE_WIDTHS_MM[0]

    def __post_init__(self) -> None:
        if self.figure_width_mm not in FIGURE_WIDTHS_MM:
            raise ValueError(f"figure width must be one of {FIGURE_WIDTHS_MM}")


CandidateRenderer = Callable[[CandidateState, Path], None]
CandidateSectionReader = Callable[[CandidateState, Path], Sequence[str]]


@dataclass(frozen=True, slots=True)
class ConvergenceBundle:
    mandatory_sections: tuple[str, ...]
    optional_paragraphs: tuple[OptionalParagraph, ...]
    output_dir: Path
    render_candidate: CandidateRenderer
    read_candidate_sections: CandidateSectionReader
    initial_enabled_optional_ids: frozenset[str] = frozenset()
    best_candidate_path: Path | None = None

    def __post_init__(self) -> None:
        ids = [paragraph.paragraph_id for paragraph in self.optional_paragraphs]
        if len(ids) != len(set(ids)):
            raise ValueError("optional paragraph ids must be unique")
        unknown = self.initial_enabled_optional_ids - set(ids)
        if unknown:
            raise ValueError(f"unknown initially-enabled optional paragraphs: {sorted(unknown)}")


@dataclass(frozen=True, slots=True)
class ConvergenceRound:
    round_number: int
    pages: int | None
    measurement_status: MeasurementStatus
    candidate_hash: str
    candidate_path: str
    enabled_optional_ids: tuple[str, ...]
    figure_width_mm: int
    delta_chars: int


@dataclass(frozen=True, slots=True)
class ConvergenceResult:
    final_pages: int | None
    rounds: tuple[ConvergenceRound, ...]
    status: ConvergenceStatus
    best_candidate_path: str
    measurement_kind: str
    publishable: bool
    termination_reason: str

    def as_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


class EstimateMeasurer:
    """Structural HWPX measurement using ``estimate_pages`` and page-break bands."""

    kind: str = "estimate"

    def measure(self, path: str | Path) -> PageMeasurement:
        try:
            prose_chars = 0
            figure_heights: list[int] = []
            table_heights: list[int] = []
            explicit_breaks = 0
            with zipfile.ZipFile(path) as archive:
                section_names = sorted(name for name in archive.namelist() if SECTION_RE.fullmatch(name))
                if not section_names:
                    return PageMeasurement(
                        "MEASUREMENT-UNAVAILABLE", None, self.kind, "no Contents/sectionN.xml entries"
                    )
                for name in section_names:
                    root = ET.fromstring(archive.read(name))
                    prose_chars += sum(len(node.text or "") for node in root.findall(".//{*}t"))
                    explicit_breaks += sum(
                        node.get("pageBreak") in {"1", "true"} for node in root.findall(".//{*}p")
                    )
                    for picture in root.findall(".//{*}pic"):
                        size = picture.find("./{*}curSz")
                        if size is not None:
                            figure_heights.append(int(size.get("height", "0")))
                    for table in root.findall(".//{*}tbl"):
                        table_heights.append(max(0, int(table.get("height", "0"))))
            estimated = estimate_pages(
                LayoutSpec(
                    sections=(
                        SectionLayout(
                            prose_chars=prose_chars,
                            figure_heights=tuple(figure_heights),
                            table_heights=tuple(table_heights),
                        ),
                    )
                )
            )
            pages = max(1 + explicit_breaks, math.ceil(estimated))
            return PageMeasurement(
                "OK",
                pages,
                self.kind,
                f"structural estimate={estimated:.2f}; explicit_page_floor={1 + explicit_breaks}",
            )
        except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
            return PageMeasurement("MEASUREMENT-UNAVAILABLE", None, self.kind, str(exc))


class SofficePdfMeasurer:
    """Smoke-tier HWPX-to-PDF page measurement guarded by one cached seed probe."""

    kind: str = "pdf-smoke"
    _probe_cache: dict[tuple[str, str, str], tuple[bool, str]] = {}

    def __init__(self, seed_path: str | Path) -> None:
        self.seed_path: Path = Path(seed_path)

    def availability(self) -> PageMeasurement:
        soffice = shutil.which("soffice")
        pdfinfo = shutil.which("pdfinfo")
        if soffice is None or pdfinfo is None:
            missing = "soffice" if soffice is None else "pdfinfo"
            return PageMeasurement("MEASUREMENT-UNAVAILABLE", None, self.kind, f"{missing} unavailable")
        key = (soffice, pdfinfo, str(self.seed_path.resolve()))
        if key not in self._probe_cache:
            measurement = self._convert_and_count(self.seed_path, soffice, pdfinfo, timeout=30)
            self._probe_cache[key] = (
                measurement.status == "OK",
                measurement.detail or (
                    f"seed conversion returned {measurement.pages} pages"
                    if measurement.pages is not None
                    else "seed conversion failed"
                ),
            )
        available, detail = self._probe_cache[key]
        return PageMeasurement("OK", None, self.kind, detail) if available else PageMeasurement(
            "MEASUREMENT-UNAVAILABLE", None, self.kind, detail
        )

    def measure(self, path: str | Path) -> PageMeasurement:
        availability = self.availability()
        if availability.status != "OK":
            return availability
        soffice = shutil.which("soffice")
        pdfinfo = shutil.which("pdfinfo")
        assert soffice is not None and pdfinfo is not None
        return self._convert_and_count(Path(path), soffice, pdfinfo, timeout=60)

    def _convert_and_count(
        self, source: Path, soffice: str, pdfinfo: str, *, timeout: int
    ) -> PageMeasurement:
        try:
            with tempfile.TemporaryDirectory(prefix="kimm-page-measure-") as directory:
                completed = subprocess.run(
                    [soffice, "--headless", "--convert-to", "pdf", "--outdir", directory, str(source)],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                pdf = Path(directory) / f"{source.stem}.pdf"
                if completed.returncode != 0 or not pdf.is_file():
                    detail = (completed.stderr or completed.stdout).strip().replace("\n", " ")
                    return PageMeasurement(
                        "MEASUREMENT-UNAVAILABLE",
                        None,
                        self.kind,
                        f"soffice conversion failed rc={completed.returncode}: {detail}".strip(),
                    )
                info = subprocess.run(
                    [pdfinfo, str(pdf)], capture_output=True, text=True, timeout=15, check=False
                )
                match = re.search(r"^Pages:\s+(\d+)\s*$", info.stdout, re.MULTILINE)
                if info.returncode != 0 or match is None:
                    return PageMeasurement(
                        "MEASUREMENT-UNAVAILABLE", None, self.kind, "pdfinfo Pages unavailable"
                    )
                return PageMeasurement("OK", int(match.group(1)), self.kind, "LibreOffice PDF smoke")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PageMeasurement("MEASUREMENT-UNAVAILABLE", None, self.kind, str(exc))


class AutoPageMeasurer:
    """Prefer PDF smoke measurement; explicitly fall back to structural estimate."""

    kind: str = "auto"

    def __init__(self, pdf: SofficePdfMeasurer, estimate: EstimateMeasurer | None = None) -> None:
        self.pdf: SofficePdfMeasurer = pdf
        self.estimate: EstimateMeasurer = estimate or EstimateMeasurer()

    def measure(self, path: str | Path) -> PageMeasurement:
        pdf_measurement = self.pdf.measure(path)
        if pdf_measurement.status == "OK":
            return pdf_measurement
        estimate = self.estimate.measure(path)
        detail = f"pdf unavailable ({pdf_measurement.detail}); {estimate.detail}"
        return PageMeasurement(estimate.status, estimate.pages, estimate.kind, detail)


def immutable_content_multiset(sections: Sequence[str]) -> Counter[str]:
    return Counter(
        " ".join(match.group(0).split())
        for section in sections
        for match in IMMUTABLE_RE.finditer(section)
    )


def assert_immutable_content(
    baseline_sections: Sequence[str], candidate_sections: Sequence[str]
) -> Counter[str]:
    baseline = immutable_content_multiset(baseline_sections)
    candidate = immutable_content_multiset(candidate_sections)
    if candidate != baseline:
        removed = baseline - candidate
        added = candidate - baseline
        raise ImmutableContentError(
            f"IMMUTABLE_CONTENT_CHANGED: removed={dict(removed)}, added={dict(added)}"
        )
    return candidate


def read_hwpx_candidate_sections(_state: CandidateState, path: Path) -> tuple[str, ...]:
    """Read the text that a rendered HWPX candidate will present."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = sorted(name for name in archive.namelist() if SECTION_RE.fullmatch(name))
            if not names:
                raise ImmutableContentError("IMMUTABLE_CONTENT_UNREADABLE: no section XML entries")
            return tuple(
                "".join(node.text or "" for node in ET.fromstring(archive.read(name)).findall(".//{*}t"))
                for name in names
            )
    except (OSError, ET.ParseError, KeyError, zipfile.BadZipFile) as exc:
        raise ImmutableContentError(f"IMMUTABLE_CONTENT_UNREADABLE: {exc}") from exc


def converge_pages(
    bundle: ConvergenceBundle,
    *,
    profile: LayoutProfile,
    measurer: PageMeasurer,
    target: int = 30,
    tolerance: int = 1,
    max_rounds: int = 6,
) -> ConvergenceResult:
    """Render, measure, and refine no more than ``max_rounds`` candidates.

    Every rendered artifact is checked before measurement. An immutable-content
    violation refuses that round and terminates immediately with
    :class:`ImmutableContentError`; the exception records the refused round.
    """
    _ = profile  # The active profile is part of the public convergence contract.
    if target < 1 or tolerance < 0 or not 1 <= max_rounds <= 6:
        raise ValueError("target must be positive, tolerance non-negative, and max_rounds 1..6")
    bundle.output_dir.mkdir(parents=True, exist_ok=True)
    best_path = bundle.best_candidate_path or bundle.output_dir / "best-candidate.hwpx"
    state = CandidateState(bundle.initial_enabled_optional_ids, FIGURE_WIDTHS_MM[0])
    expected_mandatory = immutable_content_multiset(bundle.mandatory_sections)
    rendered_baseline: Counter[str] | None = None
    seen_hashes: set[str] = set()
    rounds: list[ConvergenceRound] = []
    best_distance: int | None = None
    best_pages: int | None = None
    unchanged_streak = 0
    previous_pages: int | None = None
    termination_reason = "max_rounds"
    measurement_kind = getattr(measurer, "kind", "unknown")

    for round_number in range(1, max_rounds + 1):
        candidate_hash = _candidate_hash(bundle, state)
        if candidate_hash in seen_hashes:
            termination_reason = "revisited_candidate_hash"
            break
        seen_hashes.add(candidate_hash)

        candidate_path = bundle.output_dir / f"candidate-r{round_number:02d}-{candidate_hash[:12]}.hwpx"
        temporary = candidate_path.with_suffix(candidate_path.suffix + ".tmp")
        temporary.unlink(missing_ok=True)
        try:
            bundle.render_candidate(state, temporary)
            if not temporary.is_file():
                raise RuntimeError("candidate renderer did not create its output")
            os.replace(temporary, candidate_path)
        finally:
            temporary.unlink(missing_ok=True)

        try:
            candidate_sections = bundle.read_candidate_sections(state, candidate_path)
            candidate_immutable = _mandatory_candidate_multiset(
                bundle, state, candidate_sections, expected_mandatory
            )
            if rendered_baseline is None:
                missing = expected_mandatory - candidate_immutable
                if missing:
                    raise ImmutableContentError(
                        f"IMMUTABLE_CONTENT_CHANGED: removed={dict(missing)}, added={{}}"
                    )
                rendered_baseline = candidate_immutable
            else:
                _assert_immutable_multiset(rendered_baseline, candidate_immutable)
        except ImmutableContentError as exc:
            exc.round_number = round_number
            exc.candidate_path = str(candidate_path)
            raise

        measurement = measurer.measure(candidate_path)
        measurement_kind = measurement.kind
        pages = measurement.pages
        delta_chars = _delta_chars(bundle, state, pages, target)
        rounds.append(
            ConvergenceRound(
                round_number,
                pages,
                measurement.status,
                candidate_hash,
                str(candidate_path),
                tuple(sorted(state.enabled_optional_ids)),
                state.figure_width_mm,
                delta_chars,
            )
        )
        if pages is not None:
            distance = abs(target - pages)
            if best_distance is None or distance < best_distance:
                _atomic_copy(candidate_path, best_path)
                best_distance, best_pages = distance, pages
            if distance <= tolerance:
                return ConvergenceResult(
                    pages,
                    tuple(rounds),
                    "converged",
                    str(best_path),
                    measurement.kind,
                    True,
                    "within_tolerance",
                )
        elif not best_path.is_file():
            _atomic_copy(candidate_path, best_path)

        if measurement.status != "OK" or pages is None:
            termination_reason = "measurement_unavailable"
            break
        if previous_pages == pages:
            unchanged_streak += 1
        else:
            unchanged_streak = 0
        previous_pages = pages
        if unchanged_streak >= 2:
            termination_reason = "two_unchanged_page_counts"
            break
        next_state = _refine_state(bundle, state, pages, target, delta_chars)
        if _candidate_hash(bundle, next_state) in seen_hashes:
            termination_reason = "revisited_candidate_hash"
            break
        state = next_state
    else:
        termination_reason = "max_rounds"

    return ConvergenceResult(
        best_pages,
        tuple(rounds),
        "PAGE_BUDGET_UNMET",
        str(best_path),
        measurement_kind,
        False,
        termination_reason,
    )


def _mandatory_candidate_multiset(
    bundle: ConvergenceBundle,
    state: CandidateState,
    candidate_sections: Sequence[str],
    expected_mandatory: Counter[str],
) -> Counter[str]:
    candidate = immutable_content_multiset(candidate_sections)
    optional = Counter[str]()
    for paragraph in bundle.optional_paragraphs:
        if paragraph.paragraph_id in state.enabled_optional_ids:
            optional.update(immutable_content_multiset((paragraph.text,)))
    for token, count in optional.items():
        removable = min(count, max(0, candidate[token] - expected_mandatory[token]))
        candidate[token] -= removable
        if candidate[token] == 0:
            del candidate[token]
    return candidate


def _assert_immutable_multiset(baseline: Counter[str], candidate: Counter[str]) -> None:
    if candidate != baseline:
        removed = baseline - candidate
        added = candidate - baseline
        raise ImmutableContentError(
            f"IMMUTABLE_CONTENT_CHANGED: removed={dict(removed)}, added={dict(added)}"
        )


def _delta_chars(
    bundle: ConvergenceBundle, state: CandidateState, pages: int | None, target: int
) -> int:
    if pages is None:
        return 0
    current_chars = sum(len(section) for section in bundle.mandatory_sections) + sum(
        len(paragraph.text)
        for paragraph in bundle.optional_paragraphs
        if paragraph.paragraph_id in state.enabled_optional_ids
    )
    limit = max(1, round(current_chars * 0.1))
    raw = round((target - pages) * DEFAULT_CALIBRATED_CPP * 0.7)
    return max(-limit, min(limit, raw))


def _refine_state(
    bundle: ConvergenceBundle,
    state: CandidateState,
    pages: int,
    target: int,
    delta_chars: int,
) -> CandidateState:
    enabled = set(state.enabled_optional_ids)
    if pages < target:
        available = sorted(
            (item for item in bundle.optional_paragraphs if item.paragraph_id not in enabled),
            key=lambda item: (item.priority, item.paragraph_id),
        )
        _select_until(available, enabled, max(1, delta_chars), add=True)
        return CandidateState(frozenset(enabled), state.figure_width_mm)

    removable = sorted(
        (item for item in bundle.optional_paragraphs if item.paragraph_id in enabled),
        key=lambda item: (-item.priority, item.paragraph_id),
    )
    if removable:
        _select_until(removable, enabled, max(1, -delta_chars), add=False)
        return CandidateState(frozenset(enabled), state.figure_width_mm)
    width_index = FIGURE_WIDTHS_MM.index(state.figure_width_mm)
    next_width = FIGURE_WIDTHS_MM[min(width_index + 1, len(FIGURE_WIDTHS_MM) - 1)]
    return CandidateState(frozenset(enabled), next_width)


def _select_until(
    paragraphs: Sequence[OptionalParagraph], enabled: set[str], target_chars: int, *, add: bool
) -> None:
    selected_chars = 0
    for paragraph in paragraphs:
        if add:
            enabled.add(paragraph.paragraph_id)
        else:
            enabled.remove(paragraph.paragraph_id)
        selected_chars += len(paragraph.text)
        if selected_chars >= target_chars:
            break


def _candidate_hash(bundle: ConvergenceBundle, state: CandidateState) -> str:
    payload = {
        "mandatory_sections": bundle.mandatory_sections,
        "enabled_optional_ids": sorted(state.enabled_optional_ids),
        "figure_width_mm": state.figure_width_mm,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.")
    os.close(descriptor)
    temporary = Path(name)
    try:
        _ = shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)



__all__ = [
    "AutoPageMeasurer",
    "CandidateState",
    "ConvergenceBundle",
    "ConvergenceResult",
    "ConvergenceRound",
    "EstimateMeasurer",
    "ImmutableContentError",
    "OptionalParagraph",
    "PageMeasurement",
    "PageMeasurer",
    "SofficePdfMeasurer",
    "read_hwpx_candidate_sections",
    "assert_immutable_content",
    "converge_pages",
    "immutable_content_multiset",
]
