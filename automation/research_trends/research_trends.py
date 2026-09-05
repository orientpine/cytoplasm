from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import replace
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

# The helper is deployed flat beside this watcher (~/.hermes/scripts/). In the
# checkout it remains owned by the mail skill, so pytest needs that source path too.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    watch_failure_streak = import_module("watch_failure_streak")
except ImportError:  # pragma: no cover - the first import is the deployed layout
    sys.path.insert(0, str(_runtime_root() / "skills" / "mail" / "scripts"))
    try:
        watch_failure_streak = import_module("watch_failure_streak")
    except ImportError:  # pragma: no cover - only reachable on a half-deployed node
        watch_failure_streak = None

from automation import codex_llm  # noqa: E402
from automation.entity_preflight.audit import DEFAULT_OPERATIONAL_ROOT  # noqa: E402
from automation.entity_preflight.gate_metrics import weekly_quality_section  # noqa: E402
from automation.skill_mount import skill_scripts  # noqa: E402
try:
    from automation.research_trends.topics_import import (  # noqa: E402
        topics_import_location as _topics_import_location,
    )
except ImportError:  # pragma: no cover - exercised only in the deployed layout
    # Deployed layout: the watcher lives in ~/.hermes/scripts/ and its own package
    # modules are streamed FLAT into ~/.hermes/research_trends_runtime/ (already on
    # sys.path above). The `automation.*` spelling only resolves once the immutable
    # release has converged to origin/main, and that convergence is not synchronous
    # with this deploy — measured 2026-08-18, the release sat 3 commits behind and the
    # watcher died with ModuleNotFoundError. Same fallback shape as poll_reminders.py.
    from topics_import import topics_import_location as _topics_import_location  # noqa: E402

if TYPE_CHECKING:
    from automation.research_trends.research_trends_core import Paper

KST = timezone(timedelta(hours=9), "KST")
MAX_RESULTS = 2
# 이 리포트는 주간이다 — 스케줄이 아니라 발송 자체가 그것을 지켜야 한다. cron 이
# 주 1회여도 임시 실행·재발화가 같은 주에 두 번째 DM 을 보낸 실측(2026-08-18/19,
# 티켓 t_cda4eea8)이 이 워터마크의 이유다. 성공한 발송만 주를 소진한다(규약 (f)).
WEEK_WATERMARK = "delivered-week"
# STAGE 라벨은 배포된 프롬프트 템플릿(research-trends-v1.md)이 분기하는 계약이지
# 제공자 이름이 아니다. 1단계는 영어 1차 종합, 2단계는 한국어 재작성이며 두 단계
# 모두 공유 Codex OAuth 클라이언트 하나로만 나간다.
_STAGE_SYNTHESIS = "glm"
_STAGE_KOREAN = "codex"
LLM_TIMEOUT_S = 240.0
ARXIV_MAX_ATTEMPTS = int(os.environ.get("RESEARCH_TRENDS_ARXIV_ATTEMPTS", "4"))
ARXIV_MIN_INTERVAL_S = float(os.environ.get("RESEARCH_TRENDS_ARXIV_INTERVAL_S", "5.0"))
ARXIV_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
ARXIV_USER_AGENT = (
    "Autophagy-Agents/Research-Trends "
    "(+https://github.com/orientpine/autophagy-agents)"
)
# 마운트 판정은 governed live 정의 하나(automation/skill_mount.py)에서만 온다.
SCRIPTS_DIR = skill_scripts("topics", env_var="TOPICS_SCRIPTS")
SECRETS = Path.home() / ".env.secrets"
RAG_WATCH = Path.home() / ".hermes" / "scripts" / "rag_ingest_watch.py"
PROMPT_PATH = Path.home() / ".hermes" / "research-trends" / "research-trends-v1.md"
ENTITY_PREFLIGHT_OPERATIONAL_ROOT = Path(DEFAULT_OPERATIONAL_ROOT).expanduser()
WATCH_NAME = "research-trends"
FAILURE_NOTICE_THRESHOLD = 1
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")

