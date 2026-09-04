"""Transcription over the OpenAI-compatible ``POST /audio/transcriptions`` contract.

stdlib only (repo convention): the multipart body is assembled by hand and sent
with ``urllib.request`` so no third-party SDK enters the runtime. ``base_url``
is a plain override, which is what makes a LiteLLM gateway
(``model_info.mode: audio_transcription``) a drop-in replacement for the
provider endpoint without touching a caller.

Failure is always fail-closed and never echoes the credential: a missing key
refuses before a byte leaves the node, and a provider error is reduced to
status + provider message.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, TypeAlias
from urllib import error, request

import stt_audio
import stt_blocks
import stt_coverage

DEFAULT_BASE_URL: Final = "https://api.openai.com/v1"
DEFAULT_MODEL: Final = "gpt-4o-transcribe"
DEFAULT_LANGUAGE: Final = "ko"
DEFAULT_TIMEOUT: Final = 600.0
_RESPONSE_FORMAT: Final = "json"

Opener: TypeAlias = Callable[[request.Request, float], tuple[int, bytes, str]]


class SttError(RuntimeError):
    """The transcription call could not be made or the provider refused it."""

    def __init__(self, message: str, exit_code: int = 6) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class Transcription:
    """A non-empty transcript plus the provenance the transcript header records."""

    text: str
    model: str
    endpoint: str
    coverage: stt_coverage.Coverage | None = None
    # Empty whenever the backend cannot say when a sentence was spoken (the API
    # returns text only). The document then renders exactly as it does today.
    sentences: tuple[stt_blocks.TimedSentence, ...] = ()


def build_multipart(
    fields: Mapping[str, str], *, filename: str, mime: str, payload: bytes
) -> tuple[bytes, str]:
    """Assemble a binary-safe multipart/form-data body; returns (body, content-type)."""
    boundary = f"----autophagy-stt-{secrets.token_hex(16)}"
    body = bytearray()
    for name, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    body += payload
    body += b"\r\n"
    body += f"--{boundary}--\r\n".encode("ascii")
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _urlopen(req: request.Request, timeout: float) -> tuple[int, bytes, str]:
    with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - explicit https/http endpoint
        return response.status, response.read(), response.headers.get_content_type()


def _provider_message(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", "replace").strip()[:200]
    detail = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message", ""))[:200]
    return str(payload)[:200]


def _decode(body: bytes, content_type: str) -> str:
    if content_type != "application/json":
        return body.decode("utf-8", "replace")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as failure:
        raise SttError("전사 API 응답을 해석하지 못했습니다.") from failure
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise SttError("전사 API 응답에 text 필드가 없습니다.")
    return payload["text"]


def transcribe(
    audio: stt_audio.CheckedAudio,
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
    prompt: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    opener: Opener | None = None,
) -> Transcription:
    """Transcribe ``audio``; refuse fail-closed rather than return a doubtful text."""
    if not api_key:
        raise SttError(
            "전사 자격증명이 없습니다: OPENAI_API_KEY 를 ~/.env.secrets 에 설정해 주세요."
        )
    endpoint = f"{base_url.rstrip('/')}/audio/transcriptions"
    fields = {"model": model, "response_format": _RESPONSE_FORMAT}
    if language:
        fields["language"] = language
    if prompt:
        fields["prompt"] = prompt
    body, content_type = build_multipart(
        fields, filename=audio.path.name, mime=audio.mime, payload=audio.path.read_bytes()
    )
    req = request.Request(  # noqa: S310 - endpoint is an explicit http(s) base url
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        },
    )
    try:
        status, raw, response_type = (opener or _urlopen)(req, timeout)
    except error.HTTPError as failure:
        detail = _provider_message(failure.read())
        raise SttError(f"전사 API 오류 status={failure.code}: {detail}") from None
    except (error.URLError, TimeoutError, OSError) as failure:
        raise SttError(f"전사 API 연결 실패: {type(failure).__name__}") from None
    if status >= 400:
        raise SttError(f"전사 API 오류 status={status}: {_provider_message(raw)}")
    text = _decode(raw, response_type).strip()
    if not text:
        raise stt_audio.TranscriptionRefused(stt_audio.EMPTY_TRANSCRIPT_NOTICE, exit_code=5)
    return Transcription(text=text, model=model, endpoint=endpoint)
