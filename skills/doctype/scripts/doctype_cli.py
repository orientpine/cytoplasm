"""CLI contract for registering, reusing, reviewing, and refining private doc types."""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Final


_SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = _SCRIPTS_DIR.parents[2]
if (REPO_ROOT / "skills" / "doctype" / "scripts").is_dir():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
else:
    # Deployed layout: /srv/autophagy-skills/releases/doctype/<sha256>/scripts/ —
    # no importable `skills` package sits above it, so synthesize `skills` and
    # `skills.doctype` with __path__ at the skill root. Python then resolves every
    # subpackage (scripts, vendor, ...) itself, keeping the absolute
    # `from skills.doctype...` imports (used by submodules too) working from the
    # immutable store.
    import types

    _SKILL_ROOT = _SCRIPTS_DIR.parent
    if "skills" not in sys.modules:
        _pkg = types.ModuleType("skills")
        _pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["skills"] = _pkg
    if "skills.doctype" not in sys.modules:
        _sk = types.ModuleType("skills.doctype")
        _sk.__path__ = [str(_SKILL_ROOT)]  # type: ignore[attr-defined]
        sys.modules["skills.doctype"] = _sk
        setattr(sys.modules["skills"], "doctype", _sk)

from skills.doctype.scripts import doctype_extract, doctype_generate, doctype_llm, doctype_review, doctype_routing, doctype_schema, doctype_store  # noqa: E402


SAVE_CLARIFY_EXIT_CODE: Final = 5
_SAVE_CANDIDATES: Final = "obsidian,drive,local"


class CliError(ValueError):
    """The command-line request does not satisfy the safe document-type contract."""


def _store() -> doctype_store.DocTypeStore:
    return doctype_store.DocTypeStore(doctype_store.StorePaths.from_environment())


def _name(value: str) -> str:
    name = value.strip()
    if not name:
        raise CliError("--name must not be empty")
    return name


def _argument(args: argparse.Namespace, name: str) -> str:
    value: object = vars(args).get(name)
    if not isinstance(value, str):
        raise CliError(f"{name} must be a string")
    return value


def _optional_argument(args: argparse.Namespace, name: str) -> str | None:
    value: object = vars(args).get(name)
    if value is None or isinstance(value, str):
        return value
    raise CliError(f"{name} must be a string when present")


def _flag(args: argparse.Namespace, name: str) -> bool:
    value: object = vars(args).get(name)
    if not isinstance(value, bool):
        raise CliError(f"{name} must be a boolean")
    return value


def _new_id(name: str) -> str:
    ascii_name = "-".join(part for part in name.lower().split() if part.isascii() and part.replace("-", "").isalnum())
    if ascii_name:
        try:
            return doctype_schema.validate_identifier(ascii_name[:64])
        except doctype_schema.DocTypeSchemaError:
            pass
    return "doctype-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


def _existing_or_id(store: doctype_store.DocTypeStore, name: str) -> tuple[doctype_store.StoredDocType | None, str]:
    try:
        entry = store.get_by_name(name)
    except doctype_store.DocTypeNotFoundError:
        return None, _new_id(name)
    return entry, entry.metadata.id


def _registered_line(prefix: str, entry: doctype_store.StoredDocType) -> str:
    metadata = entry.metadata
    return " ".join(
        (
            prefix,
            f"id={metadata.id}",
            f"version={metadata.version}",
            f"name={metadata.doc_type_name}",
            f"mode={metadata.mode}",
            f"sensitivity={metadata.sensitivity}",
            f"examples={len(metadata.examples)}",
        )
    )


def _sensitivity_tags(primary: str, secondary: str = "none") -> frozenset[str]:
    return frozenset(value for value in (primary, secondary) if value != "none")


def _save_route(args: argparse.Namespace, sensitivity: frozenset[str]) -> doctype_routing.SaveRoute:
    request = _argument(args, "save_request")
    # An absent --save-request is not a request to store the artifact externally:
    # registry operations must never be widened into a Drive upload (fail-closed).
    return doctype_routing.classify_save_request(
        request, has_file_artifact=bool(request.strip()), sensitivity=sensitivity
    )


def _clarify_save(route: doctype_routing.SaveRoute) -> int | None:
    if not route.clarify:
        return None
    print(f"SAVE-CLARIFY reason={route.reason} candidates={_SAVE_CANDIDATES}")
    return SAVE_CLARIFY_EXIT_CODE


def _save_artifact(artifact: Path, route: doctype_routing.SaveRoute) -> None:
    if not {"obsidian", "drive"}.intersection(route.destinations):
        return
    try:
        from skills.doctype.scripts import doctype_save
    except ImportError as error:
        raise CliError("document save adapter is unavailable") from error
    try:
        doctype_save.save_from_environment(artifact, route)
    except doctype_save.DocumentSaveError as error:
        raise CliError(str(error)) from error