# Package name is positional: <root>/<a>/<b>/scripts -> "a.b.scripts".
_TOPICS_PKG, _TOPICS_IMPORT_ROOT = _topics_import_location(SCRIPTS_DIR)
sys.path.insert(0, str(_TOPICS_IMPORT_ROOT))
topics_registry = import_module(f"{_TOPICS_PKG}.topics_registry")
topics_sensitivity = import_module(f"{_TOPICS_PKG}.topics_sensitivity")
topics_knowledge = import_module(f"{_TOPICS_PKG}.topics_knowledge")
topics_evidence = import_module(f"{_TOPICS_PKG}.topics_evidence")


class LlmInvocationError(core.SummaryUnavailable):
    pass


class OwnerDmDeliveryError(RuntimeError):
    pass


def _state_dir() -> Path:
    return Path(os.environ.get("RESEARCH_TRENDS_STATE_DIR", "~/.hermes/research-trends")).expanduser()


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def _announce(*, ok: bool, detail: str = "") -> bool:
    """Speak only when the helper opens or closes this watcher's incident.

    Returns True when the streak recorded the tick — only then may a failing tick
    exit 0. Under ``--deliver discord`` the scheduler posts its own failure banner
    for ANY non-zero exit regardless of stdout (2026-08-24 budget-watch
    measurement), so a recorded expected failure must exit 0 to stay silent, while
    an unrecorded one keeps exit 1 so the banner remains the last line of defence.
    """
    try:
        if watch_failure_streak is None:
            if not ok:
                print(f"{WATCH_NAME} error: {detail}"[:300])
            return False
        notice = watch_failure_streak.record(
            WATCH_NAME, ok=ok, detail=detail, threshold=FAILURE_NOTICE_THRESHOLD
        )
    except Exception:  # noqa: BLE001 - an auxiliary notice cannot change the tick verdict
        return False
    unpersisted = notice is not None and notice == getattr(
        watch_failure_streak, "PERSISTENCE_FAILURE", None
    )
    try:
        if notice is not None:
            print(notice[:300])
    except Exception:  # noqa: BLE001 - a closed sink loses the line, not the record
        pass
    return not unpersisted


def _append_log(name: str, record: dict[str, str]) -> None:
    directory = _state_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / name
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)


def _iso_week(moment: datetime) -> str:
    calendar = moment.isocalendar()
    return f"{calendar.year}-W{calendar.week:02d}"


def _delivered_week() -> str:
    """Return the ISO week whose report already reached the owner, or ''."""
    try:
        return (_state_dir() / WEEK_WATERMARK).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _record_delivered_week(week: str) -> None:
    directory = _state_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / WEEK_WATERMARK
    path.write_text(week + "\n", encoding="utf-8")
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


def _prompt(
    stage: str, topic: str, papers: tuple[Paper, ...], draft: str = "", evidence: str = ""
) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    papers_text = "\n".join(
        f"- {paper.title}\n  abstract: {paper.abstract}\n  link: {paper.url}" for paper in papers
    )
    evidence_block = f"\n{evidence}\nUse only MATERIAL/EVIDENCE, cite [En], do not invent." if evidence else ""
    return (
        f"{template}\n\nSTAGE={stage}\nTOPIC={topic}\nPAPERS:\n{papers_text}"
        f"\nDRAFT:\n{draft}{evidence_block}"
    )


def _codex_model() -> str:
    return os.environ.get(codex_llm.MODEL_ENV, "").strip() or codex_llm.DEFAULT_MODEL


