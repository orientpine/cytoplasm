"""GPU 유휴 상태에서 Drive 녹음과 후보를 순차 평가한다.

manifest/reference는 읽기 전용이다. 완료 원장이 있고 hyp 검증이 되면 재실행하지
않고, 원장 없는 기존 hyp는 새 산출물로 교체한다. 후보 라벨은 실행 간 불변이다.
실패는 다음 실행에서 재시도하며, config 해시는 CLI가 만든 값만 사용한다.
"""
from __future__ import annotations

import hashlib
import math
import os
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol

from automation.drive_client import DriveClientError
from automation.stt_eval import gpu_guard, runner_store
from automation.stt_eval.runner_inputs import (
    Candidate as Candidate,
    Configs,
    Manifest,
    Recording,
    RunnerInputError,
    candidates,
    cpu_limited,
    cpu_max_ms,
    model_missing,
    private_root,
    recordings,
    resolve_environment,
)
from automation.stt_eval.runner_process import Cli, Outcome, cancellation, execute

MIN_TIMEOUT_SECS = 300.0
DEFAULT_TIMEOUT_FACTOR = 10.0
#: 원음 머리 바이트 → 전사 CLI 가 받는 확장자. CLI 는 확장자로만 형식을 거르므로 `audio.bin`
#: 으로는 한 번도 전사되지 않았다(2026-09-22 노드 실측: 후보 전부 rc=5 "지원하지 않는 형식").
_AUDIO_MAGIC: Final = (
    (b"OggS", 0, ".ogg"), (b"fLaC", 0, ".flac"), (b"ID3", 0, ".mp3"), (b"\xff\xfb", 0, ".mp3"),
    (b"\xff\xf3", 0, ".mp3"), (b"\xff\xf2", 0, ".mp3"), (b"ftyp", 4, ".m4a"), (b"WAVE", 8, ".wav"),
    (b"\x1a\x45\xdf\xa3", 0, ".webm"),
)


class DriveLike(Protocol):
    """DriveClient의 읽기 전용 인터페이스를 그대로 받는다."""

    def verify_owner_only(self, file_id: str) -> None: ...
    def download_file(self, file_id: str, dest: Path, *, export_as: str = "") -> str: ...


def _download(drive: DriveLike, recording: Recording, audio: Path) -> str:
    try:
        drive.verify_owner_only(recording.drive_file_id)
        _ = drive.download_file(recording.drive_file_id, audio)
        audio.chmod(0o600)
        with audio.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        return "" if digest == recording.audio_sha256 else "audio-mismatch"
    except (DriveClientError, OSError):
        return "drive-failed"


def _named(audio: Path) -> Path:
    """알아본 형식이면 그 확장자로 옮긴다. 모르는 형식은 그대로 두어 CLI 가 판정한다."""
    with audio.open("rb") as handle:
        head = handle.read(16)
    suffix = next((ext for magic, at, ext in _AUDIO_MAGIC if head[at:at + len(magic)] == magic), "")
    return audio.rename(audio.with_suffix(suffix)) if suffix else audio


def _save(root: Path, label: str, recording: Recording, outcome: Outcome,
          secs: float, labels: dict[str, str]) -> bool:
    digest, reason = outcome.config_sha256, outcome.reason
    if not reason and digest is not None and outcome.artifact is not None:
        if label in labels and labels[label] != digest:
            reason = "CANDIDATE-DRIFT"
        else:
            runner_store.install(outcome.artifact, root, digest, recording.audio_sha256)
            if label not in labels:
                runner_store.append(root / "candidates.jsonl", {"label": label, "config_sha256": digest})
                labels[label] = digest
    runner_store.append(root / "runs.jsonl", {
        "at": datetime.now(timezone.utc).isoformat(), "label": label,
        "audio_sha256": recording.audio_sha256, "status": "failed" if reason else "ok",
        "reason": reason, "config_sha256": digest, "rc": outcome.rc, "secs": round(secs, 3),
    })
    print(f"RUN config={digest[:8] if digest else 'null'} audio={recording.audio_sha256[:8]} "
          + f"rc={outcome.rc} secs={secs:.3f}")
    return not reason


def _sweep(manifest: tuple[Recording, ...], configs: tuple[Candidate, ...], root: Path,
           *, drive: DriveLike, cli: Cli, tmp: Path, env: Mapping[str, str], factor: float,
           cpu_maximum: int | None) -> int:
    labels, completed = runner_store.history(root)
    runner_store.private_dir(root)
    runner_store.private_dir(tmp)
    failed = False
    for recording in manifest:
        pending = [candidate for candidate in configs if not runner_store.reusable(
            root, recording.audio_sha256, completed.get((candidate.label, recording.audio_sha256)))]
        if not pending:
            continue
        with tempfile.TemporaryDirectory(prefix="stt-eval-", dir=tmp) as directory:
            audio = Path(directory) / "audio.bin"
            download_reason: str | None = None
            for candidate in pending:
                started = time.monotonic()
                child = resolve_environment({**env, **dict(candidate.env_overrides)})
                if cpu_limited(candidate, recording, cpu_maximum):
                    print(f"DIARIZE-SKIP reason=cpu-only audio={recording.audio_sha256[:8]}")
                    outcome = Outcome("cpu-only")
                elif model_missing(child):
                    outcome = Outcome("model-missing")
                else:
                    if download_reason is None:
                        download_reason = _download(drive, recording, audio)
                        if not download_reason:
                            audio = _named(audio)
                    outcome = Outcome(download_reason)
                with tempfile.TemporaryDirectory(prefix="candidate-", dir=directory) as work:
                    if not outcome.reason:
                        outcome = execute(cli, child, Path(work), audio, recording,
                                          max(MIN_TIMEOUT_SECS, factor * recording.duration_ms / 1000))
                    success = _save(root, candidate.label, recording, outcome,
                                    time.monotonic() - started, labels)
                    # 정책 제외는 성공 산출물이 아니지만 스윕 오류로도 취급하지 않는다.
                    failed = failed or (not success and outcome.reason != "cpu-only")
    return 1 if failed else 0


def run(manifest: Manifest, configs: Configs, root: Path, *, drive: DriveLike,
        cli: Cli, tmp: Path, env: Mapping[str, str] | None = None) -> int:
    """CLI 호출자는 반환 코드를 종료 코드로 사용한다(입력 2, 루트 3, busy 6)."""
    environment = dict(os.environ if env is None else env)
    try:
        root, tmp = private_root(root), private_root(tmp)
    except RunnerInputError:
        print("STT-EVAL-ROOT-REFUSED")
        return 3
    try:
        selected, records = candidates(configs), recordings(manifest)
        cpu_maximum = cpu_max_ms(environment)
        cli = cli.expanduser().resolve() if isinstance(cli, Path) else tuple(cli)
        factor = float(environment.get("STT_EVAL_TIMEOUT_FACTOR", str(DEFAULT_TIMEOUT_FACTOR)))
        if not math.isfinite(factor) or factor < 0:
            raise RunnerInputError("timeout factor")
    except (ValueError, OSError, TypeError):
        print("STT-EVAL-INPUT-INVALID")
        return 2
    with cancellation(), gpu_guard.check(environment) as verdict:
        if verdict.exit_code:
            print(f"GPU-BUSY {verdict.reason}")
            return verdict.exit_code
        try:
            return _sweep(records, selected, root, drive=drive, cli=cli,
                          tmp=tmp, env=environment, factor=factor, cpu_maximum=cpu_maximum)
        except ValueError:
            print("STT-EVAL-JOURNAL-INVALID")
            return 2
