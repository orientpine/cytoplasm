"""Atomic private budget record I/O, extracted without changing persistence semantics."""
from __future__ import annotations
import fcntl
import json
import os
import tempfile
from pathlib import Path

def _append_record(path: Path, record: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)

def write_json(path: Path, record: dict) -> None:
    """임시 파일에 쓴 뒤 이름을 갈아끼운다 — 독자가 잘린 레코드를 보지 않게.

    제자리 truncate(`write_text`)는 쓰는 동안 읽는 쪽에게 **빈 파일**을 보여준다.
    승인 producer 와 confirm 워처는 같은 레코드를 동시에 만진다(2026-08-01 실측:
    mail 경로에서 producer 가 `JSONDecodeError: ... (char 0)` 으로 사망). 같은 구현이
    여기에도 복제돼 있어 함께 고친다.

    임시 이름은 `.`로 시작하고 `.json` 으로 끝나지 않는다 — 대기 레코드를 훑는
    `*.json` glob 이 쓰다 말은 파일을 레코드로 읽으면 안 되기 때문이다.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", mode="w", encoding="utf-8", delete=False
        ) as handle:
            temporary = Path(handle.name)
            _ = handle.write(serialized)
            # flush 까지만 한다 — 찢어진 읽기를 막는 것은 `os.replace` 이고, fsync 는
            # 호출당 0.12ms 를 3.5ms 로 만든다(실측). 내구성은 PostingJournal 이 맡는다.
            handle.flush()
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
