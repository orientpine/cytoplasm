from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Protocol
from urllib.error import HTTPError

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET_FIELD = re.compile(
    r"(?i)\b(authorization|api[-_]?key|token|secret|password|cookie)\b\s*[:=]\s*([^\s,;]+)"
)
_SENSITIVE_JSON_FIELD = re.compile(
    r'(?i)(["\'](?:content|summary|description|body|response)["\']\s*:\s*)["\'][^"\']*["\']'
)
_TOKENISH = re.compile(r"\b[A-Za-z0-9_+=/-]{40,}\b")
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+")
HTTP_STATUS = re.compile(r"(?i)\b(?:http(?: error)?|status(?: code)?)\D{0,12}([45]\d\d)\b")
RETRY_AFTER = re.compile(r"(?i)\bretry[- ]after\D{0,8}(\d+(?:\.\d+)?)")
_DIAGNOSTIC_LIMIT = 1200


class PendingRef(Protocol):
    draft_id: str


class ConfirmWatchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str = "watch",
        fatal: bool = False,
        child_returncode: int | None = None,
        stdout: str | bytes | None = None,
        stderr: str | bytes | None = None,
        http_status: int | None = None,
        retry_after: str | None = None,
        timeout_seconds: int | None = None,
        cause_type: str = "",
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.fatal = fatal
        self.child_returncode = child_returncode
        self.stdout = bounded_diagnostic(stdout)
        self.stderr = bounded_diagnostic(stderr)
        self.http_status = http_status
        self.retry_after = redact(retry_after or "")
        self.timeout_seconds = timeout_seconds
        self.cause_type = cause_type


class ConfirmBatchError(RuntimeError):
    def __init__(self, failures: tuple[ConfirmWatchError, ...]) -> None:
        super().__init__(f"calendar confirmation tick failed n={len(failures)}")
        self.failures = failures
        child_codes = [error.child_returncode for error in failures if error.child_returncode]
        self.exit_code = child_codes[0] if child_codes and 0 < child_codes[0] < 126 else 1


def redact(text: str) -> str:
    cleaned = _ANSI_ESCAPE.sub("", text)
    cleaned = _SECRET_FIELD.sub(r"\1=[REDACTED]", cleaned)
    cleaned = _SENSITIVE_JSON_FIELD.sub(r'\1"[REDACTED]"', cleaned)
    cleaned = _URL_QUERY.sub(r"\1?[REDACTED]", cleaned)
    cleaned = _TOKENISH.sub("[REDACTED-VALUE]", cleaned)
    cleaned = _EMAIL.sub("[MASKED-EMAIL]", cleaned)
    return _LONG_DIGITS.sub("[MASKED-NUM]", cleaned)


def bounded_diagnostic(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return redact(coerce_text(value)[-_DIAGNOSTIC_LIMIT:])[:_DIAGNOSTIC_LIMIT]


def coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def extract_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    return int(match.group(1)) if match else None


def extract_text(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def _retry_after_header(error: HTTPError) -> str | None:
    if error.headers is None:
        return None
    value = error.headers.get("Retry-After")
    return str(value) if value is not None else None


def transport_failure(message: str, stage: str, error: Exception) -> ConfirmWatchError:
    return ConfirmWatchError(
        message,
        stage=stage,
        fatal=True,
        http_status=error.code if isinstance(error, HTTPError) else None,
        retry_after=_retry_after_header(error) if isinstance(error, HTTPError) else None,
        cause_type=type(error).__name__,
    )


def _draft_ref(draft_id: str) -> str:
    return hashlib.sha256(draft_id.encode("utf-8")).hexdigest()[:12]


def log_failure(error: ConfirmWatchError, entry: PendingRef | None = None) -> None:
    record: dict[str, object] = {
        "event": "calendar_confirm_watch_failure",
        "stage": error.stage,
        "error": redact(str(error))[:300],
        "cause_type": error.cause_type or type(error).__name__,
        "retryable": bool(
            error.http_status == 429
            or (error.http_status is not None and 500 <= error.http_status <= 599)
            or error.cause_type in {"URLError", "TimeoutExpired", "ConnectionError"}
        ),
    }
    if entry is not None:
        record["draft_ref"] = _draft_ref(entry.draft_id)
    if error.stage.startswith("subprocess."):
        record["action"] = error.stage.removeprefix("subprocess.")
    optional = {
        "child_returncode": error.child_returncode,
        "http_status": error.http_status,
        "retry_after": error.retry_after or None,
        "timeout_seconds": error.timeout_seconds,
        "stderr_tail": error.stderr or None,
        "stdout_tail": error.stdout or None,
    }
    record.update({key: value for key, value in optional.items() if value is not None})
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), file=sys.stderr)
