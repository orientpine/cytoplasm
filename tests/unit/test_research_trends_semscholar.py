from __future__ import annotations

import email.message
import json
import sys
import types
from pathlib import Path
from urllib.error import HTTPError

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME = _ROOT / "automation" / "research_trends"
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_RUNTIME))

from automation.research_trends import research_trends  # noqa: E402
from automation.research_trends import research_trends_core as core  # noqa: E402

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2607.00001v1</id>
    <title> Autophagy regulation </title>
    <summary> A public abstract. </summary>
    <published>2026-07-15T00:00:00Z</published>
    <link rel="alternate" href="https://arxiv.org/abs/2607.00001" />
  </entry>
</feed>"""


def _semscholar_payload(items: list[dict[str, object]]) -> str:
    return json.dumps({"total": len(items), "offset": 0, "data": items})


# --- Paper.source + error hierarchy ---------------------------------------


def test_paper_defaults_to_empty_source() -> None:
    paper = core.Paper("t", "a", "https://x", "2026-07-15")
    assert paper.source == ""


def test_parse_arxiv_feed_tags_papers_with_arxiv_source() -> None:
    papers = core.parse_arxiv_feed(ATOM)
    assert len(papers) == 1
    assert papers[0].source == "arXiv"


def test_source_unavailable_is_common_base() -> None:
    assert issubclass(core.ArxivUnavailable, core.SourceUnavailable)
    assert issubclass(core.SemanticScholarUnavailable, core.SourceUnavailable)


# --- parse_semscholar ------------------------------------------------------


def test_parse_semscholar_valid_uses_url_field_and_tags_source() -> None:
    payload = _semscholar_payload(
        [
            {
                "title": "  Autonomous excavator control  ",
                "abstract": "Public abstract.",
                "url": "https://www.semanticscholar.org/paper/abc",
                "publicationDate": "2026-07-15",
                "externalIds": {"ArXiv": "2607.11111", "DOI": "10.1/x"},
            }
        ]
    )
    papers = core.parse_semscholar(payload)
    assert len(papers) == 1
    assert papers[0].title == "Autonomous excavator control"
    assert papers[0].url == "https://www.semanticscholar.org/paper/abc"
    assert papers[0].published == "2026-07-15"
    assert papers[0].source == "SemSch"


def test_parse_semscholar_url_fallback_to_arxiv() -> None:
    payload = _semscholar_payload(
        [{"title": "SLAM survey", "externalIds": {"ArXiv": "2607.22222"}}]
    )
    papers = core.parse_semscholar(payload)
    assert papers[0].url == "https://arxiv.org/abs/2607.22222"


def test_parse_semscholar_url_fallback_to_doi() -> None:
    payload = _semscholar_payload(
        [{"title": "VLA models", "externalIds": {"DOI": "10.1234/vla"}}]
    )
    papers = core.parse_semscholar(payload)
    assert papers[0].url == "https://doi.org/10.1234/vla"


def test_parse_semscholar_empty_returns_no_papers() -> None:
    assert core.parse_semscholar(_semscholar_payload([])) == ()


def test_parse_semscholar_skips_entries_missing_title_or_url() -> None:
    payload = _semscholar_payload(
        [
            {"title": None, "url": "https://x"},
            {"title": "No url anywhere", "externalIds": {}},
            {"title": "Keep me", "url": "https://keep"},
        ]
    )
    papers = core.parse_semscholar(payload)
    assert len(papers) == 1
    assert papers[0].title == "Keep me"


def test_parse_semscholar_invalid_payload_raises_typed_error() -> None:
    with pytest.raises(core.SemanticScholarResponseError):
        core.parse_semscholar("upstream is down, not json")


def test_parse_semscholar_data_not_list_raises_typed_error() -> None:
    with pytest.raises(core.SemanticScholarResponseError):
        core.parse_semscholar(json.dumps({"data": {"nope": 1}}))


# --- merge_papers ----------------------------------------------------------


def test_merge_papers_dedup_by_title_prefers_arxiv() -> None:
    arxiv = (core.Paper("Deep SLAM", "a1", "https://arxiv.org/abs/2607.1", "d", "arXiv"),)
    semsch = (core.Paper("  deep   slam ", "a2", "https://semsch/x", "d", "SemSch"),)
    merged = core.merge_papers(arxiv, semsch)
    assert len(merged) == 1
    assert merged[0].url == "https://arxiv.org/abs/2607.1"
    assert merged[0].source == "arXiv"


def test_merge_papers_dedup_by_arxiv_id_across_versions() -> None:
    arxiv = (core.Paper("A", "a", "https://arxiv.org/abs/2607.00001", "d", "arXiv"),)
    semsch = (core.Paper("Different title", "a", "https://arxiv.org/abs/2607.00001v2", "d", "SemSch"),)
    merged = core.merge_papers(arxiv, semsch)
    assert len(merged) == 1
    assert merged[0].source == "arXiv"


def test_merge_papers_keeps_distinct_papers_in_order() -> None:
    arxiv = (core.Paper("First", "a", "https://arxiv.org/abs/2607.1", "d", "arXiv"),)
    semsch = (core.Paper("Second", "b", "https://semsch/2", "d", "SemSch"),)
    merged = core.merge_papers(arxiv, semsch)
    assert [p.title for p in merged] == ["First", "Second"]
    assert [p.source for p in merged] == ["arXiv", "SemSch"]


# --- assemble_report source tags ------------------------------------------


def test_assemble_report_renders_source_tag() -> None:
    papers = (
        core.Paper("T1", "a", "https://u1", "2026-07-15", "arXiv"),
        core.Paper("T2", "b", "https://u2", "", "SemSch"),
    )
    outcome = core.TopicOutcome("slam", papers, "요약", None)
    report = core.assemble_report("2026-07-20", (outcome,))
    assert "- [T1](https://u1) [arXiv] (2026-07-15)" in report
    assert "- [T2](https://u2) [SemSch]" in report


def test_assemble_report_untagged_paper_has_no_bracket() -> None:
    papers = (core.Paper("T", "a", "https://u", "", ""),)
    outcome = core.TopicOutcome("slam", papers, "요약", None)
    report = core.assemble_report("2026-07-20", (outcome,))
    assert "- [T](https://u)" in report
    assert "[SemSch]" not in report
    assert "] []" not in report


# --- _fetch_semscholar transport (retry/backoff/throttle) ------------------

SEMSCHOLAR_JSON = json.dumps(
    {
        "total": 1,
        "data": [
            {
                "title": "Learning autonomous excavation",
                "abstract": "abs",
                "url": "https://www.semanticscholar.org/paper/xyz",
                "publicationDate": "2026-07-18",
                "externalIds": {"ArXiv": "2607.55555"},
            }
        ],
    }
)


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _http_error(url: str, code: int) -> HTTPError:
    return HTTPError(url, code, "throttled", email.message.Message(), None)


def _stub_transport_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESEARCH_TRENDS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("RESEARCH_TRENDS_FORCE_SEMSCHOLAR_FAILURE", raising=False)
    monkeypatch.setattr(research_trends, "_last_arxiv_request_at", 0.0, raising=False)
    sleeps: list[float] = []
    fake_time = types.SimpleNamespace(
        sleep=lambda seconds: sleeps.append(seconds), monotonic=lambda: 0.0
    )
    monkeypatch.setattr(research_trends, "time", fake_time, raising=False)
    return sleeps


def _semscholar_log_lines(tmp_path: Path) -> list[str]:
    path = tmp_path / "state" / "logs" / "semscholar-requests.jsonl"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_fetch_semscholar_retries_on_429_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps = _stub_transport_env(tmp_path, monkeypatch)
    attempts = {"n": 0}

    def fake_urlopen(request: object, timeout: int = 0) -> _FakeResponse:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise _http_error(getattr(request, "full_url", "http://x"), 429)
        return _FakeResponse(SEMSCHOLAR_JSON.encode("utf-8"))

    monkeypatch.setattr(research_trends, "urlopen", fake_urlopen)

    body = research_trends._fetch_semscholar("autonomous excavator")

    assert attempts["n"] == 3
    assert "Learning autonomous excavation" in body
    assert len(sleeps) >= 2
    lines = _semscholar_log_lines(tmp_path)
    assert len(lines) == 1
    assert "autonomous excavator" in lines[0]
    assert "semanticscholar.org" in lines[0]


def test_fetch_semscholar_raises_typed_error_after_persistent_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    attempts = {"n": 0}

    def fake_urlopen(request: object, timeout: int = 0) -> _FakeResponse:
        attempts["n"] += 1
        raise _http_error(getattr(request, "full_url", "http://x"), 429)

    monkeypatch.setattr(research_trends, "urlopen", fake_urlopen)

    with pytest.raises(research_trends.core.SemanticScholarUnavailable):
        research_trends._fetch_semscholar("SLAM")
    assert attempts["n"] == 4


def test_fetch_semscholar_does_not_retry_client_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    attempts = {"n": 0}

    def fake_urlopen(request: object, timeout: int = 0) -> _FakeResponse:
        attempts["n"] += 1
        raise _http_error(getattr(request, "full_url", "http://x"), 400)

    monkeypatch.setattr(research_trends, "urlopen", fake_urlopen)

    with pytest.raises(research_trends.core.SemanticScholarUnavailable):
        research_trends._fetch_semscholar("SLAM")
    assert attempts["n"] == 1


def test_fetch_semscholar_force_failure_env_skips_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    monkeypatch.setenv("RESEARCH_TRENDS_FORCE_SEMSCHOLAR_FAILURE", "1")
    called = {"n": 0}

    def fake_urlopen(request: object, timeout: int = 0) -> _FakeResponse:
        called["n"] += 1
        return _FakeResponse(b"")

    monkeypatch.setattr(research_trends, "urlopen", fake_urlopen)

    with pytest.raises(research_trends.core.SemanticScholarUnavailable):
        research_trends._fetch_semscholar("SLAM")
    assert called["n"] == 0


# --- run_topics source-agnostic contract ----------------------------------


def test_run_topics_happy_path_calls_llm_with_merged_papers() -> None:
    calls = {"glm": 0, "codex": 0}

    def glm(topic: str, papers: tuple[core.Paper, ...]) -> str:
        calls["glm"] += 1
        return "draft"

    def codex(topic: str, papers: tuple[core.Paper, ...], draft: str) -> str:
        calls["codex"] += 1
        return "요약"

    papers = (core.Paper("T", "a", "https://u", "2026-07-15", "arXiv"),)

    def fetch(topic: str) -> tuple[core.Paper, ...]:
        return papers

    outcomes = core.run_topics(("slam",), fetch, glm, codex)

    assert calls == {"glm": 1, "codex": 1}
    assert outcomes[0].failure is None
    assert outcomes[0].korean_summary == "요약"


def test_run_topics_empty_papers_skips_llm() -> None:
    called = {"n": 0}

    def glm(topic: str, papers: tuple[core.Paper, ...]) -> str:
        called["n"] += 1
        return "draft"

    def codex(topic: str, papers: tuple[core.Paper, ...], draft: str) -> str:
        called["n"] += 1
        return "x"

    def fetch(topic: str) -> tuple[core.Paper, ...]:
        return ()

    outcomes = core.run_topics(("slam",), fetch, glm, codex)

    assert called["n"] == 0
    assert outcomes[0].korean_summary == "이번 주 검색 결과가 없습니다."
    assert outcomes[0].failure is None


def test_run_topics_source_unavailable_yields_partial_without_llm() -> None:
    called = {"n": 0}

    def glm(topic: str, papers: tuple[core.Paper, ...]) -> str:
        called["n"] += 1
        return "draft"

    def codex(topic: str, papers: tuple[core.Paper, ...], draft: str) -> str:
        called["n"] += 1
        return "x"

    def fetch(topic: str) -> tuple[core.Paper, ...]:
        if topic == "subclass":
            raise core.ArxivUnavailable("boom")
        raise core.SourceUnavailable("both down")

    outcomes = core.run_topics(("subclass", "both"), fetch, glm, codex)

    assert called["n"] == 0
    assert all(o.failure == "출처 조회 실패" for o in outcomes)


# --- _fetch_all merge + partial-source semantics ---------------------------


def _raise_arxiv(topic: str) -> str:
    raise research_trends.core.ArxivUnavailable("arxiv down")


def _raise_semsch(topic: str) -> str:
    raise research_trends.core.SemanticScholarUnavailable("semsch down")


def test_fetch_all_arxiv_down_still_returns_semscholar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    monkeypatch.setattr(research_trends, "_fetch_arxiv", _raise_arxiv)
    monkeypatch.setattr(research_trends, "_fetch_semscholar", lambda topic: SEMSCHOLAR_JSON)

    papers = research_trends._fetch_all("SLAM")

    assert len(papers) >= 1
    assert all(p.source == "SemSch" for p in papers)
    failures = tmp_path / "state" / "logs" / "source-failures.jsonl"
    assert failures.exists()
    assert '"source": "arXiv"' in failures.read_text(encoding="utf-8")


def test_fetch_all_semscholar_down_still_returns_arxiv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    monkeypatch.setattr(research_trends, "_fetch_arxiv", lambda topic: ATOM)
    monkeypatch.setattr(research_trends, "_fetch_semscholar", _raise_semsch)

    papers = research_trends._fetch_all("autophagy")

    assert len(papers) >= 1
    assert all(p.source == "arXiv" for p in papers)


def test_fetch_all_both_sources_down_raises_source_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    monkeypatch.setattr(research_trends, "_fetch_arxiv", _raise_arxiv)
    monkeypatch.setattr(research_trends, "_fetch_semscholar", _raise_semsch)

    with pytest.raises(research_trends.core.SourceUnavailable):
        research_trends._fetch_all("SLAM")
    lines = (tmp_path / "state" / "logs" / "source-failures.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 2


def test_fetch_all_merges_and_dedups_across_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    dup_and_unique = json.dumps(
        {
            "data": [
                {"title": "dup", "externalIds": {"ArXiv": "2607.00001"}},
                {"title": "unique semsch", "url": "https://semsch/only"},
            ]
        }
    )
    monkeypatch.setattr(research_trends, "_fetch_arxiv", lambda topic: ATOM)
    monkeypatch.setattr(research_trends, "_fetch_semscholar", lambda topic: dup_and_unique)

    papers = research_trends._fetch_all("autophagy")

    assert len(papers) == 2
    assert sorted(p.source for p in papers) == ["SemSch", "arXiv"]


# --- surface: in-process DRY_RUN run() over stubbed dual transports --------


def test_dry_run_produces_merged_report_with_both_source_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    monkeypatch.setenv("RESEARCH_TRENDS_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("RESEARCH_TRENDS_DRY_RUN", "1")
    monkeypatch.setenv("RESEARCH_TRENDS_FAKE_GLM", "first-pass stub")
    monkeypatch.setenv("RESEARCH_TRENDS_FAKE_CODEX", "주간 요약 스텁")

    def fake_urlopen(request: object, timeout: int = 0) -> _FakeResponse:
        url = getattr(request, "full_url", "")
        if "semanticscholar.org" in url:
            return _FakeResponse(SEMSCHOLAR_JSON.encode("utf-8"))
        return _FakeResponse(ATOM.encode("utf-8"))

    monkeypatch.setattr(research_trends, "urlopen", fake_urlopen)
    monkeypatch.setattr(research_trends.topics_registry, "load_rules", lambda *a, **k: ())
    monkeypatch.setattr(research_trends.topics_registry, "list_topics", lambda *a, **k: ("SLAM",))
    monkeypatch.setattr(
        research_trends.topics_sensitivity,
        "evaluate",
        lambda topic, rules: types.SimpleNamespace(sensitive=False),
    )

    def _boom(report: str) -> None:
        raise AssertionError("_send_dm must not run under DRY_RUN")

    monkeypatch.setattr(research_trends, "_send_dm", _boom)
    monkeypatch.setattr(research_trends, "PROMPT_PATH", _ROOT / "prompts" / "research-trends-v1.md")

    assert research_trends.run() == 0

    report = capsys.readouterr().out
    assert "[arXiv]" in report
    assert "[SemSch]" in report
    assert "조회 실패" not in report
    assert list((tmp_path / "reports").glob("*.md"))
    logs = tmp_path / "state" / "logs"
    assert len((logs / "arxiv-requests.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len((logs / "semscholar-requests.jsonl").read_text(encoding="utf-8").splitlines()) == 1


# --- #2 bulk endpoint + cap; #3 header rename -----------------------------


def test_semscholar_query_url_uses_bulk_endpoint_sorted_by_date() -> None:
    url = core.semscholar_query_url("SLAM", 2)
    assert "/paper/search/bulk" in url
    assert "publicationDate" in url  # sort=publicationDate:desc (url-encoded)


def test_fetch_all_caps_semscholar_to_max_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = _stub_transport_env(tmp_path, monkeypatch)
    many = json.dumps(
        {"data": [{"title": f"paper {i}", "url": f"https://semsch/{i}"} for i in range(5)]}
    )
    monkeypatch.setattr(research_trends, "_fetch_arxiv", _raise_arxiv)
    monkeypatch.setattr(research_trends, "_fetch_semscholar", lambda topic: many)

    papers = research_trends._fetch_all("SLAM")

    assert len(papers) == research_trends.MAX_RESULTS


def test_assemble_report_header_drops_arxiv_label() -> None:
    report = core.assemble_report("2026-07-20", ())
    assert report.startswith("📚 주간 연구 동향 — 2026-07-20 KST")
    assert "arXiv 연구 동향" not in report
