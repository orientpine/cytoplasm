from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from typing import cast

from ..contracts import CitationStatus, DocRecord, EvidenceUnit
from .ingest import SUPPORTED_EXTENSIONS, RawDoc, ingest_path
from .materialize import entity_key, materialize, numeric_values
from .normalize import normalize
from .pms import SENSITIVITY_TO_CITATION_STATUS


def _collect_files(directory: Path) -> list[Path]:
    return sorted(
        (
            file
            for file in directory.iterdir()
            if file.is_file() and file.suffix.lower() in SUPPORTED_EXTENSIONS
        ),
        key=lambda file: file.name,
    )


def _predict_citation_status(unit: EvidenceUnit) -> CitationStatus:
    return SENSITIVITY_TO_CITATION_STATUS[unit.sensitivity_flag]


def _candidate_unit_ids(candidate_files: list[Path], all_units: list[EvidenceUnit]) -> set[str]:
    candidate_doc_ids: set[str] = set()
    for candidate_file in candidate_files:
        try:
            raw = ingest_path(str(candidate_file))
            doc = normalize(raw)
            candidate_doc_ids.add(doc.doc_id)
        except Exception:
            pass

    candidate_unit_ids: set[str] = set()
    for unit in all_units:
        for provenance in unit.provenances:
            if provenance.source_id in candidate_doc_ids:
                candidate_unit_ids.add(unit.unit_id)
                break
    return candidate_unit_ids


def _doc_ids(files: list[Path]) -> set[str]:
    doc_ids: set[str] = set()
    for file in files:
        try:
            doc_ids.add(normalize(ingest_path(str(file))).doc_id)
        except Exception:
            pass
    return doc_ids


def _has_new_conflicting_value(unit: EvidenceUnit, units: list[EvidenceUnit], corpus_doc_ids: set[str]) -> bool:
    candidate_values = set(numeric_values(unit.fact))
    if not candidate_values:
        return False

    existing_values: set[str] = set()
    unit_entity_key = entity_key(unit.fact)
    for other in units:
        if other.unit_id == unit.unit_id or entity_key(other.fact) != unit_entity_key:
            continue
        if any(provenance.source_id in corpus_doc_ids for provenance in other.provenances):
            existing_values.update(numeric_values(other.fact))

    return bool(existing_values and not candidate_values.issubset(existing_values))


def lint(
    corpus_dir: Path,
    candidate_dir: Path,
    warn_only: bool = False,
) -> int:
    corpus_files = _collect_files(corpus_dir)
    candidate_files = _collect_files(candidate_dir)
    all_files = sorted(set(corpus_files) | set(candidate_files), key=lambda file: file.name)

    if not all_files:
        print("WARNING: No files found in corpus or candidate directories", file=sys.stderr)
        return 0

    raw_docs: list[RawDoc] = []
    for file in all_files:
        try:
            raw_docs.append(ingest_path(str(file)))
        except Exception as exc:
            print(f"WARNING: Could not ingest {file}: {exc}", file=sys.stderr)

    docs: list[DocRecord] = []
    for raw in raw_docs:
        try:
            docs.append(normalize(raw))
        except Exception as exc:
            print(f"WARNING: Could not normalize {raw.path}: {exc}", file=sys.stderr)

    if not docs:
        print("WARNING: No documents ingested", file=sys.stderr)
        return 0

    units = materialize(docs)
    candidate_ids = _candidate_unit_ids(candidate_files, units)
    corpus_doc_ids = _doc_ids(corpus_files)

    has_violation = False
    for unit in units:
        if unit.unit_id not in candidate_ids:
            continue

        status = _predict_citation_status(unit)
        if status != CitationStatus.PUBLIC:
            print(
                f"[{status.value}] unit={unit.unit_id[:8]} fact={unit.fact[:60]!r}",
                file=sys.stderr,
            )
            has_violation = True

        if unit.conflict and _has_new_conflicting_value(unit, units, corpus_doc_ids):
            print(
                f"[CONFLICT] unit={unit.unit_id[:8]} fact={unit.fact[:60]!r}",
                file=sys.stderr,
            )
            has_violation = True

        for provenance in unit.provenances:
            if not provenance.source_url:
                print(
                    f"[WARN] unit={unit.unit_id[:8]}: no source_url (add front-matter)",
                    file=sys.stderr,
                )

    if not has_violation:
        print("OK: all candidate units are PUBLIC and conflict-free", file=sys.stderr)

    if has_violation and not warn_only:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m skills.proposal.engine.converter.corpus_lint",
        description="Pre-ingest lint: predict PUBLIC/REDACT/INTERNAL/conflict for candidate corpus files",
    )
    _ = parser.add_argument("--corpus", required=True, help="Existing corpus directory")
    _ = parser.add_argument(
        "--candidate-dir", required=True, help="Directory with candidate corpus files"
    )
    _ = parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Print warnings but always exit 0 (non-blocking mode)",
    )
    args: Namespace = parser.parse_args(argv)

    parsed_args = cast("dict[str, object]", vars(args))
    raw_corpus = parsed_args["corpus"]
    raw_candidate_dir = parsed_args["candidate_dir"]
    raw_warn_only = parsed_args["warn_only"]
    if not isinstance(raw_corpus, str):
        print("ERROR: --corpus must be a path", file=sys.stderr)
        return 1
    if not isinstance(raw_candidate_dir, str):
        print("ERROR: --candidate-dir must be a path", file=sys.stderr)
        return 1
    if not isinstance(raw_warn_only, bool):
        print("ERROR: --warn-only must be a boolean", file=sys.stderr)
        return 1

    corpus_dir = Path(raw_corpus)
    candidate_dir = Path(raw_candidate_dir)

    if not corpus_dir.exists() or not corpus_dir.is_dir():
        print(f"ERROR: corpus directory not found: {corpus_dir}", file=sys.stderr)
        return 1
    if not candidate_dir.exists() or not candidate_dir.is_dir():
        print(f"ERROR: candidate directory not found: {candidate_dir}", file=sys.stderr)
        return 1

    return lint(corpus_dir, candidate_dir, warn_only=raw_warn_only)


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["lint", "main"]
