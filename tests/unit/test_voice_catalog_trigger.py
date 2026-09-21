"""voice catalog ②b — Obsidian 트리거의 순수 절반.

소유자가 라이프로그 노트에 `- 화자:: 화자2=김민수 · 화자3=이영희` 를 적는다. 이 모듈은 그 줄을
읽고, 원장과 대조해 무엇을 등록할지 정하고, 통지 봉투를 만든다 — 파일도 네트워크도 만지지
않는다. 통지 홍수를 막는 규칙(같은 실패는 노트가 바뀌기 전엔 되풀이하지 않는다)이 여기 산다.
"""

from __future__ import annotations

import json

import pytest

from automation.interop.owner_message import Ref, render
from automation.voice_catalog import trigger


def test_parse_reads_the_first_speaker_field_line_and_skips_unknowns() -> None:
    body = "\n".join((
        "## 한눈에",
        "- 녹음:: 2026-09-16 (수) 11:15 · 20분 · 화자 4명",
        "- 화자:: 화자2=김민수 · 화자3 = 이영희, 화자1=미상; 화자0=누군가 · 화자4=",
        "- 사람:: [[김민수]]",
        "- 화자:: 화자9=무시된다",
    ))
    assert trigger.parse_assignments(body) == (
        trigger.Assignment("화자2", "김민수"),
        trigger.Assignment("화자3", "이영희"),
    )


def test_parse_answers_empty_without_the_field_and_keeps_the_first_label_only() -> None:
    assert trigger.parse_assignments("## 한눈에\n- 녹음:: x\n") == ()
    body = "- 화자:: 화자2=김민수 · 화자2=박철수"
    assert trigger.parse_assignments(body) == (trigger.Assignment("화자2", "김민수"),)


def test_pending_skips_enrolled_same_name_and_retries_failed_only_when_the_note_changed() -> None:
    same = trigger.fingerprint("body-a")
    changed = trigger.fingerprint("body-b")
    entries = {
        "화자1": trigger.Entry("김민수", "enrolled", same, "2026-09-17T10:00:00+09:00"),
        "화자2": trigger.Entry("이영희", "failed", same, "2026-09-17T10:00:00+09:00", "CATALOG-NO-SEGMENTS 화자2"),
        "화자3": trigger.Entry("박철수", "enrolled", same, "2026-09-17T10:00:00+09:00"),
    }
    assignments = (
        trigger.Assignment("화자1", "김민수"),
        trigger.Assignment("화자2", "이영희"),
        trigger.Assignment("화자3", "최영수"),
        trigger.Assignment("화자4", "새사람"),
    )
    assert trigger.pending(assignments, entries, same) == (
        trigger.Assignment("화자3", "최영수"),
        trigger.Assignment("화자4", "새사람"),
    )
    assert trigger.pending(assignments, entries, changed) == (
        trigger.Assignment("화자2", "이영희"),
        trigger.Assignment("화자3", "최영수"),
        trigger.Assignment("화자4", "새사람"),
    )
    assert trigger.pending((), entries, same) == ()


def test_ledger_round_trips_and_refuses_unknown_versions() -> None:
    ledger = {
        "000_PARA/Area/Lifelog/2026/a.md": {
            "화자2": trigger.Entry("김민수", "enrolled", "f" * 64, "2026-09-17T10:00:00+09:00"),
            "화자3": trigger.Entry("이영희", "failed", "e" * 64, "2026-09-17T10:00:00+09:00", "TRANSCRIPT-MISSING"),
        },
    }
    text = trigger.dump_ledger(ledger)
    assert json.loads(text)["version"] == trigger.LEDGER_VERSION
    assert trigger.load_ledger(text) == ledger
    assert trigger.load_ledger("") == {}
    with pytest.raises(trigger.TriggerError, match="version"):
        _ = trigger.load_ledger(json.dumps({"version": 7, "notes": {}}))


def test_messages_render_through_the_owner_envelope() -> None:
    destination = Ref(scope="channel", space="unknown", channel_id="1")
    done = trigger.enrolled_message(name="김민수", label="화자2", note_stem="2026-09-16_1115_자동_굴착",
                                    seconds=42.5, recording_id="of_abc")
    text = render(done, destination=destination)
    assert "김민수" in text and "화자2" in text and "42.5" in text and "2026-09-16_1115_자동_굴착" in text
    assert "remove --name 김민수" in text
    failed = trigger.failed_message(name="김민수", label="화자7", note_stem="2026-09-16_1115_자동_굴착",
                                    reason="CATALOG-NO-SEGMENTS 화자7")
    text = render(failed, destination=destination)
    assert "CATALOG-NO-SEGMENTS 화자7" in text and "화자7" in text
    assert done.subject_key != failed.subject_key
