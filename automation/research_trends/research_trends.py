from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from importlib import import_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _runtime_root() -> Path:
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "automation" / "entity_preflight").is_dir():
        return checkout
    override = os.environ.get("AUTOPHAGY_RUNTIME_ROOT")
    if override:
        return Path(override)
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


sys.path.insert(0, str(_runtime_root()))
sys.path.insert(0, str(Path.home() / ".hermes" / "research_trends_runtime"))
core = import_module("research_trends_core")

from automation.entity_preflight.audit import DEFAULT_OPERATIONAL_ROOT  # noqa: E402
from automation.entity_preflight.gate_metrics import weekly_quality_section  # noqa: E402

if TYPE_CHECKING:
    from automation.research_trends.research_trends_core import Paper

KST = timezone(timedelta(hours=9), "KST")
MAX_RESULTS = 2
ARXIV_MAX_ATTEMPTS = int(os.environ.get("RESEARCH_TRENDS_ARXIV_ATTEMPTS", "4"))
ARXIV_MIN_INTERVAL_S = float(os.environ.get("RESEARCH_TRENDS_ARXIV_INTERVAL_S", "5.0"))
ARXIV_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
ARXIV_USER_AGENT = (
    "Autophagy-Agents/Research-Trends "
    "(+https://github.com/orientpine/autophagy-agents)"
)
SCRIPTS_DIR = Path.home() / ".hermes" / "skills" / "topics" / "scripts"
INTEROP_CONFIG = Path.home() / ".hermes" / "interop" / "config.json"
SECRETS = Path.home() / ".env.secrets"
RAG_WATCH = Path.home() / ".hermes" / "scripts" / "rag_ingest_watch.py"
PROMPT_PATH = Path.home() / ".hermes" / "research-trends" / "research-trends-v1.md"
ENTITY_PREFLIGHT_OPERATIONAL_ROOT = Path(DEFAULT_OPERATIONAL_ROOT).expanduser()

sys.path.insert(0, str(SCRIPTS_DIR.parents[2]))
topics_registry = import_module("skills.topics.scripts.topics_registry")
topics_sensitivity = import_module("skills.topics.scripts.topics_sensitivity")


class LlmInvocationError(core.SummaryUnavailable):
    pass


class OwnerDmDeliveryError(RuntimeError):
    pass


def _state_dir() -> Path:
    return Path(os.environ.get("RESEARCH_TRENDS_STATE_DIR", "~/.hermes/research-trends")).expanduser()


def _append_log(name: str, record: dict[str, str]) -> None:
    directory = _state_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / name
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)


def _arxiv_url(topic: str) -> str:
    endpoint = os.environ.get("RESEARCH_TRENDS_ARXIV_ENDPOINT", core.ARXIV_ENDPOINT)
    return core.arxiv_query_url(topic, MAX_RESULTS).replace(core.ARXIV_ENDPOINT, endpoint, 1)


_last_arxiv_request_at = 0.0


def _throttle() -> None:
    """Space external requests >=5s apart (arXiv 1/3s + Semantic Scholar >=5s no-key)."""
    global _last_arxiv_request_at
    if _last_arxiv_request_at:
        remaining = ARXIV_MIN_INTERVAL_S - (time.monotonic() - _last_arxiv_request_at)
        if remaining > 0:
            time.sleep(remaining)
    _last_arxiv_request_at = time.monotonic()


def _retry_after_seconds(error: HTTPError) -> float | None:
    header = error.headers.get("Retry-After") if error.headers else None
    return float(header.strip()) if header and header.strip().isdigit() else None


def _backoff(attempt: int, retry_after: float | None) -> None:
    delay = ARXIV_MIN_INTERVAL_S * (2**attempt) + random.uniform(0.0, 1.0)  # noqa: S311
    time.sleep(max(delay, retry_after) if retry_after is not None else delay)


def _fetch_with_retry(url: str, error_cls: type[RuntimeError]) -> str:
    request = Request(url, headers={"User-Agent": ARXIV_USER_AGENT})
    for attempt in range(ARXIV_MAX_ATTEMPTS):
        _throttle()
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            if error.code not in ARXIV_RETRY_STATUS or attempt == ARXIV_MAX_ATTEMPTS - 1:
                raise error_cls(f"HTTP{error.code}") from error
            _backoff(attempt, _retry_after_seconds(error))
        except (URLError, TimeoutError, OSError) as error:
            if attempt == ARXIV_MAX_ATTEMPTS - 1:
                raise error_cls(error.__class__.__name__) from error
            _backoff(attempt, None)
    raise error_cls("retry exhausted")


def _semscholar_url(topic: str) -> str:
    endpoint = os.environ.get("RESEARCH_TRENDS_SEMSCHOLAR_ENDPOINT", core.SEMSCHOLAR_ENDPOINT)
    return core.semscholar_query_url(topic, MAX_RESULTS).replace(core.SEMSCHOLAR_ENDPOINT, endpoint, 1)


def _fetch_arxiv(topic: str) -> str:
    if os.environ.get("RESEARCH_TRENDS_FORCE_ARXIV_FAILURE") == "1":
        raise core.ArxivUnavailable("forced arXiv failure")
    url = _arxiv_url(topic)
    _append_log("arxiv-requests.jsonl", {"topic": topic, "url": url})
    return _fetch_with_retry(url, core.ArxivUnavailable)


def _fetch_semscholar(topic: str) -> str:
    if os.environ.get("RESEARCH_TRENDS_FORCE_SEMSCHOLAR_FAILURE") == "1":
        raise core.SemanticScholarUnavailable("forced Semantic Scholar failure")
    url = _semscholar_url(topic)
    _append_log("semscholar-requests.jsonl", {"topic": topic, "url": url})
    return _fetch_with_retry(url, core.SemanticScholarUnavailable)


