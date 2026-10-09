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
        [sys.executable, "-I", str(root / "skills/prompt/scripts/prompt_cli.py"), *args],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=120,
    )


def _git_has(root: Path, marker: str) -> bool:
    return bool(
        subprocess.run(
            ["git", "log", "--all", "-S", marker, "--format=%H"],
            capture_output=True,
            check=False,
            cwd=root,
            text=True,
            timeout=30,
        ).stdout.strip()
    )


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--root":
        return 2
    root = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="w5-prompt-bank-") as tmp:
        work = Path(tmp)
        private = work / "private"
        overlay = work / "overlay"
        marker = f"w5-prompt-{secrets.token_hex(12)}"
        body = work / "body.md"
        _ = body.write_text(f"patent {marker}\n", encoding="utf-8")
        env = {
            "AUTOPHAGY_DEMO_SECRET": "DUMMY-w5-prompt",
            "PATH": "/usr/bin:/bin",
            "PROMPT_REPO_ROOT": str(root),
            "PROMPT_OVERLAY_ROOT": str(overlay),
            "PROMPT_PRIVATE_ROOT": str(private),
            "HOME": str(work),
            "AUTOPHAGY_SKILL_LIVE_ROOT": str(root / "skills"),
            "PROMPT_MEETING_SCRIPTS": str(root / "skills/meeting/scripts"),
        }
        added = _run(
            root,
            env,
            "add",
            "--id",
            "w5-classified",
            "--category",
            "task",
            "--purpose",
            "security fixture",
            "--model",
            "any",
            "--tags",
            "w5",
            "--body-file",
            str(body),
        )
        overlay_files = list(overlay.rglob("*.md"))
        private_files = list(private.glob("*.md"))
        overlay_text = overlay_files[0].read_text(encoding="utf-8") if overlay_files else ""
        obs: dict[str, bool | int | str | None] = {
            "add_exit": added.returncode,
            "inline": "body_ref=inline" in added.stdout,
            "private_path_created": len(private_files) == 1,
            "overlay_dir_mode": f"{stat.S_IMODE(overlay_files[0].parent.stat().st_mode):04o}" if overlay_files else "",
            "overlay_body_empty": marker not in overlay_text,
            "overlay_inline": "body_ref: inline" in overlay_text,
            "repo_body_absent": marker not in subprocess.run(
                ["git", "grep", "-n", marker], cwd=root, capture_output=True, check=False, text=True, timeout=30
            ).stdout,
            "git_history_body_absent": not _git_has(root, marker),
            "error": None,
        }
        print("OBS-JSON: " + json.dumps({"keyword_inline_storage": obs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
