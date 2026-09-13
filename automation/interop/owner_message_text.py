"""Private owner-message text rendering; stdlib only, no I/O or lookups."""
from __future__ import annotations

from typing import Literal, assert_never
from urllib.parse import urlsplit


def resource_url(url: str | None) -> tuple[str | None, str | None]:
    """Return usable producer URL or the frozen v1 fallback reason."""
    if url is None:
        return None, "주소 없음"
    try:
        parsed = urlsplit(url)
        if (parsed.scheme in ("http", "https") and parsed.hostname
                and parsed.username is None and parsed.password is None
                and parsed.port != 0 and not any(
                    char <= " " or char.isspace() or char in '<>"\\'
                    for char in url)):
            return url, None
    except ValueError:
        return None, "주소 파싱 오류"
    return None, "주소 형식 오류"


def result_text(outcome: Literal["executed", "cancelled", "expired"]) -> str:
    """Frozen v1 result wording, separated to keep the public boundary within its LOC limit."""
    match outcome:
        case "executed":
            return "실행 완료"
        case "cancelled":
            return "취소됨"
        case "expired":
            return "만료됨"
        case _:
            assert_never(outcome)
