"""윈도 전사 산술 — 한 구간의 사고가 전사본 전체를 잃게 만들지 않는다.

2026-09-04 사고(t_4e3d6630): 2시간 녹음의 whisper.cpp JSON 한복판에 깨진 UTF-8
바이트가 한 번 섞였고, `json.loads(payload.read_text())` 가 UnicodeDecodeError 로
터지며 2시간 분량의 전사가 통째로 사라졌다. 이 모듈은 그 구조를 끊는다: 녹음은
타임라인이고, 타임라인은 서로 독립적으로 실패하는 창(window)으로 자를 수 있다.

여기 있는 테스트는 순수 산술만 본다 — 프로세스도, 파일도, 시계도 쓰지 않는다.
"""

from __future__ import annotations

import json
import sys
from functools import partial
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_window  # noqa: E402


# --- 계획: 어느 밀리초도 어느 창에도 안 들어가는 일이 없어야 한다 ---------------


def test_a_recording_shorter_than_one_window_stays_a_single_pass() -> None:
    """짧은 녹음은 오늘과 같은 한 번의 전사 — 창을 쪼갤 이유가 없다."""
    windows = stt_window.plan_windows(600_000, window_ms=900_000, overlap_ms=15_000)
    assert windows == (stt_window.Window(index=0, start_ms=0, length_ms=600_000),)
    assert windows[0].end_ms == 600_000


def test_a_two_hour_recording_is_tiled_with_a_fixed_stride_to_the_very_end() -> None:
    windows = stt_window.plan_windows(7_200_000, window_ms=900_000, overlap_ms=15_000)
    stride = 900_000 - 15_000
    assert [window.start_ms for window in windows] == [
        index * stride for index in range(len(windows))
    ]
    assert [window.index for window in windows] == list(range(len(windows)))
    assert windows[-1].end_ms == 7_200_000
    assert all(window.length_ms > 0 for window in windows)
    # 이어붙인 창이 녹음 전체를 덮는다: 앞 창의 끝은 다음 창의 시작보다 뒤에 있다.
    assert all(
        earlier.end_ms >= later.start_ms
        for earlier, later in zip(windows, windows[1:], strict=False)
    )


def test_a_duration_that_ends_exactly_on_a_stride_boundary_makes_no_empty_window() -> None:
    windows = stt_window.plan_windows(240_000, window_ms=100_000, overlap_ms=20_000)
    assert [(window.start_ms, window.length_ms) for window in windows] == [
        (0, 100_000),
        (80_000, 100_000),
        (160_000, 80_000),
    ]
    assert windows[-1].end_ms == 240_000


def test_an_unknown_duration_plans_nothing_rather_than_guessing() -> None:
    assert stt_window.plan_windows(0) == ()
    assert stt_window.plan_windows(-1) == ()


# --- 디코딩: 어떤 바이트도 치명적이지 않다 -------------------------------------


def test_clean_utf8_decodes_without_any_replacement() -> None:
    text, replaced = stt_window.decode_payload('{"text":"회의"}'.encode())
    assert text == '{"text":"회의"}'
    assert replaced == 0


def test_malformed_utf8_is_decoded_losslessly_instead_of_raising() -> None:
    """사고의 그 바이트열 — 깨진 자리만 대체되고 나머지 글자는 그대로 남는다."""
    payload = json.dumps(
        {"transcription": [{"text": " 오늘 킥오프 회의입니다."}]}, ensure_ascii=False
    ).encode("utf-8")
    corrupted = payload.replace("킥오프".encode(), b"\xed\xa0")

    text, replaced = stt_window.decode_payload(corrupted)

    assert replaced > 0
    assert "오늘" in text and "회의입니다" in text
    assert json.loads(text)["transcription"][0]["text"].count("\ufffd") == replaced


def test_a_payload_that_already_contains_a_replacement_character_is_not_double_counted() -> None:
    text, replaced = stt_window.decode_payload("정상 \ufffd 텍스트".encode())
    assert replaced == 0
    assert text == "정상 \ufffd 텍스트"


# --- 병합: 이음매의 소유권은 산술로 정해진다 -----------------------------------


def _segment(start: int, end: int, text: str) -> dict[str, object]:
    return {"offsets": {"from": start, "to": end}, "text": text}


