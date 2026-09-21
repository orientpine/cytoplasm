"""Obsidian 트리거의 순수 절반 — 줄을 읽고, 원장과 대조하고, 봉투를 만든다. I/O 는 없다.

소유자가 라이프로그 노트에 `- 화자:: 화자2=김민수 · 화자3=이영희` 를 적은 것이 곧 등록 명령이자
동의다(2026-09-17 결정). 통지 홍수의 방벽도 여기 있다: 같은 이름이 이미 등록됐으면 무동작, 실패는
노트 본문이 바뀌기 전엔 되풀이하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from automation.interop.owner_message import Action, OwnerMessage, Ref, Result

SPEAKER_FIELD: Final = "- 화자::"
LEDGER_FILE: Final = "obsidian-enroll.json"
LEDGER_VERSION: Final = 1
UNKNOWN_NAMES: Final = frozenset({"", "미상"})
_LABEL: Final = re.compile(r"^화자[1-9]\d*$")
_SEPARATOR: Final = re.compile(r"\s*[·,;]\s*")

Status = Literal["enrolled", "failed"]


class TriggerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Assignment:
    label: str
    name: str


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    status: Status
    fingerprint: str
    at: str
    reason: str = ""


Ledger = dict[str, dict[str, Entry]]


def parse_assignments(body: str) -> tuple[Assignment, ...]:
    line = next((raw.strip() for raw in body.splitlines() if raw.strip().startswith(SPEAKER_FIELD)), None)
    if line is None:
        return ()
    found: dict[str, str] = {}
    for piece in _SEPARATOR.split(line[len(SPEAKER_FIELD):].strip()):
        label, equals, name = piece.partition("=")
        label, name = label.strip(), name.strip()
        if not equals or not _LABEL.match(label) or name in UNKNOWN_NAMES or label in found:
            continue
        found[label] = name
    return tuple(Assignment(label, name) for label, name in found.items())


def fingerprint(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def pending(
    assignments: Iterable[Assignment], entries: Mapping[str, Entry], note_fingerprint: str
) -> tuple[Assignment, ...]:
    chosen: list[Assignment] = []
    for assignment in assignments:
        entry = entries.get(assignment.label)
        if entry is None or entry.name != assignment.name:
            chosen.append(assignment)
        elif entry.status == "failed" and entry.fingerprint != note_fingerprint:
            chosen.append(assignment)
    return tuple(chosen)


def load_ledger(text: str) -> Ledger:
    if not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as failure:
        raise TriggerError(f"{LEDGER_FILE} 을 읽을 수 없습니다: {failure.msg}") from None
    version = payload.get("version") if isinstance(payload, dict) else None
    if version != LEDGER_VERSION:
        raise TriggerError(f"{LEDGER_FILE} version={version!r} 은 지원하지 않습니다 (기대 {LEDGER_VERSION})")
    try:
        return {
            str(note): {
                str(label): Entry(
                    str(entry["name"]), _status(entry["status"]), str(entry["fingerprint"]),
                    str(entry["at"]), str(entry.get("reason", "")),
                )
                for label, entry in labels.items()
            }
            for note, labels in payload["notes"].items()
        }
    except (KeyError, TypeError, AttributeError, ValueError) as failure:
        raise TriggerError(f"{LEDGER_FILE} 필드가 깨졌습니다: {type(failure).__name__}") from None


def _status(value: object) -> Status:
    if value == "enrolled":
        return "enrolled"
    if value == "failed":
        return "failed"
    raise ValueError(f"status={value!r}")


def dump_ledger(ledger: Ledger) -> str:
    payload = {
        "version": LEDGER_VERSION,
        "notes": {
            note: {
                label: {"name": entry.name, "status": entry.status, "fingerprint": entry.fingerprint,
                        "at": entry.at, "reason": entry.reason}
                for label, entry in labels.items()
            }
            for note, labels in ledger.items()
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


Recovery = Literal["not_applicable", "irreversible"] | Action


def _message(*, key: str, subject: str, fact: str, recovery: Recovery) -> OwnerMessage:
    return OwnerMessage(
        subject_key=key, subject=subject, fact=fact,
        location=Ref(scope="none"), owner=Action(verb="none"), agent_next=None,
        recovery=recovery, detail=Result(outcome="executed"),
    )


def enrolled_message(*, name: str, label: str, note_stem: str, seconds: float, recording_id: str) -> OwnerMessage:
    return _message(
        key=f"voice-catalog/enroll/{note_stem}/{label}",
        subject=f"목소리 등록 · {name}",
        fact=(f"노트 「{note_stem}」 의 {label} 를 {name} 으로 등록했다 — 등록본 {seconds:.1f}초, "
              f"녹음 {recording_id}"),
        recovery=Action(verb="reply", argument=f"`stt_catalog_cli.py remove --name {name}` 을 지시"),
    )


def failed_message(*, name: str, label: str, note_stem: str, reason: str) -> OwnerMessage:
    return _message(
        key=f"voice-catalog/enroll-failed/{note_stem}/{label}",
        subject=f"목소리 등록 실패 · {name}",
        fact=(f"노트 「{note_stem}」 의 {label} 를 {name} 으로 등록하지 못했다 — {reason}. "
              "노트의 `- 화자::` 줄을 고치면 다시 시도한다"),
        recovery="not_applicable",
    )
