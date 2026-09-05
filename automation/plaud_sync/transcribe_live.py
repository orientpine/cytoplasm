"""Live bindings for the local transcription step — MCP, S3, the speechtotext CLI, locks.

Runs AFTER the tick's approval pass, with ``watch.lock`` released: local transcription is
about 0.4× real time on the node (a 117-minute recording ≈ 45 minutes) and holding the
watch lock that long would delay every ✅ by the same amount. What must stay serialized
is the STATE write, so ``commit`` re-takes ``watch.lock`` (blocking) for one
load → verify → save. The whisper toolchain is the resource this shares with the
speechtotext Drive watcher, so the whole step runs under ``automation.pipeline_lock``
(규약 (n)) and yields with a busy line while that watcher is transcribing.

The child gets ``SPEECHTOTEXT_BACKEND=local`` and ``DRIVE_PUBLISH_ENABLED=0`` on top of the
inherited environment: lifelog audio never leaves the node (the CLI exits 4 instead of
falling back to the API) and a personal recording is never published to Drive — the
approved Obsidian note is its only destination.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Final

from automation import pipeline_lock
from automation.skill_mount import skill_scripts

from .audio import (
    DEFAULT_MAX_AUDIO_BYTES,
    AudioError,
    AudioSource,
    AudioTooLargeError,
    Opener,
    download,
    open_url,
    parse_source,
)
from .fetch import CloudTranscript, fetch_summary, fetch_transcript
from .lifelog_extract_live import build_extractor
from .lifelog_fields import note_timezone
from .lifelog_model import ExtractionOutcome, LifelogRecording
from .mcp_client import PlaudMcpClient, PlaudMcpError, text_content
from .model import PlaudSyncRecord, PlaudSyncState
from .store import load_note_body, load_state, save_note_body, save_state, save_transcript
from .transcribe import (
    DEFAULT_MAX_ATTEMPTS,
    CliResult,
    TranscribeError,
    candidates,
    run_step,
)

KILL_SWITCH_ENV: Final = "PLAUD_SYNC_TRANSCRIBE"
CLI_ENV: Final = "SPEECHTOTEXT_CLI"
SCRIPTS_ENV: Final = "SPEECHTOTEXT_SCRIPTS"
DEFAULT_CLI_TIMEOUT: Final = 21600.0
BUSY_LINE: Final = "plaud-sync: transcribe busy (pipeline lock held)"
_CHILD_OVERRIDES: Final = {
    "SPEECHTOTEXT_BACKEND": "local",
    # Lifelog is not meeting minutes: marked gaps beat an empty note; meetings still refuse them.
    "SPEECHTOTEXT_ALLOW_INCOMPLETE": "1",
    "DRIVE_PUBLISH_ENABLED": "0",
}
_MCP_ERRORS: Final = (PlaudMcpError, OSError, ValueError)
_DETAIL_LIMIT: Final = 160
_REPO_ROOT: Final = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class StepSummary:
    line: str | None
    promoted: int


def enabled(env: Mapping[str, str]) -> bool:
    return env.get(KILL_SWITCH_ENV, "1").strip() != "0"


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def cli_path(env: Mapping[str, str]) -> Path:
    override = env.get(CLI_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return skill_scripts("speechtotext", env_var=SCRIPTS_ENV, env=env) / "speechtotext_cli.py"


class _BlockingLock:
    __slots__: tuple[str, ...] = ("_handle", "_path")

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None

    def __enter__(self) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = self._path.open("a", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        self._handle = handle

    def __exit__(self, *_exception: object) -> bool:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        return False


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _cli_result(completed: subprocess.CompletedProcess[str]) -> CliResult:
    if completed.returncode != 0:
        detail = _last_line(completed.stdout) or _last_line(completed.stderr)
        return CliResult(completed.returncode, None, "", detail[:_DETAIL_LIMIT])
    try:
        summary = json.loads(_last_line(completed.stdout) or "null")
    except ValueError:
        summary = None
    if not isinstance(summary, dict) or not isinstance(summary.get("transcript_path"), str):
        return CliResult(1, None, "", "CLI 요약 JSON 에 transcript_path 가 없다")
    model = summary.get("model")
    return CliResult(0, Path(summary["transcript_path"]), model if isinstance(model, str) else "", "")


@dataclass(frozen=True, slots=True)
class LiveEffects:
    state_dir: Path
    lock_path: Path
    env: Mapping[str, str]
    opener: Opener = open_url

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def tz(self) -> tzinfo:
        return note_timezone(self.env)[0]

    def extract(self, recording: LifelogRecording) -> ExtractionOutcome:
        """The gated LLM extractor (rules → patent → key → template) on the finalized recording."""
        return build_extractor(self.env, repo_root=_REPO_ROOT)(recording)

    def draft_body(self, recording_id: str) -> str | None:
        return load_note_body(self.state_dir, recording_id)

    def fetch_source(self, recording_id: str) -> AudioSource:
        try:
            with PlaudMcpClient() as client:
                text = text_content(client.call_tool("get_file", {"file_id": recording_id}))
            return parse_source(text, recording_id)
        except (*_MCP_ERRORS, AudioError) as error:
            raise TranscribeError(
                f"get_file: {type(error).__name__}: {error}", counted=False
            ) from error

    def fetch_summary(self, recording_id: str) -> str:
        try:
            with PlaudMcpClient() as client:
                return fetch_summary(client, recording_id)
        except _MCP_ERRORS:
            return ""

    def fetch_transcript(self, recording_id: str) -> CloudTranscript:
        try:
            with PlaudMcpClient() as client:
                return fetch_transcript(client, recording_id)
        except _MCP_ERRORS:
            return CloudTranscript("")

    def download(self, source: AudioSource) -> Path:
        dest = self.state_dir / "audio" / f"{source.recording_id}{source.suffix}"
        cap = _env_int(self.env, "PLAUD_SYNC_MAX_AUDIO_BYTES", DEFAULT_MAX_AUDIO_BYTES)
        try:
            return download(source, dest, max_bytes=cap, opener=self.opener)
        except AudioTooLargeError as error:
            raise TranscribeError(str(error), counted=True) from error
        except AudioError as error:
            raise TranscribeError(str(error), counted=False) from error

    def transcribe(self, audio: Path, label: str) -> CliResult:
        cli = cli_path(self.env)
        if not cli.is_file():
            raise TranscribeError(f"speechtotext CLI 미마운트: {cli}", counted=False)
        work = self.state_dir / "transcripts" / ".work"
        child = {**self.env, **_CHILD_OVERRIDES, "SPEECHTOTEXT_TRANSCRIPT_DIR": str(work)}
        argv = [sys.executable, str(cli), "transcribe", "--file", str(audio), "--label", label]
        try:
            completed = subprocess.run(  # noqa: S603 - argv is the resolved interpreter + governed CLI
                argv,
                env=child,
                capture_output=True,
                text=True,
                check=False,
                timeout=_env_float(self.env, "PLAUD_SYNC_TRANSCRIBE_TIMEOUT", DEFAULT_CLI_TIMEOUT),
            )
        except subprocess.TimeoutExpired as error:
            raise TranscribeError("로컬 전사가 시간 제한을 넘겼다", counted=True) from error
        except OSError as error:
            raise TranscribeError(f"CLI 실행 실패: {type(error).__name__}", counted=False) from error
        return _cli_result(completed)

    def read_transcript(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as error:
            raise TranscribeError(f"전사본을 읽지 못했다: {type(error).__name__}", counted=False) from error

    def store_transcript(self, stem: str, markdown: str) -> Path:
        return save_transcript(self.state_dir, stem, markdown)

    def commit(self, before: PlaudSyncRecord, after: PlaudSyncRecord, body: str | None) -> bool:
        with _BlockingLock(self.lock_path):
            state = load_state(self.state_path)
            current = state.records.get(before.recording_id)
            if (
                current is None
                or current.status != "transcribing"
                or current.action_hash != before.action_hash
            ):
                return False
            if body is not None:
                save_note_body(self.state_dir, after.recording_id, body)
            records = dict(state.records)
            records[after.recording_id] = after
            save_state(self.state_path, PlaudSyncState(state.version, state.last_poll_at, records))
        return True

    def discard_audio(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return


def run_transcribe_step(*, state_dir: Path, lock_path: Path, env: Mapping[str, str]) -> StepSummary | None:
    if not enabled(env):
        return None
    state_path = state_dir / "state.json"
    limit = _env_int(env, "PLAUD_SYNC_TRANSCRIBE_PER_TICK", 1)
    outcomes: tuple[tuple[str, str], ...] = ()
    with pipeline_lock.hold(env) as acquired:
        state = load_state(state_path)
        if not candidates(state, limit=1):
            return None
        if not acquired:
            return StepSummary(BUSY_LINE, 0)
        outcomes = run_step(
            state,
            effects=LiveEffects(state_dir=state_dir, lock_path=lock_path, env=env),
            limit=limit,
            max_attempts=_env_int(env, "PLAUD_SYNC_TRANSCRIBE_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
        )
    counts = Counter(outcome for _, outcome in outcomes)
    line = (
        f"plaud-sync: transcribed={counts['planned']} fallback={counts['fallback']} "
        f"retry={counts['retry']} stale={counts['stale']}"
    )
    return StepSummary(line, counts["planned"] + counts["fallback"])
