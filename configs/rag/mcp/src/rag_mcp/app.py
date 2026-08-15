from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Annotated, ClassVar, override

from fastapi import FastAPI
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from rag_mcp.auth import authorization_status
from rag_mcp.settings import RagSettings
from rag_mcp.store import MemoryDeleteResult, MemoryLoadResult, MemorySearchResult, MemoryStore


class AgentApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, api_key: str) -> None:
        super().__init__(app)
        self.api_key: str = api_key

    @override
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        status_code = authorization_status(request.headers.get("Authorization"), self.api_key)
        if status_code == HTTPStatus.OK:
            return await call_next(request)
        return Response(status_code=status_code)


class HealthResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    collection: str
    status: str


settings = RagSettings.from_environment()
memory_store = MemoryStore(settings)
mcp = FastMCP("personal-memory")


@mcp.tool
async def load_memory(
    content: Annotated[str, Field(min_length=1, max_length=100_000)],
    source: Annotated[str, Field(min_length=1, max_length=2_000)],
    metadata: dict[str, str] | None = None,
) -> MemoryLoadResult:
    return await memory_store.load(content, source, metadata or {})


@mcp.tool
async def search_memory(
    query: Annotated[str, Field(min_length=1, max_length=100_000)],
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> list[MemorySearchResult]:
    return await memory_store.search(query, limit)


@mcp.tool
async def delete_memory(
    document_id: Annotated[str, Field(min_length=1, max_length=100)],
) -> MemoryDeleteResult:
    return await memory_store.delete(document_id)


mcp_app = mcp.http_app(
    path="/",
    middleware=[Middleware(AgentApiKeyMiddleware, api_key=settings.api_key)],
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await memory_store.ensure_collection()
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> HealthResponse:
    await memory_store.health()
    return HealthResponse(collection=settings.collection_name, status="ok")


app.mount("/mcp", mcp_app)
