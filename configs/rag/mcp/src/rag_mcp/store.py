import hashlib
from dataclasses import dataclass
from http import HTTPStatus
from typing import ClassVar, Literal
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field

from rag_mcp.settings import RagSettings

_HTTP_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)
_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)


class MemoryPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    chunk_id: str
    content: str
    document_id: str
    metadata: dict[str, str]
    owner_id: Literal["cha"]
    schema_version: Literal[1]
    source: str


class EmbeddingDatum(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    embedding: list[float]


class EmbeddingResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    data: list[EmbeddingDatum] = Field(min_length=1)


class QdrantPoint(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    payload: MemoryPayload
    score: float


class QdrantQueryResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    points: list[QdrantPoint]


class QdrantQueryResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    result: QdrantQueryResult


class MemoryLoadResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    collection: str
    document_id: str
    chunk_id: str


class MemorySearchResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    content: str
    document_id: str
    metadata: dict[str, str]
    score: float
    source: str


class MemoryDeleteResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    deleted: Literal[True]
    document_id: str


@dataclass(frozen=True, slots=True)
class MemoryStore:
    settings: RagSettings

    def client(self, base_url: str) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(retries=3, limits=_HTTP_LIMITS)
        return httpx.AsyncClient(
            base_url=base_url,
            follow_redirects=False,
            limits=_HTTP_LIMITS,
            timeout=_HTTP_TIMEOUT,
            transport=transport,
            trust_env=False,
        )

    async def ensure_collection(self) -> None:
        async with self.client(self.settings.qdrant_url) as client:
            response = await client.get(f"/collections/{self.settings.collection_name}")
            if response.status_code == HTTPStatus.NOT_FOUND:
                response = await client.put(
                    f"/collections/{self.settings.collection_name}",
                    json={
                        "vectors": {
                            "distance": "Cosine",
                            "size": self.settings.embedding_dimension,
                        },
                    },
                )
            _ = response.raise_for_status()

    async def load(
        self,
        content: str,
        source: str,
        metadata: dict[str, str],
    ) -> MemoryLoadResult:
        document_id = str(uuid5(NAMESPACE_URL, f"{source}\n{content}"))
        chunk_id = hashlib.sha256(content.encode()).hexdigest()
        payload = MemoryPayload(
            chunk_id=chunk_id,
            content=content,
            document_id=document_id,
            metadata=metadata,
            owner_id="cha",
            schema_version=1,
            source=source,
        )
        vector = await self.embed(content)
        async with self.client(self.settings.qdrant_url) as client:
            response = await client.put(
                f"/collections/{self.settings.collection_name}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {
                            "id": document_id,
                            "payload": payload.model_dump(),
                            "vector": vector,
                        },
                    ],
                },
            )
            _ = response.raise_for_status()
        return MemoryLoadResult(
            collection=self.settings.collection_name,
            document_id=document_id,
            chunk_id=chunk_id,
        )

    async def search(self, query: str, limit: int) -> list[MemorySearchResult]:
        vector = await self.embed(query)
        async with self.client(self.settings.qdrant_url) as client:
            response = await client.post(
                f"/collections/{self.settings.collection_name}/points/query",
                json={
                    "limit": limit,
                    "query": vector,
                    "with_payload": True,
                },
            )
            _ = response.raise_for_status()
        query_result = QdrantQueryResponse.model_validate(response.json()).result
        return [
            MemorySearchResult(
                content=point.payload.content,
                document_id=point.payload.document_id,
                metadata=point.payload.metadata,
                score=point.score,
                source=point.payload.source,
            )
            for point in query_result.points
        ]

    async def delete(self, document_id: str) -> MemoryDeleteResult:
        async with self.client(self.settings.qdrant_url) as client:
            response = await client.post(
                f"/collections/{self.settings.collection_name}/points/delete",
                params={"wait": "true"},
                json={
                    "filter": {
                        "must": [
                            {
                                "key": "document_id",
                                "match": {"value": document_id},
                            },
                        ],
                    },
                },
            )
            _ = response.raise_for_status()
        return MemoryDeleteResult(deleted=True, document_id=document_id)

    async def health(self) -> None:
        async with self.client(self.settings.embedding_url) as embedding_client:
            embedding_response = await embedding_client.get("/health")
            _ = embedding_response.raise_for_status()
        async with self.client(self.settings.qdrant_url) as qdrant_client:
            qdrant_response = await qdrant_client.get(
                f"/collections/{self.settings.collection_name}",
            )
            _ = qdrant_response.raise_for_status()

    async def embed(self, text: str) -> list[float]:
        async with self.client(self.settings.embedding_url) as client:
            response = await client.post("/v1/embeddings", json={"inputs": [text]})
            _ = response.raise_for_status()
        return EmbeddingResponse.model_validate(response.json()).data[0].embedding
