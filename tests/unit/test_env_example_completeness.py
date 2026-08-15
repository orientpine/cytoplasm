from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final


REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
EXAMPLE_PATH: Final = REPOSITORY_ROOT / "configs" / "env.example"
SCAN_ROOTS: Final = (REPOSITORY_ROOT / "automation", REPOSITORY_ROOT / "skills")
SKIPPED_PARTS: Final = frozenset({"tests", "vendor", "__pycache__"})
ENTRY_PATTERN: Final = re.compile(r"^([A-Z][A-Z0-9_]*)=")
TOKEN_SHAPED_PATTERN: Final = re.compile(r"(?:[s][k]-|[g][h][p]_|[B][o][t] )")


def _constant_strings(tree: ast.Module) -> dict[str, str]:
    strings: dict[str, str] = {}
    for statement in tree.body:
        match statement:
            case ast.Assign(
                targets=[ast.Name(id=name)], value=ast.Constant(value=str(value))
            ):
                strings[name] = value
            case ast.AnnAssign(
                target=ast.Name(id=name), value=ast.Constant(value=str(value))
            ):
                strings[name] = value
            case _:
                pass
    return strings


def _string_value(node: ast.expr, constants: dict[str, str]) -> str | None:
    match node:
        case ast.Constant(value=str(value)):
            return value
        case ast.Name(id=name):
            return constants.get(name)
        case _:
            return None


def _is_os_environ(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _environment_parameter_positions(tree: ast.Module) -> dict[str, set[int]]:
    readers: dict[str, set[int]] = {}
    for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
        parameters = (*function.args.posonlyargs, *function.args.args)
        for node in ast.walk(function):
            key_node: ast.expr | None = None
            match node:
                case ast.Subscript(value=value, slice=slice_node) if _is_os_environ(value):
                    key_node = slice_node
                case ast.Call(
                    func=ast.Attribute(value=value, attr="get"), args=[candidate, *_]
                ) if _is_os_environ(value):
                    key_node = candidate
                case ast.Call(
                    func=ast.Attribute(value=ast.Name(id="os"), attr="getenv"), args=[candidate, *_]
                ):
                    key_node = candidate
                case _:
                    pass
            if isinstance(key_node, ast.Name):
                for index, parameter in enumerate(parameters):
                    if parameter.arg == key_node.id:
                        readers.setdefault(function.name, set()).add(index)
    return readers


def _read_keys(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants = _constant_strings(tree)
    parameter_positions = _environment_parameter_positions(tree)
    keys: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Subscript(value=value, slice=slice_node) if _is_os_environ(value):
                if key := _string_value(slice_node, constants):
                    keys.add(key)
            case ast.Call(
                func=ast.Attribute(value=value, attr="get"), args=[key_node, *_]
            ) if _is_os_environ(value):
                if key := _string_value(key_node, constants):
                    keys.add(key)
            case ast.Call(
                func=ast.Attribute(value=ast.Name(id="os"), attr="getenv"), args=[key_node, *_]
            ):
                if key := _string_value(key_node, constants):
                    keys.add(key)
            case ast.Call(func=ast.Name(id=name), args=arguments):
                for index in parameter_positions.get(name, set()):
                    if index < len(arguments):
                        if key := _string_value(arguments[index], constants):
                            keys.add(key)
            case _:
                pass
    return keys


def _environment_keys() -> set[str]:
    return {
        key
        for root in SCAN_ROOTS
        for path in root.rglob("*.py")
        if not SKIPPED_PARTS.intersection(path.relative_to(REPOSITORY_ROOT).parts)
        for key in _read_keys(path)
    }


def _documented_keys(text: str) -> set[str]:
    return {
        match.group(1)
        for line in text.splitlines()
        if (match := ENTRY_PATTERN.match(line))
    }


def test_env_example_documents_every_static_environment_read() -> None:
    text = EXAMPLE_PATH.read_text(encoding="utf-8")

    missing = _environment_keys() - _documented_keys(text)

    assert not missing, f"configs/env.example is missing: {', '.join(sorted(missing))}"


def test_env_example_has_no_token_shaped_text() -> None:
    text = EXAMPLE_PATH.read_text(encoding="utf-8")

    assert TOKEN_SHAPED_PATTERN.search(text) is None
