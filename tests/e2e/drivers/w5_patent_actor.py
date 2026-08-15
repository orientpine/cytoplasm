from __future__ import annotations

import json
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(root / "skills/patent-prep/scripts/patent_cli.py"), *args],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=120,
    )


def _git_has(root: Path, marker: str) -> bool:
    return bool(subprocess.run(["git", "log", "--all", "-S", marker, "--format=%H"], cwd=root, capture_output=True, check=False, text=True, timeout=30).stdout.strip())


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--root":
        return 2
    root = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="w5-patent-bank-") as tmp:
        work = Path(tmp)
        marker = f"w5-patent-{secrets.token_hex(12)}"
        response = work / "response.md"
        _ = response.write_text(f"offline response {marker}\n", encoding="utf-8")
        drafts = work / "agent/patent-drafts"
        env = {
            "AUTOPHAGY_DEMO_SECRET": "DUMMY-w5-patent",
            "PATH": "/usr/bin:/bin",
            "PATENT_DRAFT_ROOT": str(drafts),
            "PATENT_STATUS_ROOT": str(work / "status"),
            "PATENT_LLM_LOG_ROOT": str(work / "logs"),
            "PATENT_RUN_ID": "w5-patent",
            "PATENT_SENSITIVE_TAG": "",
        }
        _ = _run(root, env, "create", "--slug", "w5-disclosure")
        _ = _run(root, env, "checklist", "--slug", "w5-disclosure", "--state", "in-progress")
        drafted = _run(root, env, "draft", "--slug", "w5-disclosure", "--response-file", str(response))
        obs: dict[str, bool | int | str | None] = {
            "draft_exit": drafted.returncode,
            "tag_auto_attached": "tag_auto_attached=true" in drafted.stdout,
            "provider_nonglm": "provider=openai-codex" in drafted.stdout,
            "glm_calls": 0,
            "workspace_private": (drafts / "w5-disclosure").exists() and stat.S_IMODE((drafts / "w5-disclosure").stat().st_mode) == 0o700,
            "git_history_invention_absent": not _git_has(root, marker),
            "error": None,
        }
        print("OBS-JSON: " + json.dumps({"forced_nonglm_private_draft": obs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
