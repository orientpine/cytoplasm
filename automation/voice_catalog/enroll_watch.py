"""Obsidian 트리거 워처의 I/O 절반 — 미러를 읽고, enroll 자식을 돌리고, 원장을 쓰고, 통지한다.

마킹은 성공 후(설계규약 (f)), 통지 실패는 마킹을 되돌리지 않는다((i)). 자식에는 받은 env 를
그대로 넘겨 자격증명이 명시 전파된다((b-2)). 미러는 읽기만 하고 git 을 만지지 않는다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from automation.interop.owner_message import OwnerMessage
from automation.skill_mount import skill_scripts
from automation.stt_eval.cron.capture_sources import mirror_dir
from automation.voice_catalog import trigger
from skills.speechtotext.scripts import stt_catalog

MIRROR_ENV: Final = "VOICE_CATALOG_MIRROR"
CLI_ENV: Final = "VOICE_CATALOG_ENROLL_CLI"
LIFELOG_DIR: Final = "000_PARA/Area/Lifelog"
TRANSCRIPT_DIR: Final = ".hermes/plaud-sync/transcripts"
ENROLL_TIMEOUT: Final = 1800


@dataclass(frozen=True, slots=True)
class EnrollOutcome:
    ok: bool
    seconds: float = 0.0
    recording_id: str = ""
    reason: str = ""


Enroll = Callable[[Path, Path, str, str, Mapping[str, str]], EnrollOutcome]
Notify = Callable[[str, OwnerMessage], bool]


def _notify(content: str, message: OwnerMessage) -> bool:
    from automation.owner_notice import notify_owner

    return notify_owner(content, message=message)


def _enroll(cli: Path, transcript: Path, label: str, name: str, env: Mapping[str, str]) -> EnrollOutcome:
    argv = [sys.executable, str(cli), "enroll", "--transcript", str(transcript), "--speaker", label, "--name", name]
    try:
        completed = subprocess.run(  # noqa: S603 - resolved local CLI, owner-authored arguments
            argv, capture_output=True, text=True, env=dict(env), check=False, timeout=ENROLL_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        return EnrollOutcome(False, reason=f"ENROLL-{type(failure).__name__.upper()}")
    if completed.returncode != 0:
        tail = [line for line in completed.stderr.splitlines() if line.strip()]
        return EnrollOutcome(False, reason=(tail[-1] if tail else f"ENROLL-RC-{completed.returncode}")[:200])
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        return EnrollOutcome(True, float(payload["seconds"]), str(payload["recording_id"]))
    except (IndexError, ValueError, KeyError, TypeError):
        return EnrollOutcome(False, reason="ENROLL-OUTPUT-UNREADABLE")


def _cli_path(env: Mapping[str, str]) -> Path:
    return skill_scripts("speechtotext", env_var=CLI_ENV, env=env) if env.get(CLI_ENV, "").strip() else (
        skill_scripts("speechtotext", env=env) / "stt_catalog_cli.py")


def _mirror(env: Mapping[str, str], home: Path) -> Path | None:
    override = env.get(MIRROR_ENV, "").strip()
    return Path(override).expanduser() if override else mirror_dir(home)


def _save(root: Path, ledger: trigger.Ledger) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = root / trigger.LEDGER_FILE
    tmp = target.with_suffix(".tmp")
    _ = tmp.write_text(trigger.dump_ledger(ledger), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(target)


def _load(root: Path) -> trigger.Ledger:
    target = root / trigger.LEDGER_FILE
    return trigger.load_ledger(target.read_text(encoding="utf-8")) if target.is_file() else {}


def run_once(
    env: Mapping[str, str], *, home: Path, enroll: Enroll = _enroll, notify: Notify = _notify,
) -> int:
    mirror = _mirror(env, home)
    if mirror is None or not mirror.is_dir():
        print("VOICE-CATALOG-SKIP reason=mirror-missing")
        return 0
    root = stt_catalog.catalog_root(env)
    ledger = _load(root)
    cli = _cli_path(env)
    for note in sorted((mirror / LIFELOG_DIR).rglob("*.md")):
        body = note.read_text(encoding="utf-8")
        assignments = trigger.parse_assignments(body)
        if not assignments:
            continue
        relpath = unicodedata.normalize("NFC", note.relative_to(mirror).as_posix())
        stem = unicodedata.normalize("NFC", note.stem)
        digest = trigger.fingerprint(body)
        entries = ledger.setdefault(relpath, {})
        for assignment in trigger.pending(assignments, entries, digest):
            transcript = home / TRANSCRIPT_DIR / f"{stem}.md"
            if not cli.is_file():
                outcome = EnrollOutcome(False, reason="ENROLL-CLI-MISSING")
            elif not transcript.is_file():
                outcome = EnrollOutcome(False, reason="TRANSCRIPT-MISSING")
            else:
                outcome = enroll(cli, transcript, assignment.label, assignment.name, env)
            at = datetime.now().astimezone().isoformat(timespec="seconds")
            entries[assignment.label] = trigger.Entry(
                assignment.name, "enrolled" if outcome.ok else "failed", digest, at, outcome.reason,
            )
            _save(root, ledger)
            if outcome.ok:
                print(f"VOICE-CATALOG-ENROLLED note={stem} label={assignment.label} name={assignment.name} "
                      f"seconds={outcome.seconds:.1f}")
                content = f"[voice-catalog] {assignment.name} 등록 — 노트 {stem} {assignment.label}, {outcome.seconds:.1f}초"
                message = trigger.enrolled_message(
                    name=assignment.name, label=assignment.label, note_stem=stem,
                    seconds=outcome.seconds, recording_id=outcome.recording_id,
                )
            else:
                print(f"VOICE-CATALOG-FAILED note={stem} label={assignment.label} name={assignment.name} "
                      f"reason={outcome.reason}")
                content = f"[voice-catalog] {assignment.name} 등록 실패 — 노트 {stem} {assignment.label}: {outcome.reason}"
                message = trigger.failed_message(
                    name=assignment.name, label=assignment.label, note_stem=stem, reason=outcome.reason,
                )
            if not notify(content, message):
                print(f"NOTIFY-FAIL note={stem} label={assignment.label}", file=sys.stderr)
    return 0
