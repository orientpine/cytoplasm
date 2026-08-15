"""recall_cli — W2-5 recall skill CLI (RAG search with source attribution).

Usage:
    python3 recall_cli.py search "<query>" [--limit N] [--threshold F]
                                           [--strong-threshold F] [--json]

Behavior contract (W2-5):
  * retrieval is LOCAL MCP ONLY (W2-1 memory server, ``personal_cha``) —
    exactly ONE attempt, NO retry. RAG node down => status ``unavailable``
    and the agent falls back to a general answer.
  * empty/below-threshold results => status ``no_memory`` => the agent must
    answer exactly "기억 없음" and must not fabricate.
  * every search appends one masked line to a mode-600 log under
    ``~/.hermes/recall/logs/`` (override: RECALL_LOG_DIR).

Sensitivity (v2, model-aware — 2026-07-22):
  patent-sensitive rows are excluded UNLESS the agent's PRIMARY model route
  is positively non-GLM (read from ~/.hermes/config.yaml, fail-closed on any
  ambiguity). Released rows are prefixed with the ``[[PATENT-SENSITIVE-RECALL]]``
  sentinel; the LiteLLM gateway pre-call guard rejects any glm-main request
  whose payload carries that sentinel, closing the GLM-fallback window
  (configs/litellm-staging/custom_callbacks.py). There is deliberately NO
  caller-facing flag to force inclusion.

Offline test hooks (sandbox scenario / unit tests only — no network):
  RECALL_FAKE_RESULTS=<path.json>  use these rows instead of MCP search
  RECALL_FAKE_ERROR=unreachable    simulate a down RAG node
  RECALL_HERMES_CONFIG=<path.yaml> hermes config used by the model-route guard
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import recall_core  # noqa: E402

_RUNTIME_DEFAULT = "~/.hermes/rag_ingest_runtime"
_CONFIG_DEFAULT = "~/.hermes/rag-ingest/config.json"
_LOG_DIR_DEFAULT = "~/.hermes/recall/logs"
_HERMES_CONFIG_DEFAULT = "~/.hermes/config.yaml"

# Must stay byte-identical to PATENT_SENTINEL in
# configs/litellm-staging/custom_callbacks.py (cross-checked by unit test).
SENSITIVE_MARKER = "[[PATENT-SENSITIVE-RECALL]]"


def _parse_primary_model(text: str) -> tuple[str, str]:
    """Minimal parse of the top-level ``model:`` block — (default, provider)."""
    model = provider = ""
    in_model = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_model = stripped == "model:"
            continue
        if not in_model:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip().strip("'\"")
        if key == "default":
            model = value
        elif key == "provider":
            provider = value
    return model, provider


def _primary_route_is_glm_free() -> bool:
    """True only when the agent's PRIMARY model route is positively non-GLM.

    Fail-closed: unreadable/missing config, empty keys, or any GLM/LiteLLM
    marker in the route => False (exclude, the v1 behavior). The fallback
    chain may still contain GLM — that window is closed at the LiteLLM
    gateway by the ``SENSITIVE_MARKER`` payload guard, not here.
    """
    path = Path(
        os.environ.get("RECALL_HERMES_CONFIG", _HERMES_CONFIG_DEFAULT)
    ).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    model, provider = _parse_primary_model(text)
    if not model or not provider:
        return False
    route = f"{model} {provider}".lower()
    return "glm" not in route and "litellm" not in route


def _log_line(response: dict[str, Any], network_log: list[str]) -> None:
    log_dir = Path(os.environ.get("RECALL_LOG_DIR", _LOG_DIR_DEFAULT)).expanduser()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"recall-{time.strftime('%Y%m%d')}.log"
        top = response["results"][0] if response["results"] else None
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "query": response["query"],
            "status": response["status"],
            "hits": len(response["results"]),
            "top_score": top["score"] if top else None,
            "top_source": top["source"] if top else None,
            "duration_ms": response["search"]["duration_ms"],
            "attempts": response["search"]["attempts"],
            "error": response["search"]["error"],
            "targets": sorted(set(network_log)),
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        log_path.chmod(0o600)
    except OSError:
        pass  # logging must never break recall itself


def _fake_search(query: str, limit: int) -> tuple[list[dict[str, Any]] | None, str | None, list[str]]:
    del query
    fake_error = os.environ.get("RECALL_FAKE_ERROR")
    if fake_error:
        return None, f"MCP unreachable (simulated: {fake_error})", []
    rows_path = Path(os.environ["RECALL_FAKE_RESULTS"])
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    return rows[:limit], None, ["fake://recall-test"]


def _mcp_search(query: str, limit: int) -> tuple[list[dict[str, Any]] | None, str | None, list[str], str | None]:
    """Single-attempt search through the deployed W2-4 rag_ingest client."""
    runtime = Path(os.environ.get("RAG_INGEST_RUNTIME", _RUNTIME_DEFAULT)).expanduser()
    config_path = Path(os.environ.get("RECALL_CONFIG", _CONFIG_DEFAULT)).expanduser()
    sys.path.insert(0, str(runtime))
    try:
        from rag_ingest.config import load_config
        from rag_ingest.mcp_client import McpFatalError, McpMemoryClient, McpUnreachableError
    except ImportError as error:
        return None, f"recall runtime not available: {error.__class__.__name__}", [], None
    try:
        config = load_config(config_path)
    except Exception as error:  # ConfigError or malformed JSON — fail safe
        return None, f"recall config not available: {error.__class__.__name__}", [], None
    client = McpMemoryClient(base_url=config.mcp_base_url, api_key=config.api_key)
    try:
        rows = client.search_memory(query, limit=limit)
    except (McpUnreachableError, McpFatalError) as error:
        # ONE attempt only — no retry loop. Mask detail down to the class name
        # plus a short reason so no auth material can leak into agent output.
        reason = str(error).split(":", 1)[0][:80]
        return None, f"{error.__class__.__name__}: {reason}", client.network_log, config.mcp_base_url
    return rows, None, client.network_log, config.mcp_base_url


def run_search(args: argparse.Namespace) -> int:
    started = time.monotonic()
    base_url: str | None = None
    if os.environ.get("RECALL_FAKE_RESULTS") or os.environ.get("RECALL_FAKE_ERROR"):
        rows, error, network_log = _fake_search(args.query, args.limit)
        base_url = "fake://recall-test"
    else:
        rows, error, network_log, base_url = _mcp_search(args.query, args.limit)
    duration_ms = int((time.monotonic() - started) * 1000)

    excluded_count = 0
    released_count = 0
    if rows is not None:
        sensitive_allowed = _primary_route_is_glm_free()
        visible_rows: list[dict[str, Any]] = []
        for row in rows:
            metadata = row.get("metadata")
            is_sensitive = (
                isinstance(metadata, dict)
                and metadata.get("sensitivity") == "patent-sensitive"
            )
            if is_sensitive and not sensitive_allowed:
                excluded_count += 1
                continue
            if is_sensitive:
                row = dict(row)
                row["content"] = f"{SENSITIVE_MARKER} {row.get('content', '')}"
                released_count += 1
            visible_rows.append(row)
        rows = visible_rows

    response = recall_core.build_response(
        args.query,
        rows,
        error=error,
        base_url=base_url,
        limit=args.limit,
        duration_ms=duration_ms,
        threshold=args.threshold,
        strong_threshold=args.strong_threshold,
    )
    _log_line(response, network_log)
    if excluded_count:
        summary = f"{excluded_count}건은 민감 분류로 제외"
        print(summary, file=sys.stderr if args.json else sys.stdout)
    if released_count:
        notice = (
            f"{released_count}건 patent-sensitive 포함 — 주 모델 non-GLM 확인, "
            f"{SENSITIVE_MARKER} 마커 부착 (GLM 경로에서는 게이트웨이가 차단)"
        )
        print(notice, file=sys.stderr if args.json else sys.stdout)
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(recall_core.render_text(response))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recall_cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search", help="RAG search with source attribution")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--threshold", type=float, default=recall_core.DEFAULT_THRESHOLD)
    search.add_argument(
        "--strong-threshold", type=float, default=recall_core.DEFAULT_STRONG_THRESHOLD
    )
    search.add_argument("--json", action="store_true", help="print raw recall-v1 JSON")
    args = parser.parse_args(argv)
    if not args.query.strip():
        parser.error("query must not be empty")
    return run_search(args)


if __name__ == "__main__":
    sys.exit(main())
