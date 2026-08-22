from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_roster_path_has_one_group_roster_owned_definition() -> None:
    # Given: every tracked Python module in the automation tree.
    definitions: list[Path] = []
    for path in (REPO_ROOT / "automation").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(isinstance(node, ast.FunctionDef) and node.name == "roster_path" for node in ast.walk(tree)):
            definitions.append(path.relative_to(REPO_ROOT))

    # When/Then: group_roster is the sole owner of runtime roster-path resolution.
    assert definitions == [Path("automation/group_roster/parser.py")]