def _run_llm(stage: str, topic: str, prompt: str) -> str:
    """공유 Codex OAuth 클라이언트로 한 번 부른다. 실패는 이번 주 요약 실패다 — 대체 계층 없음."""
    _append_log(
        "llm-calls.jsonl",
        {"stage": stage, "provider": codex_llm.PROVIDER, "model": _codex_model(), "topic": topic},
    )
    fake = os.environ.get(f"RESEARCH_TRENDS_FAKE_{stage.upper()}")
    if fake:
        return fake
    try:
        return codex_llm.complete(prompt, timeout=LLM_TIMEOUT_S)
    except codex_llm.CodexError as error:
        raise LlmInvocationError(f"{stage}: {error.__class__.__name__}") from error


def _synthesis(topic: str, papers: tuple[Paper, ...], evidence: str = "") -> str:
    return _run_llm(
        _STAGE_SYNTHESIS, topic,
        _prompt(_STAGE_SYNTHESIS, topic, papers, evidence=evidence),
    )


def _korean(topic: str, papers: tuple[Paper, ...], draft: str, evidence: str = "") -> str:
    return _run_llm(
        _STAGE_KOREAN, topic,
        _prompt(_STAGE_KOREAN, topic, papers, draft, evidence),
    )


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


def _send_dm(report: str) -> None:
    """ON-2: 목적지(지정 통지 채널/DM)·청킹은 owner_notice 파사드가 소유한다.

    agent-chat 직송(2026-08-24)은 §10-6 확정으로 대체됐다 — 정기 통지 트래픽은
    `#notifications`(`owner_notice_channel_id`) 로 분리한다. 마스킹은 여기 그대로.
    """
    os.environ.setdefault("DISCORD_BOT_TOKEN", _bot_token())
    from automation.owner_notice import notify_owner

    if not notify_owner(report):
        raise OwnerDmDeliveryError("owner notice delivery failed")


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
        [sys.executable, str(RAG_WATCH)], cwd=Path.home(), capture_output=True, text=True,
        check=False,
        # 규약 (b-2): the RAG ingest child needs this process's credentials, and this
        # one call was still relying on inheritance while every sibling stated them.
        env={**os.environ},
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
    now = datetime.now(KST)
    week = _iso_week(now)
    dry_run = os.environ.get("RESEARCH_TRENDS_DRY_RUN") == "1"
    if not dry_run and _delivered_week() == week:
        return 0
    day = now.date().isoformat()
    pack = topics_knowledge.collect(topics)
    evidence_block = topics_evidence.prompt_block(pack)
    sensitive = topics_evidence.is_sensitive(pack)
    summarize = (
        (lambda _topic, _papers: "") if sensitive
        else (lambda topic, papers: _synthesis(topic, papers, evidence_block))
    )
    def write_korean(topic: str, papers: object, draft: str) -> str:
        return _korean(topic, papers, draft, evidence_block)

    outcomes = core.run_topics(topics, _fetch_all, summarize, write_korean)
    validated = tuple(
        replace(outcome, korean_summary=topics_evidence.validate(outcome.korean_summary, pack))
        for outcome in outcomes
    )
    evidence_section = topics_evidence.render_section(pack)
    research_report = core.assemble_report(day, validated, evidence_section)
    report = f"{research_report}\n\n{weekly_quality_section(ENTITY_PREFLIGHT_OPERATIONAL_ROOT)}"
    report_path = _write_report(report, day)
    if isinstance(report_path, Path):
        topics_evidence.write_sidecar(report_path, pack)
    if dry_run:
        print(report)
        return 0
    _send_dm(report)
    _record_delivered_week(week)
    _ingest_report()
    return 0


def main() -> int:
    try:
        result = run()
    except (OSError, RuntimeError) as error:
        detail = _redact(" ".join(str(error).split()))
        recorded = _announce(
            ok=False,
            detail=f"{error.__class__.__name__}: {detail}" if detail else error.__class__.__name__,
        )
        return 0 if recorded else 1
    _announce(ok=True)
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - cron crash path: immediate masked line
        try:
            print(f"research-trends error: {_redact(str(error))}"[:300])
        except BrokenPipeError:
            pass
        sys.exit(1)
