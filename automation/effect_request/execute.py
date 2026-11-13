"""What runs after the owner's ✅: a Drive save or a frozen command, each exactly once.

Both executors re-derive the action hash from what is on disk before anything leaves the
node, so a frozen file or argv that changed after the card was posted runs nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Final

from .model import COMMAND, DRIVE_SAVE, EffectRequest, EffectRequestError, action_hash, file_sha256

Publisher = Callable[..., object]
FolderSaver = Callable[..., object]
Runner = Callable[..., subprocess.CompletedProcess[str]]


class BatchIncomplete(EffectRequestError):
    """Some files of a batch were not saved; ``receipt`` holds every file's own result.

    Owner Q549: the rest is saved, each file keeps its result, and a retry under the same
    approval tries only the failed files. ``retryable`` is true while any failure may pass
    on a later try (a Drive error), false when every failure is final (changed bytes).
    """

    def __init__(self, message: str, receipt: dict[str, object], retryable: bool) -> None:
        super().__init__(message, retryable=retryable)
        self.receipt = receipt

_EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_DIGITS: Final = re.compile(r"\d{7,}")
_SECRET: Final = re.compile(r"(?i)(token|secret|password|key)\s*[=:]\s*\S+")
TAIL_CHARS: Final = 400


def mask(text: str) -> str:
    return _LONG_DIGITS.sub("[번호]", _EMAIL.sub("[메일주소]", _SECRET.sub(r"\1=[가림]", text)))


def verify_binding(record: EffectRequest) -> None:
    if action_hash(record.effect, record.summary, record.payload) != record.action_hash:
        raise EffectRequestError("요청 내용이 승인 카드의 해시와 다르다 — 실행하지 않았다")


def _frozen_file(record: EffectRequest) -> Path:
    path = Path(str(record.payload["frozen_path"]))
    try:
        digest, size = file_sha256(path)
    except OSError as error:
        raise EffectRequestError("동결한 파일을 읽을 수 없다 — 저장하지 않았다") from error
    if (digest, size) != (record.payload["sha256"], record.payload["size"]):
        raise EffectRequestError("동결한 파일이 승인 뒤 바뀌었다 — 저장하지 않았다")
    return path


def save_to_drive(record: EffectRequest, publisher: Publisher) -> dict[str, object]:
    """Upload the frozen copy owner-only to the bound folder; the facade reads it back by sha256."""
    path = _frozen_file(record)
    payload = record.payload
    try:
        result = publisher(
            str(payload["kind"]), str(payload["title"]), [(path, str(payload["title"]))],
            on=date.fromisoformat(str(payload["on"])),
            project=payload.get("project") or None,
        )
    except Exception as error:  # noqa: BLE001 - every facade failure becomes a retryable receipt
        raise EffectRequestError(f"Drive 저장 실패: {type(error).__name__}: {mask(str(error))[:200]}",
                                 retryable=True) from error
    links = tuple(getattr(result, "links", ()) or ())
    return {"links": list(links), "action": str(getattr(result, "action", "")),
            "sha256": payload["sha256"], "owner_only": True}


def _frozen_item(item: dict[str, object]) -> Path:
    path = Path(str(item["frozen_path"]))
    try:
        digest, size = file_sha256(path)
    except OSError as error:
        raise EffectRequestError("동결한 파일을 읽을 수 없다 — 저장하지 않았다") from error
    if (digest, size) != (item["sha256"], item["size"]):
        raise EffectRequestError("동결한 파일이 승인 뒤 바뀌었다 — 저장하지 않았다")
    return path


def _save_item(payload: dict[str, object], item: dict[str, object], path: Path,
               publisher: Publisher, folder_saver: FolderSaver | None) -> object:
    if payload["mode"] == "folder":
        if folder_saver is None:
            from automation.drive_outputs import save_to_folder as folder_saver
        return folder_saver(path, str(item["drive_name"]), str(payload["folder_id"]),
                            sha256=str(item["sha256"]), size=int(item["size"]))  # type: ignore[call-overload]
    return publisher(
        str(payload["kind"]), str(item["title"]), [(path, str(item["title"]))],
        on=date.fromisoformat(str(payload["on"])), project=payload.get("project") or None,
    )


def save_batch(record: EffectRequest, publisher: Publisher,
               folder_saver: FolderSaver | None = None) -> dict[str, object]:
    """Save each listed file once; files the stored receipt already holds as saved are skipped."""
    payload = record.payload
    prior = (record.receipt or {}).get("files")
    kept = {int(row["index"]): row for row in prior if isinstance(row, dict)} if isinstance(prior, list) else {}
    rows: list[dict[str, object]] = []
    for index, item in enumerate(payload["files"], start=1):  # type: ignore[arg-type]
        before = kept.get(index)
        if before is not None and (before.get("status") == "ok" or before.get("retryable") is False):
            rows.append(before)
            continue
        row: dict[str, object] = {"index": index, "name": item["drive_name"], "sha256": item["sha256"]}
        try:
            result = _save_item(payload, item, _frozen_item(item), publisher, folder_saver)
        except EffectRequestError as error:
            row.update(status="failed", error=str(error)[:200], retryable=False)
        except Exception as error:  # noqa: BLE001 - every Drive failure is this file's retryable result
            row.update(status="failed", error=f"{type(error).__name__}: {mask(str(error))[:160]}", retryable=True)
        else:
            row.update(status="ok", links=list(getattr(result, "links", ()) or ()),
                       action=str(getattr(result, "action", "")))
        rows.append(row)
    failed = [row for row in rows if row["status"] != "ok"]
    receipt: dict[str, object] = {
        "files": rows, "links": [link for row in rows if row["status"] == "ok" for link in row.get("links", ())],  # type: ignore[union-attr]
        "saved": len(rows) - len(failed), "total": len(rows), "owner_only": True,
    }
    if failed:
        reasons = "; ".join(f"{row['name']}: {row['error']}" for row in failed)
        raise BatchIncomplete(
            f"{len(rows)}개 중 {len(failed)}개 저장 실패 — {reasons}",
            receipt, retryable=any(row.get("retryable") for row in failed),
        )
    return receipt


def run_command(record: EffectRequest, runner: Runner = subprocess.run) -> dict[str, object]:
    """Run the frozen argv once; a non-zero exit is a final failure, never re-run."""
    payload = record.payload
    argv = [str(item) for item in payload["argv"]]
    try:
        completed = runner(
            argv, cwd=str(payload["cwd"]), capture_output=True, text=True,
            timeout=int(payload["timeout"]), env=dict(os.environ), check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise EffectRequestError(f"제한 시간 {payload['timeout']}초를 넘겨 멈췄다") from error
    except OSError as error:
        raise EffectRequestError(f"명령을 시작하지 못했다: {type(error).__name__}") from error
    tail = mask((completed.stdout or "") + (completed.stderr or ""))[-TAIL_CHARS:]
    receipt: dict[str, object] = {"returncode": completed.returncode, "tail": tail}
    if completed.returncode != 0:
        raise EffectRequestError(f"종료 코드 {completed.returncode}: {tail[-200:]}")
    return receipt


def execute(record: EffectRequest, *, publisher: Publisher, runner: Runner = subprocess.run,
            folder_saver: FolderSaver | None = None) -> dict[str, object]:
    verify_binding(record)
    if record.effect == DRIVE_SAVE and record.payload.get("version") == 2:
        return save_batch(record, publisher, folder_saver)
    if record.effect == DRIVE_SAVE:
        return save_to_drive(record, publisher)
    if record.effect == COMMAND:
        return run_command(record, runner)
    raise EffectRequestError(f"알 수 없는 작업 종류: {record.effect}")
