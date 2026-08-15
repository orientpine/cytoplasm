import os
from dataclasses import dataclass
from urllib.parse import urlparse


class MissingConfigurationError(RuntimeError):
    def __init__(self, variable_name: str) -> None:
        self.variable_name: str = variable_name
        super().__init__(f"Missing required environment variable: {variable_name}")


class InvalidInternalUrlError(RuntimeError):
    def __init__(self, variable_name: str, expected_host: str) -> None:
        self.variable_name: str = variable_name
        self.expected_host: str = expected_host
        super().__init__(f"{variable_name} must use the internal {expected_host} service")


def required_environment_value(variable_name: str) -> str:
    value = os.environ.get(variable_name)
    if value:
        return value
    raise MissingConfigurationError(variable_name)


def internal_service_url(variable_name: str, expected_host: str) -> str:
    url = required_environment_value(variable_name).rstrip("/")
    parsed_url = urlparse(url)
    if parsed_url.scheme == "http" and parsed_url.hostname == expected_host:
        return url
    raise InvalidInternalUrlError(variable_name, expected_host)


@dataclass(frozen=True, slots=True)
class RagSettings:
    api_key: str
    collection_name: str
    embedding_dimension: int
    embedding_url: str
    qdrant_url: str

    @classmethod
    def from_environment(cls) -> "RagSettings":
        return cls(
            api_key=required_environment_value("RAG_MCP_API_KEY"),
            collection_name=required_environment_value("RAG_COLLECTION_NAME"),
            embedding_dimension=int(required_environment_value("RAG_EMBEDDING_DIMENSION")),
            embedding_url=internal_service_url("RAG_EMBEDDING_URL", "embedding"),
            qdrant_url=internal_service_url("RAG_QDRANT_URL", "qdrant"),
        )
