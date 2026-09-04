from __future__ import annotations

from dataclasses import dataclass

from skills.speechtotext.scripts import stt_speakers


@dataclass(frozen=True, slots=True)
class Sentence:
    text: str
    start_ms: int | None = None
    speaker: str = ""


def test_infer_finds_introduction_and_preserves_first_appearance_order() -> None:
    speakers = stt_speakers.infer(
        (
            Sentence("안녕하세요.", speaker="화자1"),
            Sentence("저는 한국전력기술 김민수입니다.", 192_000, "화자2"),
            Sentence("반갑습니다.", speaker="화자1"),
        )
    )

    assert speakers == (
        stt_speakers.SpeakerName("화자1", "", ""),
        stt_speakers.SpeakerName("화자2", "김민수", "자기소개 00:03:12"),
    )


def test_infer_handles_title_form_and_rejects_stoplist_candidate() -> None:
    speakers = stt_speakers.infer(
        (
            Sentence("이영희 책임입니다", speaker="화자1"),
            Sentence("저는 담당입니다", speaker="화자2"),
        )
    )

    assert stt_speakers.names(speakers) == {"화자1": "이영희"}


def test_infer_uses_earliest_candidate_and_removes_duplicate_claims() -> None:
    speakers = stt_speakers.infer(
        (
            Sentence("저는 김민수입니다. 저는 이영희입니다.", speaker="화자1"),
            Sentence("제가 김민수입니다.", speaker="화자2"),
        )
    )

    assert speakers == (
        stt_speakers.SpeakerName("화자1", "", ""),
        stt_speakers.SpeakerName("화자2", "", ""),
    )


def test_infer_prefers_known_name() -> None:
    speakers = stt_speakers.infer(
        (Sentence("저는 김민수입니다. 저는 이영희입니다.", speaker="화자1"),),
        known_names=("이영희",),
    )

    assert stt_speakers.names(speakers) == {"화자1": "이영희"}


def test_legend_round_trip_preserves_unknown_and_conflict_source() -> None:
    speakers = (
        stt_speakers.SpeakerName("화자1", "김민수", "자기소개 00:03:12"),
        stt_speakers.SpeakerName(
            "화자2", "이영희", "자기소개 00:04:00 · LLM 제안: 박철수"
        ),
        stt_speakers.SpeakerName("화자3", "", ""),
    )

    legend = stt_speakers.render_legend(speakers)

    assert legend == (
        "- 화자: 화자1=김민수 [자기소개 00:03:12] · "
        "화자2=이영희 [자기소개 00:04:00 · LLM 제안: 박철수] · 화자3=미상"
    )
    assert stt_speakers.parse_legend("# 녹취\n" + legend + "\n---\n") == speakers
    assert stt_speakers.parse_legend("# 녹취\n---\n") == ()


def test_parse_override_accepts_all_separators_and_ignores_malformed_entries() -> None:
    assert stt_speakers.parse_override(
        "화자1=김민수 · 화자2=이영희;화자3=박철수, malformed,화자4="
    ) == (
        stt_speakers.SpeakerName("화자1", "김민수", "소유자"),
        stt_speakers.SpeakerName("화자2", "이영희", "소유자"),
        stt_speakers.SpeakerName("화자3", "박철수", "소유자"),
    )


def test_parse_llm_filters_invalid_labels_and_empty_names() -> None:
    assert stt_speakers.parse_llm(
        (
            {"label": "화자1", "name": " 김민수 ", "basis": "소개"},
            {"label": "speaker_02", "name": "이영희", "basis": "소개"},
            {"label": "화자3", "name": None, "basis": "없음"},
        )
    ) == (
        stt_speakers.SpeakerName("화자1", "김민수", "LLM"),
        stt_speakers.SpeakerName("화자3", "", "LLM"),
    )


def test_merge_applies_precedence_conflict_agreement_and_empty_rules() -> None:
    llm = (
        stt_speakers.SpeakerName("화자1", "박철수", "LLM"),
        stt_speakers.SpeakerName("화자2", "이영희", "LLM"),
        stt_speakers.SpeakerName("화자3", "최민수", "LLM"),
        stt_speakers.SpeakerName("화자4", "한가람", "LLM"),
    )
    rule = (
        stt_speakers.SpeakerName("화자1", "김민수", "자기소개 00:03:12"),
        stt_speakers.SpeakerName("화자2", "이영희", "자기소개"),
        stt_speakers.SpeakerName("화자3", "", ""),
    )
    owner = (stt_speakers.SpeakerName("화자1", "오세진", "소유자"),)

    assert stt_speakers.merge(llm, rule, owner) == (
        stt_speakers.SpeakerName("화자1", "오세진", "소유자"),
        stt_speakers.SpeakerName("화자2", "이영희", "자기소개 · LLM"),
        stt_speakers.SpeakerName(
            "화자3", "최민수", "LLM"
        ),
        stt_speakers.SpeakerName("화자4", "한가람", "LLM"),
    )


def test_merge_keeps_rule_and_records_conflicting_llm_suggestion() -> None:
    assert stt_speakers.merge(
        (stt_speakers.SpeakerName("화자1", "박철수", "LLM"),),
        (stt_speakers.SpeakerName("화자1", "김민수", "자기소개 00:03:12"),),
    ) == (
        stt_speakers.SpeakerName(
            "화자1", "김민수", "자기소개 00:03:12 · LLM 제안: 박철수"
        ),
    )