def _fetch_all(topic: str) -> tuple[Paper, ...]:
    arxiv_papers: tuple[Paper, ...] | None = None
    try:
        arxiv_papers = core.parse_arxiv_feed(_fetch_arxiv(topic))
    except (core.ArxivUnavailable, core.ArxivResponseError) as error:
        _append_log("source-failures.jsonl", {"topic": topic, "source": "arXiv", "error": error.__class__.__name__})
    semscholar_papers: tuple[Paper, ...] | None = None
    try:
        semscholar_papers = core.parse_semscholar(_fetch_semscholar(topic))[:MAX_RESULTS]
    except (core.SemanticScholarUnavailable, core.SemanticScholarResponseError) as error:
        _append_log("source-failures.jsonl", {"topic": topic, "source": "SemSch", "error": error.__class__.__name__})
    if arxiv_papers is None and semscholar_papers is None:
        raise core.SourceUnavailable("arXiv+SemanticScholar")
    return core.merge_papers(arxiv_papers or (), semscholar_papers or ())


def _prompt(stage: str, topic: str, papers: tuple[Paper, ...], draft: str = "") -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    papers_text = "\n".join(
        f"- {paper.title}\n  abstract: {paper.abstract}\n  link: {paper.url}" for paper in papers
    )
    return f"{template}\n\nSTAGE={stage}\nTOPIC={topic}\nPAPERS:\n{papers_text}\nDRAFT:\n{draft}"


def _run_llm(stage: str, provider: str, model: str, topic: str, prompt: str) -> str:
    _append_log(
        "llm-calls.jsonl",
        {"stage": stage, "provider": provider, "model": model, "topic": topic},
    )
    fake = os.environ.get(f"RESEARCH_TRENDS_FAKE_{stage.upper()}")
    if fake:
        return fake
    command = ["hermes", "-z", prompt, "--provider", provider, "-m", model, "-t", "todo"]
    environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
    if provider == "custom:litellm":
        environment["LITELLM_AGENT_KEY"] = _litellm_key()
    try:
        completed = subprocess.run(
            command,
            cwd=Path.home(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LlmInvocationError(error.__class__.__name__) from error
    if completed.returncode != 0 or not completed.stdout.strip():
        raise LlmInvocationError(f"{stage} rc={completed.returncode}")
    return completed.stdout.strip()


def _glm(topic: str, papers: tuple[Paper, ...]) -> str:
    return _run_llm("glm", "custom:litellm", "glm-main", topic, _prompt("glm", topic, papers))


def _codex(topic: str, papers: tuple[Paper, ...], draft: str) -> str:
    return _run_llm("codex", "openai-codex", "gpt-5.4", topic, _prompt("codex", topic, papers, draft))


def _bot_token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if token:
        return token
    try:
        lines = SECRETS.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.partition("=")[2].strip()
    raise OwnerDmDeliveryError("DISCORD_BOT_TOKEN is unavailable")


def _litellm_key() -> str:
    key = os.environ.get("LITELLM_AGENT_KEY", "")
    if key:
        return key
    try:
        lines = SECRETS.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if line.startswith("LITELLM_AGENT_KEY="):
            return line.partition("=")[2].strip()
    raise LlmInvocationError("LITELLM_AGENT_KEY is unavailable")


def _post(token: str, path: str, payload: dict[str, str]) -> dict[str, str]:
    request = Request(
        f"https://discord.com/api/v10{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise OwnerDmDeliveryError("Discord response is invalid")
    return {str(key): str(value) for key, value in body.items()}


def _chunks(body: str) -> tuple[str, ...]:
    parts: list[str] = []
    remaining = body
    while len(remaining) > 1900:
        boundary = remaining.rfind("\n", 0, 1900)
        split_at = boundary if boundary > 0 else 1900
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return (*parts, remaining)


def _send_dm(report: str) -> None:
    token = _bot_token()
    config = json.loads(INTEROP_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("owner_id"), str):
        raise OwnerDmDeliveryError("owner DM configuration is invalid")
    channel = _post(token, "/users/@me/channels", {"recipient_id": str(config["owner_id"])})
    channel_id = channel.get("id", "")
    if not channel_id:
        raise OwnerDmDeliveryError("owner DM channel is missing")
    for chunk in _chunks(report):
        _post(token, f"/channels/{channel_id}/messages", {"content": chunk})


def _write_report(report: str, day: str) -> Path:
    directory = Path(os.environ.get("RESEARCH_TRENDS_REPORT_DIR", "~/notes/research-trends")).expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / f"research-trends-{day.replace('-', '')}.md"
    path.write_text(report + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _ingest_report() -> None:
    completed = subprocess.run(
        [sys.executable, str(RAG_WATCH)], cwd=Path.home(), capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        _append_log("rag-ingest.jsonl", {"status": "failed"})


def _safe_topics() -> tuple[str, ...]:
    rules = topics_registry.load_rules()
    return tuple(
        topic
        for topic in topics_registry.list_topics()
        if not topics_sensitivity.evaluate(topic, rules).sensitive
    )


def run() -> int:
    topics = _safe_topics()
    if not topics:
        return 0
    day = datetime.now(KST).date().isoformat()
    research_report = core.assemble_report(day, core.run_topics(topics, _fetch_all, _glm, _codex))
    report = f"{research_report}\n\n{weekly_quality_section(ENTITY_PREFLIGHT_OPERATIONAL_ROOT)}"
    _write_report(report, day)
    if os.environ.get("RESEARCH_TRENDS_DRY_RUN") == "1":
        print(report)
        return 0
    _send_dm(report)
    _ingest_report()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (OSError, RuntimeError) as error:
        print(f"research-trends error: {error.__class__.__name__}")
        sys.exit(1)
