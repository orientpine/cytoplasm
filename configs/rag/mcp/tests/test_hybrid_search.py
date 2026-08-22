import asyncio
import importlib
from typing import Protocol, Self, cast

import httpx
import pytest

from rag_mcp.settings import RagSettings
from rag_mcp.store import MemorySearchResult, MemoryStore

_RECOVERED_SCORE = 0.51
_PAYLOAD: dict[str, object] = {
    "chunk_id": "chunk",
    "metadata": {},
    "owner_id": "cha",
    "schema_version": 1,
}


def _point(document_id: str, content: str, score: float, **metadata: str) -> dict[str, object]:
    return {
        "payload": {
            **_PAYLOAD,
            "content": content,
            "document_id": document_id,
            "metadata": metadata,
            "source": f"obsidian:{document_id}.md#c0000",
        },
        "score": score,
    }


def _scroll_point(document_id: str, content: str, **metadata: str) -> dict[str, object]:
    point = _point(document_id, content, 0.0, **metadata)
    return {"id": document_id, "payload": point["payload"]}


class _FakeClient:
    responses: list[dict[str, object]]
    requests: list[tuple[str, object]]

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, *, json: object) -> httpx.Response:
        self.requests.append((url, json))
        return httpx.Response(
            200,
            json=self.responses.pop(0),
            request=httpx.Request("POST", f"http://test{url}"),
        )


def _store(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, object]]) -> MemoryStore:
    client = _FakeClient(responses)
    settings = RagSettings("key", "memory", 2, "http://embedding", "http://qdrant")
    def fake_client(_self: MemoryStore, _url: str) -> _FakeClient:
        return client

    monkeypatch.setattr(MemoryStore, "client", fake_client)
    return MemoryStore(settings)


def _embedding() -> dict[str, object]:
    return {"data": [{"embedding": [0.1, 0.2]}]}


def _query(*points: dict[str, object]) -> dict[str, object]:
    return {"result": {"points": list(points)}}


def _scroll(*points: dict[str, object]) -> dict[str, object]:
    return {"result": {"points": list(points), "next_page_offset": None}}


def test_entity_literal_union_recovers_document_outside_semantic_top_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(
        monkeypatch,
        [
            _embedding(),
            _query(_point("unrelated", "일반 연구 계획", 0.59)),
            _scroll(
                _scroll_point("unrelated", "일반 연구 계획"),
                _scroll_point("collaboration", "김철수 박사 협업 회의"),
            ),
            _query(_point("collaboration", "김철수 박사 협업 회의", _RECOVERED_SCORE)),
        ],
    )

    results = asyncio.run(store.search("최근 김철수 박사와 진행한 협업 회의", 2, ["김철수"]))

    assert [result.document_id for result in results] == ["collaboration", "unrelated"]
    assert results[0].score == _RECOVERED_SCORE


def test_hybrid_rerank_keeps_undated_document_but_places_it_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(
        monkeypatch,
        [
            _embedding(),
            _query(),
            _scroll(
                _scroll_point("undated", "김철수 기록"),
                _scroll_point("dated", "김철수 기록", event_date="2026-08-20"),
            ),
            _query(
                _point("undated", "김철수 기록", 0.5),
                _point("dated", "김철수 기록", 0.5, event_date="2026-08-20"),
            ),
        ],
    )

    results = asyncio.run(store.search("김철수", 2, ["김철수"]))

    assert [result.document_id for result in results] == ["dated", "undated"]


def test_search_without_entity_anchors_preserves_original_qdrant_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [_embedding(), _query(_point("one", "내용", 0.7))]
    store = _store(monkeypatch, responses)

    result = asyncio.run(store.search("질의", 5))

    assert result == [
        MemorySearchResult(
            content="내용",
            document_id="one",
            metadata={},
            score=0.7,
            source="obsidian:one.md#c0000",
        )
    ]


class _SearchMemory(Protocol):
    async def __call__(self, query: str, limit: int) -> list[MemorySearchResult]: ...


class _RecordingStore:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def search(self, *args: object) -> list[MemorySearchResult]:
        self.calls.append(args)
        return []


def test_search_memory_without_new_argument_uses_byte_identical_call_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_MCP_API_KEY", "test")
    monkeypatch.setenv("RAG_COLLECTION_NAME", "memory")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSION", "2")
    monkeypatch.setenv("RAG_EMBEDDING_URL", "http://embedding")
    monkeypatch.setenv("RAG_QDRANT_URL", "http://qdrant")
    app = importlib.import_module("rag_mcp.app")
    recording = _RecordingStore()
    monkeypatch.setattr(app, "memory_store", recording)
    search_memory = cast("_SearchMemory", app.search_memory)

    _ = asyncio.run(search_memory("질의", 5))

    assert recording.calls == [("질의", 5)]
