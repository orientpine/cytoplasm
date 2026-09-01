"""The watched Drive folder: what to pick up, and what was already handled.

Polling Drive is a **read**, so it needs no approval gate — but two fail-closed
rules keep it honest: an unconfigured folder does nothing at all (never a guess
at which folder the owner meant), and a recording is marked processed only
after the meeting chain succeeded (설계규약 (f)), so a failed tick retries
instead of silently dropping a meeting.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

import stt_audio

FOLDER_ENV: Final = "SPEECHTOTEXT_DRIVE_FOLDER"
STATE_ENV: Final = "SPEECHTOTEXT_STATE_FILE"
DEFAULT_STATE: Final = "~/.hermes/speechtotext/state.json"
DIR_MODE: Final = 0o700
FILE_MODE: Final = 0o600

NO_FOLDER_NOTICE: Final = (
    "감시할 Drive 폴더가 설정되지 않았습니다: SPEECHTOTEXT_DRIVE_FOLDER 에 폴더 경로를 "
    "지정해 주세요. 어떤 폴더인지 추측하지 않고 중단합니다."
)


class DriveScanRefused(Exception):
    """The watcher cannot know what to scan, so it does nothing (fail closed)."""

    def __init__(self, notice: str, exit_code: int) -> None:
        super().__init__(notice)
        self.notice = notice
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class DriveAudio:
    """A recording in the watched folder that has not been ingested yet."""

    file_id: str
    name: str

    @property
    def label(self) -> str:
        return Path(self.name).stem


def folder_parts(env: Mapping[str, str]) -> tuple[str, ...]:
    """Split the configured folder path; refuse when it is unset."""
    raw = (env.get(FOLDER_ENV) or "").strip().strip("/")
    if not raw:
        raise DriveScanRefused(NO_FOLDER_NOTICE, exit_code=4)
    return tuple(part for part in raw.split("/") if part)


def is_audio(name: str) -> bool:
    """True when the Drive file name carries a transcribable audio extension."""
    return Path(name).suffix.lower() in stt_audio.SUPPORTED_SUFFIXES


def state_path(env: Mapping[str, str]) -> Path:
    raw = env.get(STATE_ENV) or DEFAULT_STATE
    if raw.startswith("~"):
        home = env.get("HOME")
        return Path(raw.replace("~", home, 1)) if home else Path(raw).expanduser()
    return Path(raw)


def load_state(path: Path) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    processed = raw.get("processed") if isinstance(raw, dict) else None
    return processed if isinstance(processed, dict) else {}


def save_state(path: Path, processed: Mapping[str, dict[str, str]]) -> None:
    path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump({"processed": dict(processed)}, handle, ensure_ascii=False, sort_keys=True)
        staged = Path(handle.name)
    staged.chmod(FILE_MODE)
    os.replace(staged, path)


def pending(
    children: Sequence[Mapping[str, object]], processed: Mapping[str, dict[str, str]]
) -> tuple[DriveAudio, ...]:
    """Audio files in the folder that no successful tick has claimed yet."""
    return tuple(
        DriveAudio(file_id=str(child.get("id", "")), name=str(child.get("name", "")))
        for child in children
        if str(child.get("id", "")) and is_audio(str(child.get("name", "")))
        and str(child.get("id", "")) not in processed
    )


def mark_processed(
    path: Path, audio: DriveAudio, *, now: datetime, digest: str
) -> dict[str, dict[str, str]]:
    """Record success — called only after the meeting chain returned 0."""
    processed = load_state(path)
    processed[audio.file_id] = {
        "name": audio.name,
        "sha256": digest,
        "at": now.isoformat(timespec="seconds"),
    }
    save_state(path, processed)
    return processed
