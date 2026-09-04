"""Conformance guard: Drive output mutations belong behind the shared facade."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_ALLOWLIST: Final[dict[str, str]] = {
    "automation/drive_client.py": "single low-level gws argv client - the facade seam",
    "skills/patent-prep/scripts/patent_export.py": (
        "dedicated approval gate with allowlist-of-one folder"
    ),
}
_FOLDER_MIME: Final = "application/vnd.google-apps.folder"
_MUTATION_SUBCOMMANDS: Final = frozenset({"create", "update"})


def _relative(path: Path) -> str:
    return path.relative_to(_REPO).as_posix()


def _python_sources(*roots: str) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for root in roots
            for path in (_REPO / root).rglob("*.py")
            if "__pycache__" not in path.parts
        )
    )


def _literal_strings(node: ast.AST) -> frozenset[str]:
    return frozenset(
        value.value
        for value in ast.walk(node)
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    )


def _mutation_argvs(path: Path) -> tuple[ast.List | ast.Tuple | ast.Call, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[ast.List | ast.Tuple | ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            values = _literal_strings(node)
        elif isinstance(node, ast.Call):
            values = frozenset(
                value for argument in node.args for value in _literal_strings(argument)
            )
        else:
            continue
        is_upload = "drive" in values and "+upload" in values
        is_files_mutation = (
            "drive" in values
            and "files" in values
            and bool(values & _MUTATION_SUBCOMMANDS)
        )
        if is_upload or is_files_mutation:
            found.append(node)
    return tuple(found)


def _unallowlisted(paths: tuple[Path, ...]) -> list[str]:
    return [path for path in map(_relative, paths) if path not in _ALLOWLIST]


def test_no_vendored_drive_publish_scripts_remain() -> None:
    vendored = sorted(
        _relative(path)
        for path in (_REPO / "skills").glob("**/scripts/drive_publish.py")
    )
    assert not vendored, "vendored Drive publishers must be deleted: " + ", ".join(vendored)


def test_upload_and_folder_creation_literals_stay_at_the_facade_seam() -> None:
    offenders: list[Path] = []
    for path in _python_sources("skills", "automation"):
        source = path.read_text(encoding="utf-8")
        mutations = _mutation_argvs(path)
        has_upload_literal = '"+upload"' in source or "'+upload'" in source
        has_folder_creation = _FOLDER_MIME in source and any(
            "files" in _literal_strings(argv) and "create" in _literal_strings(argv)
            for argv in mutations
        )
        if has_upload_literal or has_folder_creation:
            offenders.append(path)

    unallowlisted = _unallowlisted(tuple(offenders))
    assert not unallowlisted, (
        "Drive uploads and folder-creation argv must use automation.drive_outputs; "
        + "unapproved direct implementations: "
        + ", ".join(unallowlisted)
    )
    assert all(reason.strip() for reason in _ALLOWLIST.values())


def test_skill_scripts_do_not_construct_direct_gws_drive_mutation_argvs() -> None:
    offenders = tuple(
        path
        for path in sorted((_REPO / "skills").glob("*/scripts/*.py"))
        if _mutation_argvs(path)
    )
    unallowlisted = _unallowlisted(offenders)
    assert not unallowlisted, (
        "skill scripts must not construct direct gws Drive mutation argv; "
        + "use automation.drive_outputs instead: "
        + ", ".join(unallowlisted)
    )
    assert all(reason.strip() for reason in _ALLOWLIST.values())
