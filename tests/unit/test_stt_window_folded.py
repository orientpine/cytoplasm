"""Folded-window regressions live separately from existing replay evidence files."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

SCRIPTS: Final = Path(__file__).resolve().parents[2] / "skills/speechtotext/scripts"
sys.path.insert(0, str(SCRIPTS))

import stt_blocks  # noqa: E402
import stt_client  # noqa: E402
import stt_diarize  # noqa: E402
import stt_local  # noqa: E402
import stt_polish  # noqa: E402
import stt_speaker_flow  # noqa: E402
import stt_window  # noqa: E402
import stt_window_run  # noqa: E402
import stt_window_store  # noqa: E402

REPEATED: Final = "같은 문장을 여러 번 다시 말하고 있습니다. " * 40
WINDOWS: Final = stt_window.plan_windows(240_000, window_ms=100_000, overlap_ms=20_000)
WindowRun = tuple[stt_window_run.WindowReport, stt_window_store.WindowStore]


def _toolchain(binary: Path) -> stt_local.LocalToolchain:
    return stt_local.LocalToolchain(
        binary=binary, model=binary, ffmpeg=binary, ffprobe=None, threads=1,
        language="ko", timeout=60, allow_incomplete=False, prompt="", repeat_limit=0.08,
        max_context="0", window_ms=100_000, overlap_ms=20_000,
    )


def _raw() -> bytes:
    return json.dumps({"transcription": [
        {"text": REPEATED, "offsets": {"from": 80_000, "to": 150_000}},
        {"text": "겹친 문장입니다.", "offsets": {"from": 170_000, "to": 180_000}},
    ]}, ensure_ascii=False).encode()


def test_accept_keeps_segments_when_repetition_exceeds_limit(tmp_path: Path) -> None:
    # Given
    raw = _raw()
    # When
    kept, reason = stt_window_run.accept(raw, WINDOWS[1], _toolchain(tmp_path / "whisper"))
    # Then
    assert kept == tuple(json.loads(raw)["transcription"])
    assert reason.startswith("repetition=")


def test_accept_keeps_lifelog_unfolded_when_incomplete_is_allowed(tmp_path: Path) -> None:
    # Given
    toolchain = replace(_toolchain(tmp_path / "whisper"), allow_incomplete=True)
    # When
    kept, reason = stt_window_run.accept(_raw(), WINDOWS[1], toolchain)
    # Then
    assert kept == tuple(json.loads(_raw())["transcription"])
    assert reason == ""


@pytest.mark.parametrize("raw,reason", [(b"{", "invalid-json"), (b"{}", "no-transcription")])
def test_accept_drops_payload_when_it_cannot_be_parsed(
    tmp_path: Path, raw: bytes, reason: str,
) -> None:
    # Given
    toolchain = _toolchain(tmp_path / "whisper")
    # When
    result = stt_window_run.accept(raw, WINDOWS[1], toolchain)
    # Then
    assert result == (None, reason)


@pytest.fixture
def window_run(tmp_path: Path) -> WindowRun:
    binary = tmp_path / "whisper"
    raw = _raw().decode()
    binary.write_text(
        '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n'
        'case "$1" in -of) out="$2"; shift;; -ot) offset="$2"; shift;; esac\n'
        'shift\ndone\ncase "$offset" in\n80000)\n'
        f"printf '%s' '{raw}' > \"$out.json\";;\n"
        '*) printf \'{"transcription":[{"text":"정상 창 발화.",'
        '"offsets":{"from":%s,"to":%s}}]}\' "$offset" "$((offset + 80000))" '
        '> "$out.json";;\nesac\n', encoding="utf-8",
    )
    binary.chmod(0o700)
    store = stt_window_store.WindowStore(tmp_path / "cache", "fixture")
    report = stt_window_run.run_windows(
        tmp_path / "audio.wav", WINDOWS, _toolchain(binary),
        workdir=tmp_path, prompt="", store=store,
    )
    return report, store


def test_merge_folds_only_owned_sentences_when_window_repeats(window_run: WindowRun) -> None:
    # Given: the real runner executed a fake whisper process for each window.
    report, _store = window_run
    # When
    merged = stt_window.merge(report.results)
    sentences = stt_blocks.sentences_from_words(stt_blocks.words_from_whisper(list(merged)))
    body = stt_polish.polish_sentences(sentences).body
    # Then
    fold = body[body.index("<details>"):body.index("</details>") + len("</details>")]
    assert "<summary>" in fold and "repetition=" in fold
    assert fold.count(REPEATED.split(". ")[0] + ".") == 40
    assert "겹친 문장입니다." not in fold
    assert body.index(stt_window.gap_marker(WINDOWS[1], until=160_000)) < body.index(fold)
    assert body.index(fold) < body.rindex("정상 창 발화.")


def test_tidy_preserves_fold_when_speakers_are_assigned(window_run: WindowRun) -> None:
    # Given
    report, _store = window_run
    merged = stt_window.merge(report.results)
    sentences = stt_blocks.sentences_from_words(stt_blocks.words_from_whisper(list(merged)))
    before = stt_polish.polish_sentences(sentences).body
    turns = tuple(stt_diarize.Turn(i, i + 10_000, i % 3) for i in range(0, 240_000, 10_000))
    # When
    assigned = stt_diarize.assign(sentences, turns)
    polished, _speakers = stt_speaker_flow.tidy(stt_client.Transcription(
        text=stt_window.text_of(merged), model="fixture", endpoint="local", sentences=assigned,
    ))
    after = polished.body
    # Then
    assert "<details>" in before
    fold = before[before.index("<details>"):before.index("</details>") + len("</details>")]
    assert fold in after
    assert fold in stt_polish.polish(after).body


def test_quarantine_retains_raw_when_window_is_folded(window_run: WindowRun) -> None:
    # Given
    report, store = window_run
    # When
    raw = (store.quarantine_dir / "window-00001.json").read_bytes()
    # Then
    assert raw == _raw()
    assert report.quarantined == (1,)


def test_runner_keeps_marker_only_when_redecode_of_repeated_cache_fails(tmp_path: Path) -> None:
    # Given: a lifelog cache can contain repetition that a meeting cannot trust.
    binary = tmp_path / "whisper"
    binary.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    binary.chmod(0o700)
    store = stt_window_store.WindowStore(tmp_path / "cache", "fixture")
    store.save(WINDOWS[1], _raw())
    # When
    report = stt_window_run.run_windows(
        tmp_path / "audio.wav", WINDOWS, _toolchain(binary),
        workdir=tmp_path, prompt="", store=store,
    )
    # Then
    assert report.results[1] == stt_window.gap_result(WINDOWS[1], until=160_000)
