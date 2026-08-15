"""Mail-mode resolution + W4-2-runtime re-verdict (3-state: no-go|read-go|full-go).

The repo verdict is the tracked immutable SEED (configs/mail-mode.default.json,
source W0-7c). The runtime re-verdict lives OUTSIDE the checkout at
~/.hermes/mail-triage/mail-mode.json: two consecutive approved-send failures
write it with ``source: W4-2-runtime`` and append a W4-1N switch record. The
runtime file wins over the seed; anything unreadable resolves to no-go (fail
closed). A runtime path at or beside the seed fails closed to no-go and is
never written into the checkout. Restoring full-go is a human/orchestrator
action (delete or rewrite the runtime file), never automatic.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path

import triage_core

MODES = ("no-go", "read-go", "full-go")


def gate_dir() -> Path:
    path = Path(os.environ.get("TRIAGE_GATE_DIR", "~/.hermes/mail-triage")).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def runtime_mode_file() -> Path:
    return Path(
        os.environ.get("TRIAGE_MAIL_MODE_FILE", str(gate_dir() / "mail-mode.json"))
    ).expanduser()


def repo_mode_file() -> Path:
    return Path(
        os.environ.get("TRIAGE_MAIL_MODE_REPO", "/srv/autophagy-agents/configs/mail-mode.default.json")
    ).expanduser()


def _runtime_path_shadows_seed(runtime: Path, seed: Path) -> bool:
    """Fail-closed: the runtime override must never live at (or beside) the
    tracked seed — writing there dirties the ops checkout and blocks pulls."""
    try:
        resolved_runtime = runtime.expanduser().resolve()
        resolved_seed = seed.expanduser().resolve()
    except OSError:
        return True
    return resolved_runtime == resolved_seed or resolved_runtime.parent == resolved_seed.parent


def effective_mode() -> str:
    """Runtime re-verdict wins over the repo verdict; missing both = no-go."""
    runtime, seed = runtime_mode_file(), repo_mode_file()
    if _runtime_path_shadows_seed(runtime, seed):
        return "no-go"
    for path in (runtime, seed):
        try:
            mode = json.loads(path.read_text(encoding="utf-8")).get("mode")
        except (OSError, json.JSONDecodeError):
            continue
        if mode in MODES:
            return str(mode)
    return "no-go"


def downgrade_to_no_go(reason: str) -> None:
    """W4-2-runtime re-verdict + W4-1N switch record (2 consecutive failures)."""
    previous = effective_mode()
    runtime, seed = runtime_mode_file(), repo_mode_file()
    if not _runtime_path_shadows_seed(runtime, seed):
        write_json(runtime, {
            "decided_at": triage_core.utc_now(),
            "mode": "no-go",
            "source": "W4-2-runtime",
        })
    append_record(gate_dir() / "mode-switch.jsonl", {
        "event": "w4-1n-switch",
        "from": previous,
        "reason": triage_core.redact(reason)[:300],
        "source": "W4-2-runtime",
        "timestamp": triage_core.utc_now(),
        "to": "no-go",
    })


def append_record(path: Path, record: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)


def write_json(path: Path, record: dict) -> None:
    """임시 파일에 쓴 뒤 이름을 갈아끼운다 — 독자가 잘린 레코드를 보지 않게.

    제자리 truncate(`write_text`)는 쓰는 동안 읽는 쪽에게 **빈 파일**을 보여준다.
    승인 producer 와 confirm 워처는 같은 레코드를 동시에 만지므로 실제로 발생한다 —
    2026-08-01 실측에서 producer 가 `JSONDecodeError: ... (char 0)` 으로 죽었고,
    `set_approval_binding`·`set_message_id` 는 그 읽기를 감싸지 않아 그대로 터진다.

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
            # flush 까지만 한다. 찢어진 읽기를 막는 것은 `os.replace` 이지 fsync 가
            # 아니고, 여기서 fsync 는 호출당 0.12ms 를 3.5ms 로 만든다(실측, 29배).
            # 크래시 내구성은 POST 직전에 한 번 도는 PostingJournal 이 이미 맡는다.
            handle.flush()
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
