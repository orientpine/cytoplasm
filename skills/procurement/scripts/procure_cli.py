"""CLI for the procurement doc-drafting skill (W4-4).

Subcommands mirror the DM conversation the agent drives with cha:
  preflight      template format check (records CONVERSION-REQUEST for .hwp)
  collect-start  preflight + open a collection session, print the questions
  collect-answer store one validated answer (품목/금액/업체/…), print next question
  collect-status show the current field mapping
  generate       refuse-if-missing → generate draft → read-back verify
  verify         standalone read-back assert with the format-appropriate parser
  review         send the review-request DM (attach ≤25MiB, Drive link beyond)

Exit codes: 0 ok | 2 usage/bad input | 3 unsupported template (no generation)
            5 missing fields (refused) | 6 review DM failure
            7 doc library missing (fail-closed) | 8 read-back verify failed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = _SCRIPTS_DIR.parents[2]
if (REPO_ROOT / "skills" / "procurement" / "scripts").is_dir():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
else:
    # Deployed layout: /srv/autophagy-skills/releases/procurement/<sha256>/scripts/ —
    # no importable `skills` package sits above it, so synthesize `skills` and
    # `skills.procurement` with __path__ at the skill root. Python then resolves every
    # subpackage (scripts, vendor, ...) itself, keeping the absolute
    # `from skills.procurement...` imports (used by submodules too) working from the
    # immutable store.
    import types

    _SKILL_ROOT = _SCRIPTS_DIR.parent
    if "skills" not in sys.modules:
        _pkg = types.ModuleType("skills")
        _pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["skills"] = _pkg
    if "skills.procurement" not in sys.modules:
        _sk = types.ModuleType("skills.procurement")
        _sk.__path__ = [str(_SKILL_ROOT)]  # type: ignore[attr-defined]
        sys.modules["skills.procurement"] = _sk
        sys.modules["skills"].procurement = _sk  # type: ignore[attr-defined]

from skills.procurement.scripts import procure_core as core  # noqa: E402
from skills.procurement.scripts import procurement_governed  # noqa: E402
from skills.procurement.scripts import procure_generate as gen  # noqa: E402
from skills.procurement.scripts import procure_hwpx, procure_registry as registry  # noqa: E402
from skills.procurement.scripts import procure_registry_cli as registry_cli  # noqa: E402
from skills.procurement.scripts import procure_review as review  # noqa: E402
from skills.procurement.scripts.procure_core import Session, SessionError, load_session, save_session, sessions_dir  # noqa: E402


def _sessions_dir() -> Path:
    return sessions_dir()


def _audit(event: str, detail: str) -> None:
    log = Path(os.environ.get("PROCURE_AUDIT_LOG", "~/.hermes/procurement/audit.log")).expanduser()
    log.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {event} {detail}\n")


def _preflight(path: Path) -> core.Preflight:
    try:
        return core.preflight_path(path)
    except core.UnsupportedTemplate as error:
        _audit("CONVERSION-REQUESTED", f"file={path.name} reason={error}")
        print(error.conversion_request)
        sys.exit(3)


def _load_session(session_id: str) -> tuple[Path, Session]:
    try:
        return load_session(session_id)
    except SessionError as error:
        print(error, file=sys.stderr)
        sys.exit(2)


def _save_session(record: Path, session: Session) -> None:
    save_session(record, session)


def _print_questions(missing: tuple[str, ...]) -> None:
    for field in missing:
        print(f"QUESTION {field}: {core.QUESTIONS.get(field, '값을 알려주세요')}")


def cmd_preflight(args: argparse.Namespace) -> None:
    path = Path(args.file)
    result = _preflight(path)
    fields = ",".join(_template_fields(result, path))
    print(f"PREFLIGHT-OK format={result.format} parser={result.parser} placeholders={fields}")


def cmd_collect_start(args: argparse.Namespace) -> None:
    template = Path(args.template).resolve()
    result = _preflight(template)
    placeholders = _template_fields(result, template)
    session: Session = {
        "id": uuid.uuid4().hex[:12],
        "template": str(template),
        "format": result.format,
        "placeholders": list(placeholders),
        "answers": {},
    }
    _save_session(_sessions_dir() / f"{session['id']}.json", session)
    print(f"COLLECT-STARTED session={session['id']} format={result.format} "
          f"placeholders={','.join(placeholders)}")
    _print_questions(core.missing_fields(placeholders, {}))


def cmd_collect_answer(args: argparse.Namespace) -> None:
    record, session = _load_session(args.session)
    if args.field not in session["placeholders"]:
        print(f"이 템플릿에 없는 항목입니다: {args.field}", file=sys.stderr)
        sys.exit(2)
    try:
        session["answers"][args.field] = core.validate_answer(args.field, args.value)
    except ValueError as error:
        print(f"ANSWER-REJECTED {error}", file=sys.stderr)
        sys.exit(2)
    _save_session(record, session)
    missing = core.missing_fields(tuple(session["placeholders"]), session["answers"])
    print(f"ANSWERED {args.field}={session['answers'][args.field]}")
    if missing:
        _print_questions(missing)
    else:
        print(f"COLLECT-COMPLETE session={session['id']} — generate 가능")


def cmd_collect_status(args: argparse.Namespace) -> None:
    _, session = _load_session(args.session)
    print(json.dumps(session, ensure_ascii=False, indent=1))


def _resolved_fields(placeholders: tuple[str, ...], answers: dict[str, str]) -> dict[str, str]:
    fields = {k: v for k, v in core.default_fields().items() if k in placeholders}
    fields.update(answers)
    return fields


def _template_fields(result: core.Preflight, template: Path) -> tuple[str, ...]:
    fields = core.extract_placeholders(result, template.read_bytes())
    if fields or result.format != "hwpx":
        return fields
    return procure_hwpx.field_names(procure_hwpx.analyze(template))


def _generate(template: Path, placeholders: tuple[str, ...], answers: dict[str, str],
              out: Path, result: core.Preflight, form_map: Path | None = None) -> None:
    missing = core.missing_fields(placeholders, answers)
    if missing:
        _audit("GENERATION-REFUSED", f"template={template.name} missing={','.join(missing)}")
        print(core.render_refusal(missing))
        sys.exit(5)
    fields = _resolved_fields(placeholders, answers)
    _audit("GENERATION-ATTEMPT", f"template={template.name} format={result.format} out={out.name}")
    try:
        gen.generate(result, template, fields, out, form_map=form_map)
        verified = gen.verify(result, out, fields)
    except gen.DependencyMissing as error:
        out.unlink(missing_ok=True)
        print(error, file=sys.stderr)
        sys.exit(7)
    except gen.VerifyFailed as error:
        print(f"VERIFY-FAIL {error}", file=sys.stderr)
        sys.exit(8)
    print(f"GENERATED file={out} format={result.format}")
    print(f"VERIFIED parser={result.parser} fields={','.join(verified)}")


def cmd_generate(args: argparse.Namespace) -> None:
    if args.session:
        _, session = _load_session(args.session)
        template = Path(session["template"])
        placeholders, answers = tuple(session["placeholders"]), dict(session["answers"])
        form_map = None
    elif args.template_name:
        try:
            registered = registry.load(args.template_name)
        except registry.RegistryError as error:
            print(f"TEMPLATE-NOT-FOUND {error}", file=sys.stderr)
            sys.exit(2)
        template, placeholders = registered.template, registered.fields
        answers, form_map = json.loads(args.fields_json or "{}"), registered.analysis
    else:
        template = Path(args.template)
        result = _preflight(template)
        placeholders = _template_fields(result, template)
        answers = json.loads(args.fields_json or "{}")
        form_map = None
    result = _preflight(template)
    _generate(template, placeholders, answers, Path(args.out), result, form_map)


def cmd_verify(args: argparse.Namespace) -> None:
    generated = Path(args.file)
    result = _preflight(generated)
    fields = json.loads(args.fields_json)
    try:
        verified = gen.verify(result, generated, fields)
    except gen.DependencyMissing as error:
        print(error, file=sys.stderr)
        sys.exit(7)
    except gen.VerifyFailed as error:
        print(f"VERIFY-FAIL {error}", file=sys.stderr)
        sys.exit(8)
    print(f"VERIFIED parser={result.parser} fields={','.join(verified)}")


def cmd_review(args: argparse.Namespace) -> None:
    file = Path(args.file)
    if not file.is_file():
        print(f"파일 없음: {file}", file=sys.stderr)
        sys.exit(2)
    try:
        line = review.send_review(file, args.note or "")
    except review.ReviewError as error:
        print(f"REVIEW-FAILED {error}", file=sys.stderr)
        sys.exit(6)
    _audit("REVIEW-DM", f"file={file.name} {line}")
    print(line)


Option = tuple[str, bool, bool]
Command = Callable[[argparse.Namespace], None]

_SPECS: dict[str, tuple[list[Option], Command]] = {
    "preflight": ([("file", False, False)], cmd_preflight),
    "collect-start": ([("--template", True, False)], cmd_collect_start),
    "collect-answer": (
        [("--session", True, False), ("--field", True, False), ("--value", True, False)],
        cmd_collect_answer,
    ),
    "collect-status": ([("--session", True, False)], cmd_collect_status),
    "generate": (
        [("--session", False, False), ("--template", False, False), ("--template-name", False, False),
         ("--fields-json", False, False), ("--out", True, False)],
        cmd_generate,
    ),
    "register": (
        [("--name", True, False), ("--template", True, False), ("--force", False, True)],
        registry_cli.cmd_register,
    ),
    "templates-list": ([], registry_cli.cmd_templates_list),
    "templates-show": ([("--name", True, False)], registry_cli.cmd_templates_show),
    "verify": (
        [("--file", True, False), ("--fields-json", True, False)],
        cmd_verify,
    ),
    "review": ([("--file", True, False), ("--note", False, False)], cmd_review),
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="procure_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for command, (options, handler) in _SPECS.items():
        p = sub.add_parser(command)
        for flag, required, store_true in options:
            if store_true:
                p.add_argument(flag, action="store_true")
            elif required:
                p.add_argument(flag, required=True)
            else:
                p.add_argument(flag)
        p.set_defaults(fn=handler)

    args = parser.parse_args(argv)
    if args.command == "generate" and not (args.session or args.template or args.template_name):
        parser.error("--session, --template, --template-name 중 하나가 필요합니다")
    if args.command in {"collect-start", "collect-answer", "generate", "register", "review"}:
        message = procurement_governed.refusal(Path(__file__))
        if message:
            print(message, file=sys.stderr)
            raise SystemExit(3)
    args.fn(args)


if __name__ == "__main__":
    main()
