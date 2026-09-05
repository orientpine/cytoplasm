"""전사 실패 표지는 완성된 문서에까지 통째로 도착해야 한다.

2026-09-04, 61분 실제 녹음(3,669,987 ms · 창 900초/겹침 15초)을 노드에서 그대로 다시
전사한 결과: 창 1 이 반복 붕괴로 격리됐고 stderr 는 `WHISPER-WINDOW-QUARANTINED index=1`
한 줄을 남겼는데, 정작 문서에서 `전사 실패 구간` 은 **0번** 나왔다. 남아 있던 것은
`[전사` 한 조각뿐이었고, 표지의 나머지 열 낱말은 00:23:47 부터 00:28:52 까지 흩어져
저마다 다른 화자의 블록이 되어 있었다.

원인은 병합이 아니었다(창 1 의 소유 구간 [885s, 1770s) 에서 시작하는 세그먼트는 표지
하나뿐이었다 — 캐시된 실제 페이로드로 재계산해 확인). 표지가 **문장으로 취급**된 것이
원인이다: 앞 창의 마지막 문장이 구두점 없이 끝나 표지가 거기에 이어 붙었고, 그렇게
916초짜리 한 문장이 된 뒤 화자 경계 분할기가 그 문장을 41 조각으로 잘랐다.

그래서 이 파일은 `merge()` 를 따로 부르지 않는다. `stt_local.transcribe` →
`stt_speaker_flow.tidy` → `stt_transcript.write_transcript` — 문서를 실제로 만드는 그
경로를 그대로 태우고, 완성된 .md 안에서 표지 한 줄을 글자 그대로 찾는다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Final

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_audio  # noqa: E402
import stt_blocks  # noqa: E402
import stt_diarize  # noqa: E402
import stt_gap  # noqa: E402
import stt_local  # noqa: E402
import stt_polish  # noqa: E402
import stt_speaker_flow  # noqa: E402
import stt_split  # noqa: E402
import stt_transcript  # noqa: E402
import stt_window  # noqa: E402

#: 창 계획: 330초 녹음 · 창 120초 · 겹침 15초 → [0,120) [105,225) [210,330).
#: 창 1 이 실패하면 그 창이 **소유한** 분은 00:01:45–00:03:30 이다(겹친 15초는 창 2 몫).
MARKER: Final = "[전사 실패 구간 00:01:45–00:03:30 — 이 구간만 비어 있고 나머지는 그대로입니다]."
OWNED: Final = (105_000, 210_000)

_FAKE_FFMPEG_WAV: Final = '''#!/usr/bin/env python3
"""ffmpeg stand-in: a real 16 kHz mono wav whose length the window planner can read."""
import os
import sys
import wave

seconds = float(os.environ.get("FAKE_WAV_SECONDS", "330"))
with wave.open(sys.argv[-1], "wb") as handle:
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(16000)
    handle.writeframes(b"\\0" * int(16000 * 2 * seconds))
'''

_FAKE_WHISPER: Final = '''#!/usr/bin/env python3
"""whisper-cli stand-in: 15-second segments per window, as the real decoder reports them.

