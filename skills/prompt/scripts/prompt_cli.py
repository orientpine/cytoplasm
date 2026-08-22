from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = _SCRIPTS_DIR.parents[2]
if (REPO_ROOT / "skills" / "prompt" / "scripts").is_dir():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
else:
    # Deployed layout: /srv/autophagy-skills/releases/prompt/<sha256>/scripts/ —
    # no importable `skills` package sits above it, so synthesize `skills` and
    # `skills.prompt` with __path__ at the skill root, exactly like doctype and
    # procurement do. The naive parents[3] insert died in production with
    # ModuleNotFoundError on 2026-08-22 (masked in the sandbox by a
    # namespace-package accident of the ~/.hermes/skills staging path).
    import types

    _SKILL_ROOT = _SCRIPTS_DIR.parent
    if "skills" not in sys.modules:
        _pkg = types.ModuleType("skills")
        _pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["skills"] = _pkg
    if "skills.prompt" not in sys.modules:
        _sk = types.ModuleType("skills.prompt")
        _sk.__path__ = [str(_SKILL_ROOT)]  # type: ignore[attr-defined]
        sys.modules["skills.prompt"] = _sk
        setattr(sys.modules["skills"], "prompt", _sk)

from skills.prompt.scripts import prompt_schema, prompt_store  # noqa: E402


class PromptCliError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GetRequest:
    id: str
    version: int | None
    write_body: Path | None


@dataclass(frozen=True, slots=True)
class AddRequest:
    id: str
    category: str
    purpose: str
    model: str
    tags: tuple[str, ...]
    body_file: Path


def _tags(raw: str) -> tuple[str, ...]:
    tags = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not tags:
        raise PromptCliError("--tags must contain at least one tag")
    return tags


def _store() -> prompt_store.PromptStore:
    return prompt_store.PromptStore(prompt_store.StorePaths.from_environment())


def _route(entry: prompt_store.StoredPrompt) -> str:
    return ",".join(entry.routing_tags) if entry.routing_tags else "none"


def _metadata_line(prefix: str, entry: prompt_store.StoredPrompt) -> str:
    metadata = entry.metadata
    return " ".join(
        (
            prefix,
            f"id={metadata.id}",
            f"version={metadata.version}",
            f"source={entry.source}",
            f"category={metadata.category}",
            f"model={metadata.model}",
            f"tags={','.join(metadata.tags) or '-'}",
            f"sensitivity={metadata.sensitivity}",
            f"routing_tags={_route(entry)}",
        )
    )


def _parse_search(arguments: list[str]) -> str:
    if len(arguments) != 1 or not arguments[0].strip():
        raise PromptCliError("usage: prompt search <query>")
    return arguments[0]


def _parse_get(arguments: list[str]) -> GetRequest:
    if not arguments:
        raise PromptCliError("usage: prompt get <id> [--version N] [--write-body FILE]")
    entry_id = arguments[0]
    version: int | None = None
    write_body: Path | None = None
    remaining = list(arguments[1:])
    while remaining:
        if len(remaining) < 2:
            raise PromptCliError(f"missing value for {remaining[0]}")
        option, value = remaining.pop(0), remaining.pop(0)
        match option:
            case "--version":
                if version is not None or not value.isdecimal() or int(value) < 1:
                    raise PromptCliError("--version must be a positive integer once")
                version = int(value)
            case "--write-body":
                if write_body is not None:
                    raise PromptCliError("--write-body may only be provided once")
                write_body = Path(value)
            case _:
                raise PromptCliError(f"unknown get option: {option}")
    return GetRequest(entry_id, version, write_body)


def _parse_add(arguments: list[str]) -> AddRequest:
    expected: set[str] = {"--id", "--category", "--purpose", "--model", "--tags", "--body-file"}
    values: dict[str, str] = {}
    remaining = list(arguments)
    while remaining:
        if len(remaining) < 2:
            raise PromptCliError(f"missing value for {remaining[0]}")
        option, value = remaining.pop(0), remaining.pop(0)
        if option not in expected or option in values:
            raise PromptCliError(f"invalid add option: {option}")
        values[option] = value
    actual: set[str] = set(values)
    if actual != expected:
        raise PromptCliError("usage: prompt add --id ID --category CATEGORY --purpose TEXT --model MODEL --tags TAGS --body-file FILE")
    category = values["--category"]
    model = values["--model"]
    if category not in ("task", "research-background"):
        raise PromptCliError("unsupported category")
    if model not in ("glm-main", "openai-codex", "any"):
        raise PromptCliError("unsupported model")
    return AddRequest(
        id=values["--id"],
        category=category,
        purpose=values["--purpose"],
        model=model,
        tags=_tags(values["--tags"]),
        body_file=Path(values["--body-file"]),
    )


def _cmd_search(query: str) -> int:
    for entry in _store().search(query):
        print(_metadata_line("HIT", entry))
    return 0


def _cmd_get(request: GetRequest) -> int:
    entry = _store().get(request.id, version=request.version)
    print(_metadata_line("PROMPT", entry))
    if request.write_body is not None:
        with request.write_body.open("x", encoding="utf-8") as handle:
            _ = handle.write(entry.body)
        request.write_body.chmod(0o600)
        return 0
    print(entry.body, end="" if entry.body.endswith("\n") else "\n")
    return 0


def _cmd_add(request: AddRequest) -> int:
    result = _store().add(
        prompt_store.PromptDraft(
            id=request.id,
            category=request.category,
            purpose=request.purpose,
            model=request.model,
            tags=request.tags,
            body=request.body_file.read_text(encoding="utf-8"),
        )
    )
    metadata = result.entry.metadata
    print(
        " ".join(
            (
                "ADDED",
                f"id={metadata.id}",
                f"version={metadata.version}",
                f"sensitivity={metadata.sensitivity}",
                f"body_ref={metadata.body_ref}",
                f"private={str(result.private_path is not None).lower()}",
            )
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("PROMPT-SCHEMA-REJECTED usage: prompt <search|get|add> ...", file=sys.stderr)
        return 2
    command = arguments.pop(0)
    try:
        match command:
            case "search":
                return _cmd_search(_parse_search(arguments))
            case "get":
                return _cmd_get(_parse_get(arguments))
            case "add":
                return _cmd_add(_parse_add(arguments))
            case _:
                raise PromptCliError(f"unknown command: {command}")
    except (PromptCliError, prompt_schema.PromptSchemaError) as error:
        print(f"PROMPT-SCHEMA-REJECTED {error}", file=sys.stderr)
        return 2
    except (prompt_store.PromptNotFoundError, prompt_store.PromptStorageError, OSError) as error:
        print(f"PROMPT-STORE-REFUSED {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
