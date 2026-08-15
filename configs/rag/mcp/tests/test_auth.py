from http import HTTPStatus

from rag_mcp.auth import authorization_status


def test_authorization_status_when_bearer_header_is_missing_returns_401() -> None:
    # Given: an authenticated MCP endpoint and no authorization header.
    # When: the request authorization is evaluated.
    status_code = authorization_status(None, "test-agent-key")

    # Then: the caller is challenged without disclosing the key.
    assert status_code == HTTPStatus.UNAUTHORIZED


def test_authorization_status_when_bearer_token_is_wrong_returns_403() -> None:
    # Given: an authenticated MCP endpoint and a mismatched bearer token.
    # When: the request authorization is evaluated.
    status_code = authorization_status("Bearer wrong-key", "test-agent-key")

    # Then: the authenticated route rejects the token.
    assert status_code == HTTPStatus.FORBIDDEN


def test_authorization_status_when_bearer_token_matches_returns_200() -> None:
    # Given: an authenticated MCP endpoint and its configured bearer token.
    # When: the request authorization is evaluated.
    status_code = authorization_status("Bearer test-agent-key", "test-agent-key")

    # Then: the request can proceed to the MCP transport.
    assert status_code == HTTPStatus.OK