`-ot` makes whisper.cpp report the recording's own offsets, so the segments written
here are absolute. One segment start may be listed in FAKE_WHISPER_UNTERMINATED: that
one ends without punctuation, which is how the real 61-minute recording ended the
window that came just before the quarantined one.
"""
import json
import os
import sys

argv = sys.argv[1:]


def flag(name, fallback=""):
    return argv[argv.index(name) + 1] if name in argv else fallback


offset = int(flag("-ot", "0"))
duration = int(flag("-d", "0"))
out = flag("-of")
with open(os.environ["FAKE_WHISPER_PLAN"], encoding="utf-8") as handle:
    plan = json.load(handle)
behaviour = plan.get(str(offset), plan.get("default", "ok"))
if behaviour == "rc":
    sys.stderr.write("fake whisper refused window %d\\n" % offset)
    raise SystemExit(3)
unterminated = {
    int(item) for item in os.environ.get("FAKE_WHISPER_UNTERMINATED", "").split(",") if item
}
end = offset + duration if duration else int(float(os.environ["FAKE_WAV_SECONDS"]) * 1000)
segments = []
cursor = offset
while cursor < end:
    stop = min(cursor + 15_000, end)
    text = (
        " 쭈 욱 김밥 이 하 늘 에서 내려 온 다 ~"
        if cursor in unterminated
        else " 구간 %d 발화입니다." % cursor
    )
    segments.append({"offsets": {"from": cursor, "to": stop}, "text": text})
    cursor = stop
with open(out + ".json", "w", encoding="utf-8") as handle:
    json.dump({"transcription": segments}, handle, ensure_ascii=False)
'''

_FAKE_DIARIZER: Final = '''#!/usr/bin/env python3
"""sherpa-onnx stand-in: a speaker turn every ten seconds, cycling four voices."""
import os

seconds = float(os.environ.get("FAKE_WAV_SECONDS", "330"))
start = 0.0
index = 0
while start < seconds:
    stop = min(start + 10.0, seconds)
    print("%.3f -- %.3f speaker_%d" % (start, stop, index % 4))
    start, index = stop, index + 1
'''


def _executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def windowed_run(tmp_path: Path, monkeypatch) -> Path:
    """A 5.5-minute recording in three windows, the middle one refusing to decode."""
    binary = _executable(tmp_path / "whisper-cli", _FAKE_WHISPER)
    ffmpeg = _executable(tmp_path / "ffmpeg", _FAKE_FFMPEG_WAV)
    ffprobe = _executable(
        tmp_path / "ffprobe", '#!/bin/sh\nprintf \'{"format":{"duration":"330.0"}}\'\n'
    )
    diarizer = _executable(tmp_path / "sherpa-onnx-offline-speaker-diarization", _FAKE_DIARIZER)
    model = tmp_path / "ggml-large-v3-turbo-q5_0.bin"
    model.write_bytes(b"ggml-model-fixture")
    plan = tmp_path / "whisper-plan.json"
    plan.write_text(json.dumps({"105000": "rc"}), encoding="utf-8")
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_BIN", str(binary))
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_MODEL", str(model))
    monkeypatch.setenv("SPEECHTOTEXT_FFMPEG_BIN", str(ffmpeg))
    monkeypatch.setenv("SPEECHTOTEXT_FFPROBE_BIN", str(ffprobe))
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_MS", "120000")
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_OVERLAP_MS", "15000")
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(tmp_path / "window-cache"))
    monkeypatch.setenv("SPEECHTOTEXT_DIARIZE_BIN", str(diarizer))
    monkeypatch.setenv("SPEECHTOTEXT_DIARIZE_SEGMENTATION", str(model))
    monkeypatch.setenv("SPEECHTOTEXT_DIARIZE_EMBEDDING", str(model))
    monkeypatch.setenv("FAKE_WHISPER_PLAN", str(plan))
    monkeypatch.setenv("FAKE_WAV_SECONDS", "330")
    # 격리된 창 바로 앞 세그먼트는 구두점 없이 끝난다 — 실제 녹음이 그랬다.
    monkeypatch.setenv("FAKE_WHISPER_UNTERMINATED", "90000")
    monkeypatch.delenv("SPEECHTOTEXT_ALLOW_INCOMPLETE", raising=False)
    audio = tmp_path / "회의.m4a"
    audio.write_bytes(b"long-audio-fixture")
    return audio


def _document(audio: Path) -> str:
    """The finished .md, built the way the CLI builds it — transcribe, tidy, write."""
    toolchain = stt_local.resolve_toolchain(dict(os.environ))
    assert toolchain is not None
    diarizer = stt_diarize.resolve_toolchain(dict(os.environ))
    assert diarizer is not None
    transcription = stt_local.transcribe(
        stt_audio.check_audio(audio), toolchain, diarizer=diarizer
    )
    tidied, speakers = stt_speaker_flow.tidy(transcription)
    written = stt_transcript.write_transcript(
        audio.parent / "transcripts",
        label="구간실패",
        source_name=audio.name,
        transcription=transcription,
        now=datetime(2026, 9, 4, 22, 32),
        polish=tidied,
        extra_lines=stt_speaker_flow.legend_lines(speakers),
    )
    return written.read_text(encoding="utf-8")


def _blocks(document: str) -> tuple[tuple[str, ...], ...]:
    body = stt_polish.split_document(document)[1]
    return tuple(
        tuple(line for line in chunk.splitlines() if line.strip())
        for chunk in re.split(r"\n\s*\n", body)
        if chunk.strip()
    )


def _stamp_ms(block: tuple[str, ...]) -> int:
    matched = stt_blocks.HEADER.match(block[0])
    if matched is None or not matched.group(1).isdigit():
        return -1
    return (
        int(matched.group(1)) * 3_600 + int(matched.group(2)) * 60 + int(matched.group(3))
    ) * 1_000


def _fragments(document: str) -> str:
    """What the document actually kept of the marker — the 2026-09-04 failure was `[전사`."""
    return f"문서에 남은 표지 조각: {re.findall(r'.{0,12}전사 ?실패.{0,20}', document)}"


def test_the_gap_marker_reaches_the_finished_document_intact(windowed_run: Path) -> None:
    """소유자가 읽는 건 문서다 — 표지는 글자 그대로, 한 번, 자기 블록으로 도착해야 한다."""
    document = _document(windowed_run)

    assert document.count(MARKER) == 1, _fragments(document)
    carrying = [block for block in _blocks(document) if any(MARKER in line for line in block)]
    assert len(carrying) == 1, _fragments(document)
    # 헤더 한 줄 + 표지 한 줄. 다른 발화가 같은 블록에 섞이면 표지는 문단 속에 묻힌다.
    assert carrying[0] == ("[00:01:45]", MARKER)
    # 표지는 잃은 창만 대신한다 — 앞뒤 창의 말은 그대로 문서에 있다(분할기가 화자
    # 경계에서 조각내는 것은 발화에 대해서는 정상 동작이다).
    assert "구간 0" in document
    assert "210000" in document and "315000" in document


def test_the_quarantined_windows_minutes_carry_the_marker_and_nothing_else(
    windowed_run: Path,
) -> None:
    """격리된 창이 소유한 분에는 표지 말고 아무 블록도 없어야 한다.

    실제 사고 문서에서는 이 구간에 41개 블록이 있었다. 그중 열 블록은 표지의 조각이었고,
    나머지는 앞 창이 이미 전사한 낱말이 916초짜리 문장의 선형 보간을 타고 뒤로 밀린
    것이었다 — 둘 다 표지를 문장에 붙인 데서 나온 같은 원인이다.
    """
    document = _document(windowed_run)

    inside = [block for block in _blocks(document) if OWNED[0] <= _stamp_ms(block) < OWNED[1]]
    assert [block[1:] for block in inside] == [(MARKER,)]
    assert inside[0][0] == "[00:01:45]"


def test_a_marker_is_never_cut_at_a_speaker_boundary() -> None:
    """화자 경계 분할기가 표지를 자르면 소유자가 읽는 건 `[전사` 한 조각이다."""
    marker = stt_gap.marker(885_000, 1_770_000)
    said = stt_blocks.TimedSentence(marker, 885_000, 1_770_000)
    turns = tuple(
        stt_diarize.Turn(start, start + 10_000, index % 4)
        for index, start in enumerate(range(885_000, 1_770_000, 10_000))
    )

    assert stt_split.split_on_turns((said,), turns) == (said,)
    assert stt_diarize.assign((said,), turns) == (said,)
    # 표지에는 화자가 붙지 않는다 — 아무도 하지 않은 말이다.
    assert stt_diarize.assign((said,), turns)[0].speaker == ""


def test_a_marker_stays_its_own_block_even_when_the_words_around_it_never_end(
) -> None:
    """앞 창이 구두점 없이 끝나도 표지는 그 문장에 붙지 않는다(사고의 그 자리)."""
    marker = stt_gap.marker(105_000, 210_000)
    words = (
        stt_blocks.TimedWord(" 쭈 욱 김밥 이 하 늘 에서 내려 온 다 ~", 90_000, 105_000),
        stt_blocks.TimedWord(marker, 105_000, 210_000),
        stt_blocks.TimedWord(" 다음 창의 첫 문장입니다.", 210_000, 214_000),
    )

    sentences = stt_blocks.sentences_from_words(words)

    assert [sentence.text for sentence in sentences] == [
        "쭈 욱 김밥 이 하 늘 에서 내려 온 다 ~",
        marker,
        "다음 창의 첫 문장입니다.",
    ]
    assert (sentences[1].start_ms, sentences[1].end_ms) == (105_000, 210_000)
    # 화자 배정이 없는 경로(진단기 없음)에서도 표지는 자기 블록을 갖는다.
    body = stt_polish.polish_sentences(sentences).body
    assert f"[00:01:45]\n{marker}" in body


def test_tidying_never_rewrites_the_marker() -> None:
    """표지는 들린 말이 아니다 — 다듬기가 그 줄을 건드리면 어느 분이 비었는지가 조용히 바뀐다."""
    marker = stt_gap.marker(105_000, 210_000)
    polished = stt_polish.polish_sentences(
        (stt_blocks.TimedSentence(marker, 105_000, 210_000),)
    )
    assert marker in polished.body


def _segment(start: int, end: int, text: str) -> dict[str, object]:
    return {"offsets": {"from": start, "to": end}, "text": text}


def test_merge_gives_the_quarantined_windows_minutes_to_the_marker_alone() -> None:
    """실측(2026-09-04, 61분 녹음)의 산술 그대로 — 소유권은 시작 시각 하나로 정해진다.

    창 0 은 901,340 ms 까지 전사했지만 소유는 885,000 ms 에서 끝난다. 이음매를 걸친
    세그먼트는 시작이 885,000 보다 앞이므로 창 0 이 그대로 가져가고, 885,000 이후에서
    시작하는 창 0 의 세그먼트는 버려진다. 그래서 격리된 창의 구간에서 시작하는 것은
    표지 하나뿐이다.
    """
    windows = stt_window.plan_windows(3_669_987, window_ms=900_000, overlap_ms=15_000)
    assert [window.start_ms for window in windows] == [0, 885_000, 1_770_000, 2_655_000, 3_540_000]
    first = stt_window.WindowResult(
        windows[0],
        (
            _segment(870_000, 884_000, " 앞 창의 말입니다."),
            _segment(884_000, 901_340, " 이음매를 걸친 말입니다."),
            _segment(890_000, 900_000, " 창 1 의 몫이라 버려지는 말입니다."),
        ),
    )
    third = stt_window.WindowResult(windows[2], (_segment(1_770_000, 1_780_000, " 창 2 의 말."),))

    merged = stt_window.merge(
        (first, stt_window.gap_result(windows[1], until=windows[2].start_ms), third)
    )

    inside = [
        segment for segment in merged if 885_000 <= int(segment["offsets"]["from"]) < 1_770_000
    ]
    assert [segment["text"] for segment in inside] == [stt_gap.marker(885_000, 1_770_000)]
    assert inside[0]["offsets"] == {"from": 885_000, "to": 1_770_000}
    assert " 이음매를 걸친 말입니다." in [segment["text"] for segment in merged]


def test_the_marker_names_only_the_minutes_no_window_transcribed() -> None:
    """겹친 15초는 다음 창이 전사한다 — 표지가 그 15초까지 부르면 없는 누락을 부른다."""
    windows = stt_window.plan_windows(330_000, window_ms=120_000, overlap_ms=15_000)
    result = stt_window.gap_result(windows[1], until=windows[2].start_ms)
    assert result.segments[0]["text"] == MARKER
    assert result.segments[0]["offsets"] == {"from": 105_000, "to": 210_000}
    # 마지막 창에는 다음 창이 없으므로 자기 끝까지 부른다.
    last = stt_window.gap_result(windows[2], until=None)
    assert last.segments[0]["offsets"] == {"from": 210_000, "to": 330_000}
