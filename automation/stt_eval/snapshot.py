"""전사 옆 평가 스냅샷을 체크아웃 밖의 비공개 가설 저장소로 옮긴다."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from automation.plaud_sync.audio_manifest import manifest_path, outside_checkout
from automation.stt_eval.model import EvalRecord, dump_record, load_record


def atomic_publish(root: Path, target: Path, write: Callable[[Path], object]) -> Path:
    """가설·정답이 같은 권한과 원자적 교체 경계를 공유한다."""
    target = outside_checkout(target)
    temporary: Path | None = None
    try:
        for directory in (root, *reversed(target.parent.relative_to(root).parents), target.parent):
            directory = outside_checkout(root / directory)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".snapshot-", delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
        _ = write(temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        _ = temporary.replace(target)
        return target
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError as error:
                print(f"STT-EVAL-SNAPSHOT-CLEANUP-FAIL reason={type(error).__name__}", file=sys.stderr)


def _failure(error: OSError | ValueError) -> None:
    signal = "STT-EVAL-ROOT-REFUSED" if str(error) == "STT-EVAL-ROOT-REFUSED" else f"STT-EVAL-SNAPSHOT-FAIL reason={type(error).__name__}"
    print(signal, file=sys.stderr)


def write_reference(record: EvalRecord, env: Mapping[str, str]) -> Path | None:
    """정답만 비공개 원자 저장한다. 실패 시 이전 정답을 남기고 None을 반환한다."""
    try:
        root = manifest_path(env).parent
        if record.provenance is None or record.config_sha256 is not None:
            raise ValueError("reference provenance/config")
        target = root / "reference" / f"{record.audio_sha256}.json"
        return atomic_publish(root, target, lambda path: dump_record(record, path))
    except (OSError, ValueError) as error:
        _failure(error)
        return None


def move_snapshot(transcript: Path, env: Mapping[str, str]) -> Path | None:
    """실패는 원본과 전사본을 남기고 비식별 신호만 출력한다. 원장 쓰기는 하지 않는다."""
    source = transcript.with_suffix(".eval.json")
    try:
        if not source.exists():
            return None
        root = manifest_path(env).parent
        record = load_record(source)
        if record.config_sha256 is None:
            raise ValueError("missing config_sha256")
        target = outside_checkout(root / "hyp" / record.config_sha256 / f"{record.audio_sha256}.json")
        _ = atomic_publish(root, target, lambda path: shutil.copyfile(source, path))
        source.unlink()
        return target
    except (OSError, ValueError) as error:
        _failure(error)
        return None
