import os
from typing import ClassVar, Final, Literal, Protocol

import numpy as np
from fastapi import FastAPI
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field
from sentence_transformers import SentenceTransformer


class EmbeddingEncoder(Protocol):
    def encode(
        self,
        sentences: list[str],
        *,
        convert_to_numpy: Literal[True],
        normalize_embeddings: bool,
    ) -> NDArray[np.float32]: ...

    def get_sentence_embedding_dimension(self) -> int | None: ...


MODEL_NAME: Final = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")


def load_embedding_model() -> EmbeddingEncoder:
    return SentenceTransformer(MODEL_NAME, device="cpu")


model: Final[EmbeddingEncoder] = load_embedding_model()


class EmbeddingDimensionUnavailableError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The embedding model did not report a vector dimension")


class EmbeddingRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    inputs: list[str] = Field(min_length=1, max_length=64)


class EmbeddingDatum(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    embedding: list[float]
    index: int


class EmbeddingResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    data: list[EmbeddingDatum]
    dimensions: int
    model: str


class HealthResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    dimensions: int
    model: str
    status: str


def get_embedding_dimension() -> int:
    dimension = model.get_sentence_embedding_dimension()
    if isinstance(dimension, int):
        return dimension
    raise EmbeddingDimensionUnavailableError


embedding_dimension: Final = get_embedding_dimension()

app = FastAPI()


@app.get("/health")
def health() -> HealthResponse:
    return HealthResponse(dimensions=embedding_dimension, model=MODEL_NAME, status="ok")


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest) -> EmbeddingResponse:
    vectors = np.asarray(
        model.encode(request.inputs, convert_to_numpy=True, normalize_embeddings=True),
        dtype=np.float32,
    )
    return EmbeddingResponse(
        data=[
            EmbeddingDatum(
                embedding=[float(value) for value in np.ravel(vector)],
                index=index,
            )
            for index, vector in enumerate(vectors)
        ],
        dimensions=embedding_dimension,
        model=MODEL_NAME,
    )