def test_a_sentence_spoken_inside_the_overlap_is_kept_by_exactly_one_window() -> None:
    """겹침 구간의 문장은 다음 창의 것 — 유사도 추정 없이 시작 시각만으로 정한다."""
    windows = stt_window.plan_windows(240_000, window_ms=100_000, overlap_ms=20_000)
    seam = " 이음매에서 말한 문장입니다."
    results = (
        stt_window.WindowResult(windows[0], (_segment(10_000, 20_000, " 첫 문장"), _segment(90_000, 95_000, seam))),
        stt_window.WindowResult(windows[1], (_segment(90_000, 95_000, seam), _segment(120_000, 125_000, " 둘째 문장"))),
        stt_window.WindowResult(windows[2], (_segment(200_000, 205_000, " 마지막 문장"),)),
    )

    merged = stt_window.merge(results)

    texts = [segment["text"] for segment in merged]
    assert texts.count(seam) == 1
    assert texts == [" 첫 문장", seam, " 둘째 문장", " 마지막 문장"]
    starts = [segment["offsets"]["from"] for segment in merged]
    assert starts == sorted(starts)


def test_the_last_window_keeps_everything_to_the_end_of_the_recording() -> None:
    windows = stt_window.plan_windows(240_000, window_ms=100_000, overlap_ms=20_000)
    results = (
        stt_window.WindowResult(windows[2], (_segment(239_000, 240_000, " 끝말"),)),
    )
    assert [segment["text"] for segment in stt_window.merge(results)] == [" 끝말"]


def test_window_relative_offsets_are_shifted_onto_the_global_timeline() -> None:
    """ffmpeg 로 잘라낸 창은 0 부터 세므로 창 시작만큼 밀어 준다(토큰까지)."""
    window = stt_window.Window(index=1, start_ms=105_000, length_ms=120_000)
    segment = {
        "offsets": {"from": 1_000, "to": 4_000},
        "tokens": [{"text": "안녕", "offsets": {"from": 1_000, "to": 2_000}}],
        "text": " 안녕하세요",
    }
    merged = stt_window.merge(
        (stt_window.WindowResult(window, (segment,), offset_ms=window.start_ms),)
    )
    assert merged[0]["offsets"] == {"from": 106_000, "to": 109_000}
    assert merged[0]["tokens"][0]["offsets"] == {"from": 106_000, "to": 107_000}
    # 입력은 건드리지 않는다 — 캐시에 남는 원본과 병합 결과가 갈라지면 안 된다.
    assert segment["offsets"] == {"from": 1_000, "to": 4_000}


def test_a_segment_without_offsets_belongs_to_the_window_that_produced_it() -> None:
    windows = stt_window.plan_windows(240_000, window_ms=100_000, overlap_ms=20_000)
    results = tuple(
        stt_window.WindowResult(window, ({"text": f" 구간 {window.index}"},)) for window in windows
    )
    assert [segment["text"] for segment in stt_window.merge(results)] == [
        " 구간 0",
        " 구간 1",
        " 구간 2",
    ]


# --- 표지: 잃은 구간은 전사본 안에서 눈에 보여야 한다 ---------------------------


def test_the_gap_marker_names_the_minutes_that_are_missing() -> None:
    marker = stt_window.gap_marker(
        stt_window.Window(index=3, start_ms=2_700_000, length_ms=900_000)
    )
    assert marker.startswith("[전사 실패 구간 00:45:00–01:00:00")
    assert "나머지는 그대로" in marker


def test_a_quarantined_window_becomes_one_visible_segment_on_the_timeline() -> None:
    window = stt_window.Window(index=1, start_ms=105_000, length_ms=120_000)
    result = stt_window.gap_result(window)
    assert result.window == window
    assert result.segments[0]["offsets"] == {"from": 105_000, "to": 225_000}
    assert result.segments[0]["text"] == stt_window.gap_marker(window)


def test_text_of_joins_the_segments_the_way_the_document_reads_them() -> None:
    assert stt_window.text_of(
        ({"text": " 첫 문장."}, {"text": "  둘째   문장. "}, {"nottext": 1})
    ) == "첫 문장. 둘째 문장."


# --- 캐시 키: 같은 오디오·모델·계획이면 같은 키, 하나만 달라도 다른 키 -----------


def test_the_cache_key_changes_when_the_plan_changes() -> None:
    short = stt_window.plan_windows(240_000, window_ms=100_000, overlap_ms=20_000)
    long = stt_window.plan_windows(240_000, window_ms=200_000, overlap_ms=20_000)
    key = partial(stt_window.cache_key, tool="whisper-build")
    same = key(audio_sha256="abc", model="ggml", windows=short)
    assert same == key(audio_sha256="abc", model="ggml", windows=short)
    assert same != key(audio_sha256="abc", model="ggml", windows=long)
    assert same != key(audio_sha256="def", model="ggml", windows=short)
    assert same != key(audio_sha256="abc", model="other", windows=short)