def cmd_register(args: argparse.Namespace) -> int:
    store = _store()
    name = _name(_argument(args, "name"))
    example = Path(_argument(args, "example"))
    source = doctype_extract.read_document(example)
    sensitivity = doctype_store.document_sensitivity(source.text, store.paths.rules_file)
    route = _save_route(args, _sensitivity_tags(sensitivity))
    if (exit_code := _clarify_save(route)) is not None:
        return exit_code
    _, entry_id = _existing_or_id(store, name)
    extracted = doctype_extract.extract(
        example, store.paths.rules_file, mode_override=_optional_argument(args, "mode")
    )
    result = store.add(extracted.draft(entry_id, name))
    _save_artifact(result.private_path, route)
    print(_registered_line("REGISTERED", result.entry))
    print(f"EXAMPLE-PRIVATE ref={result.entry.metadata.examples[-1].ref}")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    latest: dict[str, doctype_store.StoredDocType] = {}
    for entry in _store().list():
        prior = latest.get(entry.metadata.id)
        if prior is None or prior.metadata.version < entry.metadata.version:
            latest[entry.metadata.id] = entry
    for entry in sorted(latest.values(), key=lambda item: item.metadata.doc_type_name):
        print(_registered_line("DOCTYPE", entry))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    entry = _store().get_by_name(_name(_argument(args, "name")))
    print(doctype_schema.compose_entry(entry.metadata), end="")
    return 0


def _review(result: doctype_generate.DraftResult, entry: doctype_store.StoredDocType) -> str:
    target = doctype_review.resolve_target()
    message = (
        "📄 서류 종류 초안 검토 요청\n"
        f"type={entry.metadata.id} version={entry.metadata.version}\n"
        f"file={result.path.name} sha256={result.sha256}\n"
        "검토·승인은 cha가 직접 수행합니다. 이 스킬은 제출하지 않습니다."
    )
    doctype_review.send_review(target, message)
    return "skipped" if not target else "sent"


def cmd_draft(args: argparse.Namespace) -> int:
    store = _store()
    entry = store.get_by_name(_name(_argument(args, "name")))
    inputs = doctype_generate.load_inputs(Path(_argument(args, "inputs_json")))
    input_sensitivity = doctype_store.document_sensitivity("\n".join(inputs.values()), store.paths.rules_file)
    route = _save_route(args, _sensitivity_tags(entry.metadata.sensitivity, input_sensitivity))
    if (exit_code := _clarify_save(route)) is not None:
        return exit_code
    result = doctype_generate.generate(
        store, entry, inputs, Path(_argument(args, "out"))
    )
    _save_artifact(result.path, route)
    review = _review(result, entry) if _flag(args, "review") else "not-requested"
    print(
        " ".join(
            (
                "DRAFTED",
                f"version={result.version}",
                f"file={result.path.name}",
                f"sha256={result.sha256}",
                f"narrative_sections={','.join(result.narrative_sections) or '-'}",
                f"review={review}",
                f"drive={'verified' if 'drive' in route.destinations else ''}",
            )
        )
    )
    return 0


def cmd_refine(args: argparse.Namespace) -> int:
    store = _store()
    entry = store.get_by_name(_name(_argument(args, "name")))
    approved = Path(_argument(args, "approved"))
    source = doctype_extract.read_document(approved)
    approved_sensitivity = doctype_store.document_sensitivity(source.text, store.paths.rules_file)
    route = _save_route(args, _sensitivity_tags(entry.metadata.sensitivity, approved_sensitivity))
    if (exit_code := _clarify_save(route)) is not None:
        return exit_code
    extracted = doctype_extract.extract(
        approved,
        store.paths.rules_file,
        mode_override=entry.metadata.mode,
        prior=entry.metadata,
        note=_optional_argument(args, "note") or "",
    )
    result = store.add_version(extracted.draft(entry.metadata.id, entry.metadata.doc_type_name))
    _save_artifact(result.private_path, route)
    print(_registered_line("REFINED", result.entry))
    print(f"EXAMPLE-PRIVATE ref={result.entry.metadata.examples[-1].ref}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doctype")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register-from-example")
    _ = register.add_argument("--name", required=True)
    _ = register.add_argument("--example", required=True)
    _ = register.add_argument("--mode", choices=("slot-fill", "narrative", "hybrid"))
    _ = register.add_argument("--save-request", default="")
    _ = commands.add_parser("list")
    show = commands.add_parser("show")
    _ = show.add_argument("--name", required=True)
    draft = commands.add_parser("draft")
    _ = draft.add_argument("--name", required=True)
    _ = draft.add_argument("--inputs-json", required=True)
    _ = draft.add_argument("--out", required=True)
    _ = draft.add_argument("--review", action="store_true")
    _ = draft.add_argument("--save-request", default="")
    refine = commands.add_parser("refine")
    _ = refine.add_argument("--name", required=True)
    _ = refine.add_argument("--approved", required=True)
    _ = refine.add_argument("--note")
    _ = refine.add_argument("--save-request", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        match _argument(args, "command"):
            case "register-from-example":
                return cmd_register(args)
            case "list":
                return cmd_list(args)
            case "show":
                return cmd_show(args)
            case "draft":
                return cmd_draft(args)
            case "refine":
                return cmd_refine(args)
            case unexpected:
                raise CliError(f"unknown command: {unexpected}")
    except (
        CliError,
        doctype_extract.ExtractionError,
        doctype_generate.GenerationError,
        doctype_llm.LlmCallError,
        doctype_review.DeliveryError,
        doctype_schema.DocTypeSchemaError,
        doctype_store.DocTypeNotFoundError,
        doctype_store.DocTypeStorageError,
        OSError,
    ) as error:
        print(f"DOCTYPE-REFUSED {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
