"""Drive 파사드의 검증 성공만 원본 삭제 허가로 바꾼다."""
from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from automation import drive_outputs
from automation.drive_client import DriveClient

from .audio import AudioSource
from .audio_manifest import (
    ManifestEntry, append_locked, audio_digest, contains, manifest_lock, manifest_path,
    outside_checkout,
)


def _file_id(link: str) -> str:
    parsed = urlsplit(link)
    if parsed.scheme != "https" or parsed.netloc != "drive.google.com":
        raise ValueError("Drive link")
    match = re.fullmatch(r"/file/d/([A-Za-z0-9_-]+)/view", parsed.path)
    file_id = match.group(1) if match else parse_qs(parsed.query).get("id", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
        raise ValueError("Drive file id")
    return file_id


def _notice(error: Exception) -> None:
    reason = "STT-EVAL-ROOT-REFUSED" if str(error) == "STT-EVAL-ROOT-REFUSED" else type(error).__name__
    print(f"AUDIO-ARCHIVE-FAIL reason={reason}", file=sys.stderr)


def archive_audio(
    audio: Path, source: AudioSource, stem: str, env: Mapping[str, str], *,
    client: DriveClient | None = None, expected_sha256: str | None = None,
) -> bool:
    """다운로드 파일은 변환·재인코딩 없이 전달하고 SHA 하나당 원장 한 행만 쓴다."""
    if env.get("DRIVE_PUBLISH_ENABLED") != "1":
        return False
    try:
        path = manifest_path(env)
        with manifest_lock(path):
            digest = audio_digest(audio)
            if expected_sha256 is not None and expected_sha256 != digest:
                raise ValueError("audio sha256 mismatch")
            if contains(path, digest):
                return True
            # 메타데이터 오류는 원격 부수효과 전에 판정한다.
            entry = ManifestEntry(digest, "pending", source.duration_ms, stem, source.created_at)
            _ = entry.row()
            drive = client if client is not None else drive_outputs.client_from_environment()
            _ = outside_checkout(drive.folder_cache)
            title = digest[:12]
            result = drive_outputs.publish_best_effort(
                kind="audio", title=title, artifacts=[(audio, title)], project="lifelog",
                on=datetime.fromisoformat(source.created_at).date(), client=drive,
            )
            if result is None or len(result.links) != 1:
                raise ValueError("publication unverified")
            if audio_digest(audio) != digest:
                raise ValueError("audio changed during upload")
            entry = ManifestEntry(digest, _file_id(result.links[0]), source.duration_ms,
                                  stem, source.created_at)
            _ = append_locked(path, entry)
        return True
    except Exception as error:
        _notice(error)
        return False


def may_discard(audio: Path, env: Mapping[str, str]) -> bool:
    """옵트아웃은 기존 삭제 정책, 옵트인은 원장에 검증된 현재 바이트만 허용한다."""
    if env.get("DRIVE_PUBLISH_ENABLED") != "1":
        return True
    try:
        path = manifest_path(env)
        with manifest_lock(path):
            return contains(path, audio_digest(audio))
    except Exception as error:
        print(f"AUDIO-DISCARD-REFUSED reason={type(error).__name__}", file=sys.stderr)
        return False
