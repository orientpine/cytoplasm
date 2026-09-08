"""성공 재사용과 비공개 산출물·append 원장 저장을 맡는다."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TypeAlias

from automation.stt_eval.model import EvalRecordError, load_record
from automation.stt_eval.runner_inputs import RunnerInputError, read_jsonl, sha

Row: TypeAlias = dict[str, object]


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def append(path: Path, row: Row) -> None:
    payload = (json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > 4096:
        raise RunnerInputError("journal row exceeds 4KiB")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        if os.write(fd, payload) != len(payload):
            raise OSError("short journal write")
        os.fsync(fd)
    finally:
        os.close(fd)


def history(root: Path) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    labels: dict[str, str] = {}
    completed: dict[tuple[str, str], str] = {}
    path = root / "candidates.jsonl"
    for row in read_jsonl(path) if path.exists() else []:
        label, digest = row.get("label"), sha(row.get("config_sha256"))
        if not isinstance(label, str) or label in labels and labels[label] != digest:
            raise RunnerInputError("CANDIDATE-DRIFT")
        labels[label] = digest
    path = root / "runs.jsonl"
    for row in read_jsonl(path) if path.exists() else []:
        label, audio = row.get("label"), sha(row.get("audio_sha256"))
        if not isinstance(label, str) or row.get("status") not in ("ok", "failed"):
            raise RunnerInputError("run fields")
        if row["status"] == "ok":
            digest = sha(row.get("config_sha256"))
            if labels.get(label) != digest:
                raise RunnerInputError("candidate journal mismatch")
            completed[label, audio] = digest
    return labels, completed


def reusable(root: Path, audio: str, digest: str | None) -> bool:
    if digest is None:
        return False
    try:
        record = load_record(root / "hyp" / digest / f"{audio}.json")
    except EvalRecordError:
        return False
    return record.audio_sha256 == audio and record.config_sha256 == digest and record.status != "failed"


def install(source: Path, root: Path, digest: str, audio: str) -> None:
    """다른 파일시스템의 tmp에서도 대상 디렉터리 안에서 원자 교체한다."""
    parent = root / "hyp" / digest
    private_dir(root / "hyp")
    private_dir(parent)
    name: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=parent, prefix=".install-", delete=False) as handle:
            name = Path(handle.name)
            _ = handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        _ = name.replace(parent / f"{audio}.json")
    finally:
        if name is not None:
            name.unlink(missing_ok=True)
