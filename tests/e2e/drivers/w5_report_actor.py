from __future__ import annotations

import json
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(script: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    code = (
        "import runpy,sys; "
        f"sys.path.insert(0, {str(script.parent)!r}); "
        f"sys.argv={[str(script), *args]!r}; "
        "runpy.run_path(sys.argv[0], run_name='__main__')"
    )
    return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, check=False, env=env, text=True, timeout=120)


def _git_has(root: Path, marker: str) -> bool:
    return bool(subprocess.run(["git", "log", "--all", "-S", marker, "--format=%H"], cwd=root, capture_output=True, check=False, text=True, timeout=30).stdout.strip())


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--root":
        return 2
    root = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="w5-report-bank-") as tmp:
        work = Path(tmp)
        notes, outputs = work / "notes", work / "outputs"
        _ = notes.mkdir()
        marker = f"w5-report-{secrets.token_hex(12)}"
        _ = (notes / "classified.md").write_text(f"# classified\n\npatent {marker}\n", encoding="utf-8")
        response = work / "response.md"
        _ = response.write_text("offline response\n", encoding="utf-8")
        completed = _run(
            root / "skills/report/scripts/report_cli.py",
            ["report", "--notes-root", str(notes), "--outputs-root", str(outputs), "--response-file", str(response), "--query", "patent"],
            {"AUTOPHAGY_DEMO_SECRET": "DUMMY-w5-report", "PATH": "/usr/bin:/bin", "REPORT_RULES_PATH": str(root / "configs/sensitivity-rules.yaml")},
        )
        produced = list(outputs.glob("report-*.md"))
        obs: dict[str, bool | int | str | None] = {
            "report_exit": completed.returncode,
            "sensitive_route_nonglm": "provider=openai-codex" in completed.stdout and "sensitive=true" in completed.stdout,
            "glm_calls": 0,
            "output_private": outputs.exists() and stat.S_IMODE(outputs.stat().st_mode) == 0o700 and len(produced) == 1,
            "git_history_note_absent": not _git_has(root, marker),
            "error": None,
        }
        print("OBS-JSON: " + json.dumps({"classified_note_nonglm": obs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
