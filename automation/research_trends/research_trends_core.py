from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as element_tree
from dataclasses import dataclass
from typing import Callable

ARXIV_ENDPOINT = "http://export.arxiv.org/api/query"
SEMSCHOLAR_ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
SEMSCHOLAR_FIELDS = "title,abstract,url,publicationDate,externalIds"
ATOM_NAMESPACE = "{http://www.w3.org/2005/Atom}"
_ARXIV_ID = re.compile(r"arxiv\.org/abs/([^?#\s]+?)(?:v\d+)?$", re.IGNORECASE)
_DOI = re.compile(r"doi\.org/(.+)$", re.IGNORECASE)


class SourceUnavailable(RuntimeError):
    pass


class ArxivResponseError(RuntimeError):
    pass


class ArxivUnavailable(SourceUnavailable):
    pass


class SemanticScholarResponseError(RuntimeError):
    pass


class SemanticScholarUnavailable(SourceUnavailable):
    pass


class SummaryUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Paper:
    title: str
    abstract: str
    url: str
    published: str
    source: str = ""


@dataclass(frozen=True, slots=True)
class TopicOutcome:
    topic: str
    papers: tuple[Paper, ...]
    korean_summary: str
    failure: str | None


FetchPapers = Callable[[str], tuple["Paper", ...]]
SummarizeGlm = Callable[[str, tuple[Paper, ...]], str]
WriteKorean = Callable[[str, tuple[Paper, ...], str], str]


def arxiv_query_url(topic: str, maximum: int = 3) -> str:
    query = urllib.parse.urlencode(
        {
            "search_query": f'all:"{topic}"',
            "start": 0,
            "max_results": maximum,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    return f"{ARXIV_ENDPOINT}?{query}"


def _text(entry: element_tree.Element, tag: str) -> str:
    value = entry.findtext(f"{ATOM_NAMESPACE}{tag}", default="")
    return " ".join(value.split())


def _paper_url(entry: element_tree.Element) -> str:
    for link in entry.findall(f"{ATOM_NAMESPACE}link"):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return str(link.get("href"))
    return _text(entry, "id")


def parse_arxiv_feed(payload: str) -> tuple[Paper, ...]:
    try:
        root = element_tree.fromstring(payload)
    except element_tree.ParseError as error:
        raise ArxivResponseError("arXiv returned invalid Atom XML") from error
    papers: list[Paper] = []
    for entry in root.findall(f"{ATOM_NAMESPACE}entry"):
        title = _text(entry, "title")
        url = _paper_url(entry)
        if not title or not url:
            continue
        papers.append(
            Paper(
                title=title,
                abstract=_text(entry, "summary"),
                url=url,
                published=_text(entry, "published"),
                source="arXiv",
            )
        )
    return tuple(papers)


def semscholar_query_url(topic: str, maximum: int = 3) -> str:
    query = urllib.parse.urlencode(
        {"query": topic, "fields": SEMSCHOLAR_FIELDS, "sort": "publicationDate:desc", "limit": maximum}
    )
    return f"{SEMSCHOLAR_ENDPOINT}?{query}"


def _semscholar_url(item: dict[str, object]) -> str:
    direct = item.get("url")
    if isinstance(direct, str) and direct:
        return direct
    external = item.get("externalIds")
    if isinstance(external, dict):
        arxiv_id = external.get("ArXiv")
        if isinstance(arxiv_id, str) and arxiv_id:
            return f"https://arxiv.org/abs/{arxiv_id}"
        doi = external.get("DOI")
        if isinstance(doi, str) and doi:
            return f"https://doi.org/{doi}"
    return ""


def parse_semscholar(payload: str) -> tuple[Paper, ...]:
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as error:
        raise SemanticScholarResponseError("Semantic Scholar returned invalid JSON") from error
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise SemanticScholarResponseError("Semantic Scholar payload is malformed")
    papers: list[Paper] = []
    for item in document["data"]:
        if not isinstance(item, dict):
            continue
        raw_title = item.get("title")
        title = " ".join(raw_title.split()) if isinstance(raw_title, str) else ""
        url = _semscholar_url(item)
        if not title or not url:
            continue
        abstract = item.get("abstract")
        published = item.get("publicationDate")
        papers.append(
            Paper(
                title=title,
                abstract=abstract if isinstance(abstract, str) else "",
                url=url,
                published=published if isinstance(published, str) else "",
                source="SemSch",
            )
        )
    return tuple(papers)


def _dedup_keys(paper: Paper) -> tuple[object, ...]:
    keys: list[object] = [" ".join(paper.title.split()).casefold()]
    arxiv_match = _ARXIV_ID.search(paper.url)
    if arxiv_match:
        keys.append(("arxiv", arxiv_match.group(1).casefold()))
    doi_match = _DOI.search(paper.url)
    if doi_match:
        keys.append(("doi", doi_match.group(1).casefold()))
    return tuple(keys)


def merge_papers(preferred: tuple[Paper, ...], secondary: tuple[Paper, ...]) -> tuple[Paper, ...]:
    seen: set[object] = set()
    merged: list[Paper] = []
    for paper in (*preferred, *secondary):
        keys = _dedup_keys(paper)
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        merged.append(paper)
    return tuple(merged)


def _failure(topic: str, message: str) -> TopicOutcome:
    return TopicOutcome(topic=topic, papers=(), korean_summary="", failure=message)


def run_topics(
    topics: tuple[str, ...],
    fetch_papers: FetchPapers,
    summarize_glm: SummarizeGlm,
    write_korean: WriteKorean,
) -> tuple[TopicOutcome, ...]:
    outcomes: list[TopicOutcome] = []
    for topic in topics:
        try:
            papers = fetch_papers(topic)
        except SourceUnavailable:
            outcomes.append(_failure(topic, "출처 조회 실패"))
            continue
        if not papers:
            outcomes.append(TopicOutcome(topic, (), "이번 주 검색 결과가 없습니다.", None))
            continue
        try:
            glm_summary = summarize_glm(topic, papers)
            korean_summary = write_korean(topic, papers, glm_summary)
        except SummaryUnavailable:
            outcomes.append(_failure(topic, "LLM 요약 실패"))
            continue
        outcomes.append(TopicOutcome(topic, papers, korean_summary.strip(), None))
    return tuple(outcomes)


def assemble_report(report_day: str, outcomes: tuple[TopicOutcome, ...]) -> str:
    lines = [f"📚 주간 연구 동향 — {report_day} KST"]
    for outcome in outcomes:
        lines.extend(("", f"## {outcome.topic}"))
        if outcome.failure is not None:
            lines.append(f"⚠️ {outcome.failure}: 이 항목은 부분 리포트입니다.")
            continue
        lines.append(outcome.korean_summary)
        if not outcome.papers:
            continue
        lines.append("논문 링크:")
        for paper in outcome.papers:
            published = f" ({paper.published[:10]})" if paper.published else ""
            tag = f" [{paper.source}]" if paper.source else ""
            lines.append(f"- [{paper.title}]({paper.url}){tag}{published}")
    return "\n".join(lines)
