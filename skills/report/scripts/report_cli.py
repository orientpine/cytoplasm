#!/usr/bin/env python3
"""Private command surface for !report, !slides, and !script."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(_SCRIPT_DIR))
    report_core = import_module("report_core")
    report_llm = import_module("report_llm")
    report_sensitivity = import_module("report_sensitivity")
    report_drive = import_module("drive_publish")
else:
    report_core = import_module(".report_core", __package__)
    report_llm = import_module(".report_llm", __package__)
    report_sensitivity = import_module(".report_sensitivity", __package__)
    report_drive = import_module(".drive_publish", __package__)


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _output_path(directory: Path, kind: str, suffix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"{kind}-{stamp}{suffix}"


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _report(args: argparse.Namespace) -> int:
    notes = report_core.select_notes(Path(args.notes_root).expanduser(), limit=args.limit, query=args.query)
    if not notes:
        print("자료 부족: 선택 조건에 맞는 노트가 없습니다.")
        return 0
    rules_path = Path(os.environ.get("REPORT_RULES_PATH", _SCRIPT_DIR.parent / "configs" / "sensitivity-rules.yaml"))
    route = report_sensitivity.route_notes(notes, report_sensitivity.load_rules(rules_path))
    title = args.title or "연구 노트 보고서"
    draft = Path(args.response_file).read_text(encoding="utf-8") if args.response_file else report_llm.generate(
        report_core.build_prompt(notes, title), route
    )
    output = _output_path(_private_directory(Path(args.outputs_root).expanduser()), "report", ".md")
    _write_private(output, report_core.assemble_report(title, notes, draft))
    link = report_drive.publish_best_effort(output, "report")
    print(f"REPORT-CREATED path={output} drive={link} provider={route.provider} sensitive={str(route.sensitive).lower()} notes={len(notes)}")
    return 0


def _slides(args: argparse.Namespace) -> int:
    report = Path(args.report).expanduser().read_text(encoding="utf-8")
    deck = report_core.render_slides(report)
    output = _output_path(_private_directory(Path(args.outputs_root).expanduser()), "slides", ".html")
    _write_private(output, deck.html)
    link = report_drive.publish_best_effort(output, "report")
    print(f"SLIDES-CREATED path={output} drive={link} slides={len(deck.titles)}")
    return 0


def _script(args: argparse.Namespace) -> int:
    report = Path(args.report).expanduser().read_text(encoding="utf-8")
    titles = report_core.render_slides(report).titles
    output = _output_path(_private_directory(Path(args.outputs_root).expanduser()), "script", ".md")
    _write_private(output, report_core.generate_script(titles))
    link = report_drive.publish_best_effort(output, "report")
    print(f"SCRIPT-CREATED path={output} drive={link} slides={len(titles)}")
    return 0


def _organize(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc).isocalendar()
    week = f"{now.year}-W{now.week:02d}"
    index = report_core.organize_notes(Path(args.notes_root).expanduser(), week)
    print(f"NOTES-ORGANIZED path={index}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="report")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report")
    report.add_argument("--notes-root", default="~/notes")
    report.add_argument("--outputs-root", default="~/outputs")
    report.add_argument("--query", default="")
    report.add_argument("--limit", type=int, default=12)
    report.add_argument("--title", default="")
    report.add_argument("--response-file", default="")
    report.set_defaults(func=_report)
    slides = commands.add_parser("slides")
    slides.add_argument("--report", required=True)
    slides.add_argument("--outputs-root", default="~/outputs")
    slides.set_defaults(func=_slides)
    script = commands.add_parser("script")
    script.add_argument("--report", required=True)
    script.add_argument("--slides", default="")
    script.add_argument("--outputs-root", default="~/outputs")
    script.set_defaults(func=_script)
    organize = commands.add_parser("organize")
    organize.add_argument("--notes-root", default="~/notes")
    organize.set_defaults(func=_organize)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, report_llm.LlmInvocationError) as error:
        print(f"REPORT-REFUSED {error.__class__.__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
