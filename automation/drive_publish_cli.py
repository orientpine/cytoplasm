"""Publish one deliverable to the canonical Drive tree from the command line.

Every caller of the publishing facade was skill code, so a session that authored a
deliverable for the owner had no command to run and left the file at a local path.
That is how a 용역공정표 template addressed to eight participating institutions ended
up in `~/Documents` on 2026-08-26. The convention already said Drive; what was missing
was a way to obey it.

This adds NO upload logic. It parses arguments and calls `drive_outputs.publish`, so
the single-facade rule and `tests/unit/test_drive_outputs_conformance.py` keep holding.
Failures are loud on purpose: a person running this by hand must not read silence as
success, which is why it calls `publish` rather than `publish_best_effort`.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Final, Sequence

from automation.drive_outputs import publish
from automation.drive_taxonomy import CATEGORIES, TaxonomyError, category

ENABLED_ENV: Final = "DRIVE_PUBLISH_ENABLED"

#: Refused before any Drive call — a bad kind, a gate-only kind, or a missing file.
EXIT_REFUSED: Final = 2
#: The opt-in is not set. Nothing was uploaded and the caller is told so.
EXIT_DISABLED: Final = 3
#: The facade itself failed; its exception type is reported, never swallowed.
EXIT_FAILED: Final = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drive-publish",
        description="산출물 1건을 표준 Drive 트리에 발행한다 (automation.drive_outputs 파사드 경유).",
    )
    parser.add_argument(
        "--kind", required=True,
        help="카테고리 — " + " | ".join(sorted(CATEGORIES)),
    )
    parser.add_argument("--title", required=True, help="산출물 제목 (파일명·sticky 키의 기준)")
    parser.add_argument("--project", default=None, help="과제 폴더 한 단 (선택)")
    parser.add_argument("--on", default=None, help="기준 날짜 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument(
        "--companion", action="append", default=[], metavar="PATH",
        help="본 산출물과 함께 올리는 부속 파일 (반복 가능)",
    )
    parser.add_argument("paths", nargs="+", help="발행할 로컬 파일 경로")
    return parser


def _refuse(message: str) -> int:
    print(f"DRIVE-PUBLISH-REFUSED {message}", file=sys.stderr)
    return EXIT_REFUSED


def _existing(raw: Sequence[str]) -> tuple[list[Path], list[str]]:
    paths = [Path(value).expanduser() for value in raw]
    return paths, [str(path) for path in paths if not path.is_file()]


def main(argv: Sequence[str] | None = None) -> int:
    """Parse, refuse fast, then delegate to the one facade that may touch Drive."""
    args = build_parser().parse_args(argv)

    try:
        selected = category(args.kind)
    except TaxonomyError as error:
        return _refuse(f"kind={args.kind!r}: {error}")
    if selected.gate_only:
        return _refuse(f"kind={args.kind!r} 는 gate-only — 전용 반출 게이트로만 나간다")
    if selected.skill_owned:
        return _refuse(
            f"kind={args.kind!r} 는 스킬이 소유한다 — 그 파이프라인을 거치지 않은 문서는 "
            f"원장도 관리번호도 갖지 못한다. 이 명령으로 만든다: {selected.skill_owned}"
        )

    paths, missing = _existing(args.paths)
    companions, missing_companions = _existing(args.companion)
    if missing or missing_companions:
        return _refuse("파일 없음: " + ", ".join(missing + missing_companions))

    try:
        on = date.fromisoformat(args.on) if args.on else None
    except ValueError:
        return _refuse(f"--on 날짜 형식이 아니다: {args.on!r}")

    if os.environ.get(ENABLED_ENV) != "1":
        print(
            f"DRIVE-PUBLISH-DISABLED {ENABLED_ENV} 가 1 이 아니다 — 아무것도 올리지 않았다.",
            file=sys.stderr,
        )
        return EXIT_DISABLED

    artifacts = [(path, args.title if len(paths) == 1 else path.stem) for path in paths]
    try:
        result = publish(
            args.kind, args.title, artifacts,
            companions=companions, on=on, project=args.project,
        )
    except Exception as error:  # noqa: BLE001 — a hand-run publish reports, never hides
        print(f"DRIVE-PUBLISH-FAIL {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_FAILED

    print(f"PUBLISHED kind={args.kind} title={args.title} result={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
