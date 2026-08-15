from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, check=False, env=env, text=True, timeout=120)


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--root":
        return 2
    root = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="w5-trends-bank-") as tmp:
        work = Path(tmp)
        home = work / "home"
        runtime = home / ".hermes/research_trends_runtime"
        topics = home / ".hermes/skills/topics/scripts"
        _ = runtime.mkdir(parents=True)
        _ = topics.mkdir(parents=True)
        for source in ("research_trends.py", "research_trends_core.py"):
            _ = shutil.copy(root / "automation/research_trends" / source, runtime / source)
        for source in ("topics_registry.py", "topics_sensitivity.py"):
            _ = shutil.copy(root / "skills/topics/scripts" / source, topics / source)
        rules = home / ".hermes/sensitivity-rules.yaml"
        _ = rules.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy(root / "configs/sensitivity-rules.yaml", rules)
        state_file = home / ".hermes/state/research-topics.yaml"
        _ = state_file.parent.mkdir(parents=True, exist_ok=True)
        env = {
            "AUTOPHAGY_DEMO_SECRET": "DUMMY-w5-trends",
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "TOPICS_STATE_FILE": str(state_file),
            "TOPICS_RULES_PATH": str(rules),
            "RESEARCH_TRENDS_STATE_DIR": str(work / "state"),
            "RESEARCH_TRENDS_REPORT_DIR": str(work / "reports"),
            "RESEARCH_TRENDS_DRY_RUN": "1",
            "RESEARCH_TRENDS_FAKE_GLM": "unused",
            "RESEARCH_TRENDS_FAKE_CODEX": "unused",
        }
        refused = _run(
            [sys.executable, "-I", str(root / "skills/topics/scripts/topics_cli.py"), "add", "patent marker"], env
        )
        weekly = _run([sys.executable, "-I", str(runtime / "research_trends.py")], env)
        logs = work / "state/logs"
        state_text = state_file.read_text(encoding="utf-8") if state_file.exists() else ""
        obs: dict[str, bool | int | str | None] = {
            "refused": "TOPIC-REFUSED" in (refused.stdout + refused.stderr),
            "registry_contains_topic": "patent marker" in state_text,
            "arxiv_calls": _lines(logs / "arxiv-requests.jsonl"),
            "semscholar_calls": _lines(logs / "semscholar-requests.jsonl"),
            "glm_calls": _lines(logs / "llm-calls.jsonl"),
            "codex_calls": 0,
            "report_written": bool(list((work / "reports").glob("*.md"))),
            "error": None if weekly.returncode == 0 else weekly.returncode,
        }
        print("OBS-JSON: " + json.dumps({"sensitive_topic_refused": obs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
