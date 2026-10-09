from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--root":
        return 2
    root = Path(sys.argv[2]).resolve()
    with tempfile.TemporaryDirectory(prefix="w5-trends-bank-") as tmp:
        work = Path(tmp)
        home = work / "home"
        home.mkdir()
        state_file = home / ".hermes/state/research-topics.yaml"
        os.environ.update({
            "HOME": str(home),
            "TOPICS_SCRIPTS": str(root / "skills/topics/scripts"),
            "TOPICS_STATE_FILE": str(state_file),
            "RESEARCH_TRENDS_STATE_DIR": str(work / "state"),
            "RESEARCH_TRENDS_REPORT_DIR": str(work / "reports"),
            "RESEARCH_TRENDS_DRY_RUN": "1",
            "RESEARCH_TRENDS_FAKE_GLM": "English draft",
            "RESEARCH_TRENDS_FAKE_CODEX": "한국어 정리",
        })
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / "automation/research_trends"))
        from automation.knowledge.pack import EvidencePack, KnowledgeQuery
        from automation.research_trends import research_trends
        from skills.topics.scripts import topics_cli

        research_trends.PROMPT_PATH = root / "prompts/research-trends-v1.md"
        research_trends._throttle = lambda: None
        research_trends.topics_registry.DEFAULT_STATE_PATH = state_file
        research_trends.topics_registry.list_topics = lambda: tuple(
            json.loads(line[4:]) for line in state_file.read_text().splitlines()[2:]
        )
        pack = EvidencePack(
            "knowledge-v1", KnowledgeQuery("patent marker", "synthesize", caller="topics"),
            "no_evidence", (), {"rag": "no_memory", "wiki": "none", "twin": "none"},
        )
        research_trends.topics_knowledge.collect = lambda _: pack

        class Response:
            def __init__(self, data: bytes) -> None:
                self.data = data

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def read(self) -> bytes:
                return self.data

        def fetch(request: object, timeout: int = 0) -> Response:
            del timeout
            if "semanticscholar.org" in getattr(request, "full_url", ""):
                return Response(json.dumps({"data": [{
                    "title": "Synthetic paper", "abstract": "Synthetic result",
                    "url": "https://example.invalid/paper", "publicationDate": "2026-10-01",
                }]}).encode())
            return Response(b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')

        research_trends.urlopen = fetch
        added = topics_cli.main(["add", "patent marker"])
        weekly = research_trends.run()
        logs = work / "state/logs"
        obs: dict[str, bool | int | str | None] = {
            "add_exit": added,
            "registry_contains_topic": "patent marker" in state_file.read_text(),
            "arxiv_calls": _lines(logs / "arxiv-requests.jsonl"),
            "semscholar_calls": _lines(logs / "semscholar-requests.jsonl"),
            "model_calls": _lines(logs / "llm-calls.jsonl"),
            "report_written": bool(list((work / "reports").glob("*.md"))),
            "error": None if weekly == 0 else weekly,
        }
        print("OBS-JSON: " + json.dumps({"keyword_topic_processed": obs}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
