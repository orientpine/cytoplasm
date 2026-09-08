from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final, cast


_REPO: Final = Path(__file__).resolve().parents[2]
_CONFIG: Final = _REPO / "pyrightconfig.json"
_SIBLING_IMPORT: Final = re.compile(r"^(?:import|from)\s+(\w+)")
_ROOT: Final = re.compile(r'"root"\s*:\s*"([^"]+)"')


def _has_sibling_import(scripts: Path) -> bool:
    stems = {path.stem for path in scripts.glob("*.py")}
    return any(
        match.group(1) in stems
        for script in scripts.glob("*.py")
        for line in script.read_text(encoding="utf-8").splitlines()
        if (match := _SIBLING_IMPORT.match(line))
    )


def test_pyright_config_lists_existing_sibling_import_roots() -> None:
    # Given: scripts whose runtime imports resolve from their own directory.
    sibling_roots = {
        scripts.relative_to(_REPO).as_posix()
        for scripts in (_REPO / "skills").glob("*/scripts")
        if _has_sibling_import(scripts)
    }

    # When: the editor-only BasedPyright configuration is parsed.
    parsed = subprocess.run(
        (sys.executable, "-m", "json.tool", str(_CONFIG)),
        check=False,
        capture_output=True,
        text=True,
    )
    assert parsed.returncode == 0, parsed.stderr
    config = cast(dict[str, list[str]], json.loads(_CONFIG.read_text(encoding="utf-8")))
    roots = tuple(match.group(1) for match in _ROOT.finditer(_CONFIG.read_text(encoding="utf-8")))

    # Then: 모든 형제 import 루트와 격리 서브서비스가 설정되어야 한다.
    assert sibling_roots <= set(roots)
    assert "configs/stt-engines" in config["exclude"]
    assert all((_REPO / root).is_dir() for root in roots)
    environments = cast(list[dict[str, object]], json.loads(_CONFIG.read_text(encoding="utf-8"))["executionEnvironments"])
    assert tuple(environment["root"] for environment in environments) == roots
    assert all(environment.get("extraPaths") == ["."]
               for environment in environments if environment["root"] != ".")
