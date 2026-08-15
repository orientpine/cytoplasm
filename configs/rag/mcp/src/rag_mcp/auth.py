import hmac
from http import HTTPStatus


def authorization_status(authorization_header: str | None, expected_token: str) -> int:
    if authorization_header is None:
        return HTTPStatus.UNAUTHORIZED

    expected_header = f"Bearer {expected_token}"
    if hmac.compare_digest(authorization_header, expected_header):
        return HTTPStatus.OK
    return HTTPStatus.FORBIDDEN