def test_spans_reads_only_the_offsets_a_segment_actually_reported() -> None:
    assert stt_window.spans(
        (_segment(0, 1_000, "가"), {"text": "나"}, {"offsets": {"from": "x"}, "text": "다"})
    ) == ((0, 1_000),)


# --- 저장소: 전사된 발화가 사는 곳 ---------------------------------------------

import stt_client  # noqa: E402
import stt_media  # noqa: E402
import stt_window_run  # noqa: E402
import stt_window_store  # noqa: E402


def _store(tmp_path: Path) -> stt_window_store.WindowStore:
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"audio-bytes")
    tool = tmp_path / "whisper-cli"
    tool.write_bytes(b"whisper-build")
    return stt_window_store.resolve_store(
        {"SPEECHTOTEXT_WINDOW_CACHE": str(tmp_path / "cache")},
        audio=audio,
        model=tmp_path / "ggml-large-v3-turbo-q5_0.bin",
        windows=stt_window.plan_windows(240_000, window_ms=100_000, overlap_ms=20_000),
        tool=tool,
    )


def test_the_window_cache_refuses_to_live_inside_a_git_checkout(tmp_path: Path) -> None:
    """전사된 발화는 저장소에 들어가면 안 된다 — 판단이 안 서면 시작하지 않는다."""
    (tmp_path / ".git").mkdir()
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"audio-bytes")
    try:
        stt_window_store.resolve_store(
            {"SPEECHTOTEXT_WINDOW_CACHE": str(tmp_path / "nested" / "cache")},
            audio=audio,
            model=tmp_path / "m.bin",
            windows=stt_window.plan_windows(60_000),
            tool=tmp_path / "whisper-cli",
        )
    except stt_client.SttError as refusal:
        assert "git 체크아웃" in str(refusal)
    else:
        raise AssertionError("a cache path inside a checkout must be refused")


def test_a_cached_window_is_written_owner_only_and_read_back_verbatim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    window = stt_window.Window(index=1, start_ms=80_000, length_ms=100_000)
    store.save(window, b'{"transcription": []}')

    assert store.load(window) == b'{"transcription": []}'
    assert store.payload(window).stat().st_mode & 0o777 == 0o600
    assert store.windows.stat().st_mode & 0o777 == 0o700
    store.clear()
    assert store.load(window) is None


def test_the_partial_transcript_is_kept_where_the_refusal_can_name_it(tmp_path: Path) -> None:
    kept = _store(tmp_path).preserve("여기까지 전사한 말입니다.")
    assert kept is not None
    assert kept.read_text(encoding="utf-8") == "여기까지 전사한 말입니다."
    assert kept.stat().st_mode & 0o777 == 0o600


def test_the_duration_of_a_file_that_is_not_a_wav_is_zero_not_a_guess(tmp_path: Path) -> None:
    broken = tmp_path / "input16k.wav"
    broken.write_bytes(b"RIFF")
    assert stt_media.wav_duration_ms(broken) == 0


def test_a_window_gets_a_budget_from_its_own_length_under_the_overall_ceiling() -> None:
    class _Toolchain:
        timeout = 14_400.0

    import time as _time

    deadline = _time.monotonic() + 14_400.0
    window = stt_window.Window(index=0, start_ms=0, length_ms=900_000)
    assert stt_window_run.budget(window, _Toolchain(), deadline) == 3_600.0
    # 길이를 못 읽어 한 번에 도는 경우에는 예전처럼 전체 상한을 그대로 쓴다.
    unbounded = stt_window.Window(index=0, start_ms=0, length_ms=0)
    assert stt_window_run.budget(unbounded, _Toolchain(), deadline) > 14_000.0


def test_the_gap_marker_ends_a_sentence_so_it_stays_its_own_line() -> None:
    """실측(노드 실행): 마침표가 없으면 다음 창의 첫 문장이 표지 뒤에 그대로 붙는다."""
    sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))
    import stt_blocks

    marker = stt_window.gap_marker(stt_window.Window(index=2, start_ms=210_000, length_ms=120_000))
    sentences = stt_blocks.split_sentences(f"{marker} 다음 창의 첫 문장입니다.")
    assert sentences == (marker, "다음 창의 첫 문장입니다.")
