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
        [sys.executable, "-I", str(root / "skills/proposal/scripts/proposal_cli.py"), *args],
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
    with tempfile.TemporaryDirectory(prefix="w5-proposal-bank-") as tmp:
        work = Path(tmp)
        marker = f"w5-proposal-{secrets.token_hex(12)}"
        bin_dir = work / "bin"
        _ = bin_dir.mkdir()
        hermes = bin_dir / "hermes"
        _ = hermes.write_text("#!/bin/sh\nprintf 'offline review\\n'\n", encoding="utf-8")
        hermes.chmod(0o755)
        workspace = work / "agent/proposals"
        status_root = work / "status"
        env = {
            "AUTOPHAGY_DEMO_SECRET": "DUMMY-w5-proposal",
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PROPOSAL_WORKSPACE_ROOT": str(workspace),
            "PROPOSAL_STATUS_ROOT": str(status_root),
            "PROPOSAL_RULES_PATH": str(root / "configs/sensitivity-rules.yaml"),
            "PROPOSAL_LLM_LOG_ROOT": str(work / "logs"),
            "PROPOSAL_RUN_ID": "w5-proposal",
            "PROPOSAL_KANBAN_DISABLED": "1",
            "PROPOSAL_DM_DISABLED": "1",
        }
        _ = _run(root, env, "create", "--slug", "w5-plan", "--title", "W5 plan", "--section", "need:Need", "--section", "approach:Approach", "--section", "impact:Impact")
        drafted = [
            _run(root, env, "draft", "--slug", "w5-plan", "--section", "need", "--text", f"Need {marker}"),
            _run(root, env, "draft", "--slug", "w5-plan", "--section", "approach", "--text", "Approach draft"),
            _run(root, env, "contribute", "--slug", "w5-plan", "--section", "approach", "--source", "human", "--text", "Human contribution"),
            _run(root, env, "draft", "--slug", "w5-plan", "--section", "impact", "--text", "Impact draft"),
        ]
        assembled = _run(root, env, "assemble", "--slug", "w5-plan")
        reviewed = _run(root, env, "review", "--slug", "w5-plan")
        status = _run(root, env, "status", "--slug", "w5-plan")
        assembly = workspace / "w5-plan/assembled.md"
        logs = work / "logs"
        status_text = status.stdout + "\n" + "\n".join(path.read_text(encoding="utf-8") for path in status_root.glob("*.json"))
        obs: dict[str, bool | int | str | None] = {
            "assembled_sections": sum(f"## {heading}" in assembly.read_text(encoding="utf-8") for heading in ("Need", "Approach", "Impact")) if assembly.exists() else 0,
            "contribution_folded": drafted[2].returncode == 0 and "CONTRIBUTION-FOLDED" in drafted[2].stdout,
            "review_calls": len((logs / "llm-calls.jsonl").read_text(encoding="utf-8").splitlines()) if (logs / "llm-calls.jsonl").exists() else 0,
            "review_provider_nonglm": reviewed.returncode == 0 and "provider=openai-codex" in reviewed.stdout,
            "workspace_private": (workspace / "w5-plan").exists() and stat.S_IMODE((workspace / "w5-plan").stat().st_mode) == 0o700,
            "status_metadata_body_absent": marker not in status_text,
            "git_history_body_absent": not _git_has(root, marker),
            "error": None if assembled.returncode == 0 and all(result.returncode == 0 for result in drafted) else "proposal-cli",
        }
        print("OBS-JSON: " + json.dumps({"private_assembly_review": obs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
