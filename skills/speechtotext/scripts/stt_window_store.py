"""Where a window's transcript survives a crash, a quarantine, and a refusal.

A 2-hour local transcription is 45 minutes of GPU. Losing it to a fault in minute 44
is only unavoidable if nothing was written down on the way, so every window that
produced valid JSON is kept here under a key made of the audio's own digest, the model,
and the window plan — a re-run reuses those and re-decodes only what is missing. The
entry is dropped once a run loses nothing; anything else stays resumable on purpose.

Two invariants matter more than the caching:

* **This is transcribed speech.** 0700 directories, 0600 files, under the owner's
  private `~/.hermes` tree — and never inside a git checkout, where a `git add -A`
  could publish a meeting.
* **A refusal is not an empty hand.** `preserve()` writes the partial transcript
  before any refusal is raised, so the message can name a file that already exists.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import stt_client
import stt_window

DIR_MODE: Final = 0o700
FILE_MODE: Final = 0o600
CACHE_ENV: Final = "SPEECHTOTEXT_WINDOW_CACHE"
DEFAULT_CACHE_DIR: Final = "~/.hermes/speechtotext/windows"
_HASH_CHUNK: Final = 1024 * 1024

CHECKOUT_NOTICE: Final = (
    "창 캐시 경로 {path} 가 git 체크아웃 안에 있습니다. 전사된 발화가 저장소에 섞이지 "
    "않도록 중단합니다 — SPEECHTOTEXT_WINDOW_CACHE 를 체크아웃 밖으로 지정해 주세요."
)


@dataclass(frozen=True, slots=True)
class WindowStore:
    """The three places one run writes: cached windows, quarantine, partial transcript."""

    root: Path
    key: str

    @property
    def windows(self) -> Path:
        return self.root / self.key

    @property
    def quarantine_dir(self) -> Path:
        return self.root / "quarantine" / self.key

    def payload(self, window: stt_window.Window) -> Path:
        return self.windows / f"window-{window.index:05d}.json"

    def load(self, window: stt_window.Window) -> bytes | None:
        try:
            return self.payload(window).read_bytes()
        except OSError:
            return None

    def save(self, window: stt_window.Window, raw: bytes) -> None:
        self._write(self.payload(window), raw)

    def quarantine(self, window: stt_window.Window, raw: bytes) -> Path | None:
        return self._write(self.quarantine_dir / f"window-{window.index:05d}.json", raw)

    def preserve(self, text: str) -> Path | None:
        """Keep the partial transcript so a refusal never means an empty hand."""
        return self._write(self.root / "partial" / f"{self.key}.md", text.encode("utf-8"))

    def clear(self) -> None:
        """Drop the cached windows — only ever called after a run that lost nothing."""
        shutil.rmtree(self.windows, ignore_errors=True)

    def _write(self, target: Path, raw: bytes) -> Path | None:
        try:
            target.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(FILE_MODE)
        except OSError as failure:
            print(f"WINDOW-STORE-UNWRITABLE path={target} {type(failure).__name__}", file=sys.stderr)
            return None
        return target


def resolve_store(
    env: Mapping[str, str],
    *,
    audio: Path,
    model: Path,
    windows: Sequence[stt_window.Window],
    tool: Path,
) -> WindowStore:
    """The store for this recording under this plan, refusing a path inside a checkout."""
    root = Path(env.get(CACHE_ENV, "").strip() or DEFAULT_CACHE_DIR).expanduser()
    for candidate in (root, *root.parents):
        if (candidate / ".git").exists():
            raise stt_client.SttError(CHECKOUT_NOTICE.format(path=root))
    key = stt_window.cache_key(
        audio_sha256=digest(audio),
        model=model.stem,
        tool=digest(tool),
        windows=windows,
    )
    return WindowStore(root=root, key=key)


def digest(path: Path) -> str:
    """sha256 of the recording — the half of the resume key the audio itself owns."""
    hashed = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            hashed.update(chunk)
    return hashed.hexdigest()
