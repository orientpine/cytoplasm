"""recall_core — W2-5 recall skill pure logic (stdlib only, no network).

Response schema ``recall-v1`` (the contract every consumer relies on):

    {
      "version": "recall-v1",
      "query": "<original query>",
      "status": "hit" | "no_memory" | "unavailable",
      "message": null | "기억 없음" | "검색 불가 — …",
      "threshold": 0.45,          # hard floor: results below are dropped
      "strong_threshold": 0.60,   # semantic hit even without lexical grounding
      "results": [                # only for status == "hit", ranked
        {
          "rank": 1,
          "score": 0.57,
          "grounded": true,       # >=50% of distinctive query tokens in content
          "source": "wiki:노트.md#c0000",
          "source_type": "wiki",
          "attribution": "위키: 노트.md",
          "title": "노트 제목",
          "excerpt": "본문 앞 200자…",
          "metadata": { …selected source-ref keys… }
        }
      ],
      "search": {
        "base_url": "http://…:8765" | null,
        "limit": 5,
        "attempts": 1,            # ALWAYS 1 — single attempt, no retry
        "duration_ms": 123,
        "error": null | "<masked reason>"
      }
    }

Classification rule (calibrated live on personal_cha, 2026-07-15 —
docs/qa/W2-5/02-threshold-calibration.txt):

  a result is a HIT candidate when score >= threshold (0.45), and a HIT when
  score >= strong_threshold (0.60) OR it is lexically grounded (>= 50% of the
  query's distinctive tokens appear in the retrieved content). This separates
  genuine unique-fact hits (0.57+, grounded) from fabricated look-alike
  queries that embed an unknown token (~0.52, ungrounded).
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "recall-v1"

STATUS_HIT = "hit"
STATUS_NO_MEMORY = "no_memory"
STATUS_UNAVAILABLE = "unavailable"

NO_MEMORY_MESSAGE = "기억 없음"
UNAVAILABLE_MESSAGE = (
    "검색 불가 — RAG 메모리 노드에 연결할 수 없습니다. 재시도하지 않고 일반 지식으로만 답합니다."
)

DEFAULT_THRESHOLD = 0.45
DEFAULT_STRONG_THRESHOLD = 0.60
GROUNDING_RATIO = 0.5
EXCERPT_CHARS = 200

# Korean question/filler words that carry no retrievable content.
_STOPWORDS = {
    "무엇", "뭐야", "뭐지", "뭐였지", "뭔가", "언제", "어디", "누구", "누가",
    "어떻게", "어떤", "얼마", "왜", "알려줘", "알려주세요", "말해줘", "궁금해",
    "있어", "있나", "있지", "인가", "인가요", "대해", "대한", "관련", "관해",
    "그리고", "그런데", "하지만", "우리", "우리의", "당신", "제발", "혹시",
}

_LATIN_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_HANGUL_TOKEN = re.compile(r"[가-힣]{2,}")

# metadata keys forwarded into each result's ``metadata`` (source refs +
# perspective) — everything else (hashes, chunk bookkeeping) is dropped.
_METADATA_KEYS = (
    "source_type", "title", "path", "task_id", "message_id", "session_id",
    "day", "channel", "first_message_id", "last_message_id", "report_agent",
    "agent_id", "role", "project", "interest_tags", "tags",
)


def tokenize(query: str) -> list[str]:
    """Distinctive tokens: latin/digit ids (len>=3) + Hangul words (len>=2)."""
    tokens: list[str] = []
    for match in _LATIN_TOKEN.finditer(query):
        tokens.append(match.group(0).lower())
    for match in _HANGUL_TOKEN.finditer(query):
        word = match.group(0)
        if word not in _STOPWORDS:
            tokens.append(word)
    return list(dict.fromkeys(tokens))


def _token_in(token: str, haystack: str) -> bool:
    if token in haystack:
        return True
    # Korean particles glue to nouns ("코드네임이" vs "코드네임은"): retry with
    # one trailing char stripped so a suffixed query token still matches.
    return len(token) >= 3 and token[:-1] in haystack


def grounding_ratio(tokens: list[str], content: str) -> float:
    if not tokens:
        return 0.0
    haystack = content.lower()
    matched = sum(1 for token in tokens if _token_in(token, haystack))
    return matched / len(tokens)


def attribution(source: str, metadata: dict[str, Any]) -> str:
    """Human-readable source line from the W2-4 source key + metadata."""
    source_type = str(metadata.get("source_type", "")) or source.split(":", 1)[0]
    body = source.split(":", 1)[1] if ":" in source else source
    body = body.split("#c", 1)[0]  # drop chunk suffix
    path = str(metadata.get("path", "")) or body
    if source_type == "wiki":
        return f"위키: {path}"
    if source_type == "meeting":
        return f"회의: {path}"
    if source_type == "note":
        return f"노트: {path}"
    if source_type == "peer-report":
        task = metadata.get("task_id")
        suffix = f" (task {task})" if task else ""
        return f"동료 보고: #agents-log 메시지 {body}{suffix}"
    if source_type == "team-chat":
        channel = metadata.get("channel", "team")
        return f"팀 채팅: #{channel} {body}"
    if source_type == "conversation":
        session = metadata.get("session_id", body.split(":", 1)[0])
        day = metadata.get("day", "")
        suffix = f" ({day})" if day else ""
        return f"대화 기록: 세션 {session}{suffix}"
    return f"출처: {source}"


def _build_result(rank: int, row: dict[str, Any], grounded: bool) -> dict[str, Any]:
    metadata_raw = row.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    content = str(row.get("content", ""))
    source = str(row.get("source", ""))
    return {
        "rank": rank,
        "score": round(float(row.get("score", 0.0)), 6),
        "grounded": grounded,
        "source": source,
        "source_type": str(metadata.get("source_type", "")),
        "attribution": attribution(source, metadata),
        "title": str(metadata.get("title", "")),
        "excerpt": content[:EXCERPT_CHARS],
        "metadata": {k: metadata[k] for k in _METADATA_KEYS if k in metadata},
    }


def classify(
    query: str,
    rows: list[dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
    strong_threshold: float = DEFAULT_STRONG_THRESHOLD,
) -> list[dict[str, Any]]:
    """Filter raw search rows down to attributable hits (may be empty)."""
    tokens = tokenize(query)
    hits: list[dict[str, Any]] = []
    for row in rows:
        score = float(row.get("score", 0.0))
        if score < threshold:
            continue
        grounded = grounding_ratio(tokens, str(row.get("content", ""))) >= GROUNDING_RATIO
        if score >= strong_threshold or grounded:
            hits.append(_build_result(len(hits) + 1, row, grounded))
    return hits


def build_response(
    query: str,
    rows: list[dict[str, Any]] | None,
    *,
    error: str | None = None,
    base_url: str | None = None,
    limit: int = 5,
    duration_ms: int = 0,
    threshold: float = DEFAULT_THRESHOLD,
    strong_threshold: float = DEFAULT_STRONG_THRESHOLD,
) -> dict[str, Any]:
    """Assemble the recall-v1 response for all three statuses."""
    if error is not None or rows is None:
        status, results = STATUS_UNAVAILABLE, []
        message: str | None = UNAVAILABLE_MESSAGE
    else:
        results = classify(query, rows, threshold, strong_threshold)
        if results:
            status, message = STATUS_HIT, None
        else:
            status, message = STATUS_NO_MEMORY, NO_MEMORY_MESSAGE
    return {
        "version": SCHEMA_VERSION,
        "query": query,
        "status": status,
        "message": message,
        "threshold": threshold,
        "strong_threshold": strong_threshold,
        "results": results,
        "search": {
            "base_url": base_url,
            "limit": limit,
            "attempts": 1,
            "duration_ms": duration_ms,
            "error": error,
        },
    }


def render_text(response: dict[str, Any]) -> str:
    """Agent-facing rendering of a recall-v1 response."""
    status = response["status"]
    if status == STATUS_UNAVAILABLE:
        return f"RECALL-UNAVAILABLE {response['message']}"
    if status == STATUS_NO_MEMORY:
        return f"RECALL-NO-MEMORY {response['message']}"
    lines = [f"RECALL-HIT query={response['query']!r} hits={len(response['results'])}"]
    for result in response["results"]:
        lines.append(
            f"[{result['rank']}] score={result['score']:.3f} "
            f"grounded={str(result['grounded']).lower()} — {result['attribution']}"
        )
        if result["title"]:
            lines.append(f"    제목: {result['title']}")
        lines.append(f"    내용: {result['excerpt']}")
    lines.append("출처 표기용: " + " / ".join(r["attribution"] for r in response["results"]))
    return "\n".join(lines)
