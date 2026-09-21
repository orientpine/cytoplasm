"""voice catalog ② — 등록 저장소의 순수 절반.

전사본 블록 헤더는 시작 시각만 갖는다. 한 화자의 구간은 그 블록의 시작부터 다음 블록의
시작까지이고, 마지막 블록은 오디오 길이를 알 때만 구간이 된다. 이 모듈은 I/O 를 하지
않는다 — 어느 구간을 자를지, 어떤 ffmpeg 필터를 만들지, 카탈로그 JSON 이 어떻게 생겼는지만
정한다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_blocks  # noqa: E402
import stt_catalog  # noqa: E402


@dataclass(frozen=True, slots=True)
class _Said:
    text: str
    start_ms: int | None
    speaker: str


def _sentences() -> tuple[_Said, ...]:
    return (
        _Said("첫 블록.", 0, "화자1"),
        _Said("이어지는 문장.", None, "화자1"),
        _Said("두 번째 블록.", 10_000, "화자2"),
        _Said("세 번째 블록.", 14_000, "화자1"),
        _Said("네 번째.", 50_000, "화자2"),
        _Said("마지막 블록.", 52_000, "화자1"),
    )


def test_block_starts_keep_only_headed_sentences_in_order() -> None:
    assert stt_catalog.block_starts(_sentences()) == (
        ("화자1", 0), ("화자2", 10_000), ("화자1", 14_000), ("화자2", 50_000), ("화자1", 52_000),
    )


def test_plan_segments_uses_next_block_start_as_end_and_drops_the_tail_without_duration() -> None:
    planned = stt_catalog.plan_segments(_sentences(), "화자1", per_block_max_ms=60_000)
    assert planned == (
        stt_catalog.Interval(0, 10_000),
        stt_catalog.Interval(14_000, 50_000),
    )


def test_plan_segments_closes_the_last_block_with_the_audio_duration() -> None:
    planned = stt_catalog.plan_segments(_sentences(), "화자1", duration_ms=60_000)
    assert planned[-1] == stt_catalog.Interval(52_000, 60_000)


def test_plan_segments_prefers_long_blocks_then_restores_time_order() -> None:
    said = (
        _Said("a", 0, "화자1"), _Said("b", 2_000, "화자2"),
        _Said("c", 3_000, "화자1"), _Said("d", 30_000, "화자2"),
        _Said("e", 31_000, "화자1"), _Said("f", 36_000, "화자2"),
    )
    planned = stt_catalog.plan_segments(said, "화자1", total_max_ms=30_000, per_block_max_ms=30_000)
    assert planned == (stt_catalog.Interval(3_000, 30_000), stt_catalog.Interval(31_000, 34_000))


def test_plan_segments_caps_each_block_and_the_total() -> None:
    said = (_Said("a", 0, "화자1"), _Said("b", 100_000, "화자2"))
    planned = stt_catalog.plan_segments(said, "화자1", per_block_max_ms=30_000, total_max_ms=20_000)
    assert planned == (stt_catalog.Interval(0, 20_000),)


def test_plan_segments_skips_fragments_and_answers_empty_for_an_absent_label() -> None:
    said = (_Said("a", 0, "화자1"), _Said("b", 1_000, "화자2"), _Said("c", 2_000, "화자1"))
    assert stt_catalog.plan_segments(said, "화자1", min_block_ms=1_500) == ()
    assert stt_catalog.plan_segments(_sentences(), "화자9") == ()
    assert stt_catalog.plan_segments(_sentences(), "화자0") == ()


def test_plan_segments_reads_a_real_transcript_body() -> None:
    body = "\n".join((
        "[00:00:00] 화자1", "안녕하세요. 오늘 안건입니다.", "",
        "[00:00:20] 화자2", "네 시작하죠.", "",
        "[00:00:25] 화자1", "첫 번째는 일정입니다.", "",
        "[00:01:05] 화자0 · UNKNOWN", "웅얼.", "",
    ))
    planned = stt_catalog.plan_segments(stt_blocks.parse(body), "화자1", per_block_max_ms=60_000)
    assert planned == (stt_catalog.Interval(0, 20_000), stt_catalog.Interval(25_000, 65_000))


def test_speaker_summary_counts_blocks_and_speech_per_label() -> None:
    summary = stt_catalog.speaker_summary(_sentences(), duration_ms=60_000)
    assert summary == {
        "화자1": stt_catalog.SpeakerStat(blocks=3, speech_ms=54_000),
        "화자2": stt_catalog.SpeakerStat(blocks=2, speech_ms=6_000),
    }


def test_catalog_root_prefers_env_and_refuses_a_git_checkout(tmp_path: Path) -> None:
    outside = tmp_path / "state"
    assert stt_catalog.catalog_root({"SPEECHTOTEXT_VOICE_CATALOG": str(outside)}) == outside
    assert stt_catalog.catalog_root({}) == Path("~/.hermes/speechtotext/voice-catalog").expanduser()
    inside = tmp_path / "repo" / "voice"
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(stt_catalog.CatalogError, match="CATALOG-ROOT-REFUSED"):
        _ = stt_catalog.catalog_root({"SPEECHTOTEXT_VOICE_CATALOG": str(inside)})


def _sample(recording: str = "abcd1234", label: str = "화자1", sha: str = "f" * 64) -> stt_catalog.Sample:
    return stt_catalog.Sample(
        wav=f"samples/x-{recording}-{label}.wav", sha256=sha, seconds=12.5,
        recording_id=recording, transcript_stem="2026-09-07-150643--13b66f70f19d",
        speaker_label=label, intervals=(stt_catalog.Interval(0, 12_500),),
    )


def test_upsert_is_idempotent_per_recording_and_label_and_keeps_other_samples() -> None:
    empty = stt_catalog.Catalog()
    one = stt_catalog.upsert(empty, "홍길동", _sample(), created_at="2026-09-17T00:00:00+09:00")
    again = stt_catalog.upsert(one, "홍길동", _sample(sha="e" * 64), created_at="2026-09-18T00:00:00+09:00")
    other = stt_catalog.upsert(again, "홍길동", _sample(recording="ffff0000"), created_at="2026-09-18T00:00:00+09:00")
    assert [person.name for person in other.people] == ["홍길동"]
    person = other.people[0]
    assert person.created_at == "2026-09-17T00:00:00+09:00"
    assert [sample.sha256[0] for sample in person.samples] == ["e", "f"]
    assert stt_catalog.remove(other, "홍길동").people == ()
    assert stt_catalog.remove(other, "없는사람") == other


def test_catalog_json_round_trips_and_refuses_unknown_versions() -> None:
    catalog = stt_catalog.upsert(stt_catalog.Catalog(), "홍길동", _sample(), created_at="2026-09-17T00:00:00+09:00")
    text = stt_catalog.to_json(catalog)
    assert json.loads(text)["version"] == stt_catalog.SCHEMA_VERSION
    assert stt_catalog.from_json(text) == catalog
    assert stt_catalog.from_json("") == stt_catalog.Catalog()
    with pytest.raises(stt_catalog.CatalogError, match="version"):
        _ = stt_catalog.from_json(json.dumps({"version": 99, "people": []}))


def test_ffmpeg_filter_trims_each_interval_and_concatenates_in_order() -> None:
    intervals = (stt_catalog.Interval(0, 10_000), stt_catalog.Interval(14_000, 50_000))
    assert stt_catalog.ffmpeg_filter(intervals) == (
        "[0:a]atrim=start=0:end=10,asetpts=PTS-STARTPTS[s0];"
        "[0:a]atrim=start=14:end=50,asetpts=PTS-STARTPTS[s1];"
        "[s0][s1]concat=n=2:v=0:a=1[out]"
    )


def test_transcript_stems_adds_the_header_title_stem_that_survives_a_note_rename() -> None:
    header = "# 2026-09-16_0215_녹음 전사본\n\n- 원본 음성: of_abc.ogg\n"
    assert stt_catalog.transcript_stems(header, "2026-09-16_1115_자동_굴착") == (
        "2026-09-16_1115_자동_굴착", "2026-09-16_0215_녹음",
    )
    assert stt_catalog.transcript_stems(header, "2026-09-16_0215_녹음") == ("2026-09-16_0215_녹음",)
    assert stt_catalog.transcript_stems("- 원본 음성: x.ogg\n", "stem") == ("stem",)


def test_sample_filename_is_stable_and_free_of_path_characters() -> None:
    name = stt_catalog.sample_filename("홍 길동/박사", "abcd1234", "화자2")
    assert name == "홍_길동_박사-abcd1234-화자2.wav"
    assert "/" not in name and " " not in name
