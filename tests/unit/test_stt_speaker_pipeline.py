"""화자 파이프라인 통합 — 전사(diarize) → 회의록 LLM 이름 → 소유자 지정.

한 문서 안에서 세 출처가 만난다. 규칙 기반 자기소개, meeting 자식이 돌려준 LLM 제안,
그리고 소유자가 직접 준 이름. 이 파일은 그 세 가지가 CLI 한 번의 실행으로 실제 파일에
어떻게 도착하는지를 진짜 프로세스(가짜 whisper/ffmpeg/diarize/meeting 바이너리)로 확인한다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "speechtotext"
sys.path.insert(0, str(SKILL / "scripts"))

import speechtotext_cli  # noqa: E402
import stt_polish  # noqa: E402
import stt_speakers  # noqa: E402

FIRST = "안녕하세요, 저는 김민수입니다. 오늘 안건을 정리하겠습니다."
SECOND = "네, 알겠습니다. 다음 주까지 초안을 보내겠습니다."


def _segment(text: str, start_ms: int, end_ms: int) -> dict[str, object]:
    """whisper.cpp `-ojf` 세그먼트 하나 — 토큰마다 자기 구간을 갖는다."""
    words = text.split()
    span = (end_ms - start_ms) // max(len(words), 1)
    return {
        "offsets": {"from": start_ms, "to": end_ms},
        "text": " " + text,
        "tokens": [
            {
                "text": (" " if index else "") + word,
                "offsets": {
                    "from": start_ms + span * index,
                    "to": start_ms + span * (index + 1),
                },
            }
            for index, word in enumerate(words)
        ],
    }


WHISPER_JSON = json.dumps(
    {"transcription": [_segment(FIRST, 0, 4000), _segment(SECOND, 4500, 9000)]},
    ensure_ascii=False,
)


@pytest.fixture
def toolchain(tmp_path: Path) -> dict[str, Path]:
    """진짜 실행 파일 대역 — ffmpeg, whisper-cli, sherpa-onnx diarize, meeting CLI."""
    scripts: dict[str, Path] = {}

    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text(
        '#!/bin/sh\nfor a in "$@"; do last="$a"; done\nprintf "RIFF" > "$last"\n',
        encoding="utf-8",
    )
    ffmpeg.chmod(0o755)
    scripts["ffmpeg"] = ffmpeg

    whisper = tmp_path / "whisper-cli"
    whisper.write_text(
        "#!/bin/sh\n"
        'of=""\n'
        'while [ $# -gt 0 ]; do case "$1" in -of) of="$2"; shift 2;; *) shift;; esac; done\n'
        "cat > \"$of.json\" <<'WHISPER_EOF'\n" + WHISPER_JSON + "\nWHISPER_EOF\n",
        encoding="utf-8",
    )
    whisper.chmod(0o755)
    scripts["whisper"] = whisper

    model = tmp_path / "ggml-large-v3-turbo-q5_0.bin"
    model.write_bytes(b"ggml-model-fixture")
    scripts["model"] = model

    marker = tmp_path / "diarize-ran.txt"
    diarize = tmp_path / "sherpa-onnx-offline-speaker-diarization"
    diarize.write_text(
        "#!/bin/sh\n"
        f'printf "ran\\n" >> {str(marker)!r}\n'
        'printf "num_clusters=-1\\nStarted\\n"\n'
        'printf "0.000 -- 4.100 speaker_01\\n"\n'
        'printf "4.400 -- 9.000 speaker_00\\n"\n'
        'printf "Real time factor (RTF): 0.03\\n" >&2\n',
        encoding="utf-8",
    )
    diarize.chmod(0o755)
    scripts["diarize"] = diarize
    scripts["marker"] = marker

    segmentation = tmp_path / "segmentation.onnx"
    segmentation.write_bytes(b"onnx-segmentation-fixture")
    embedding = tmp_path / "embedding.onnx"
    embedding.write_bytes(b"onnx-embedding-fixture")
    scripts["segmentation"] = segmentation
    scripts["embedding"] = embedding
    return scripts


def _env(monkeypatch, tmp_path: Path, toolchain: dict[str, Path], *, diarize: bool = True) -> None:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SPEECHTOTEXT_TRANSCRIPT_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setenv("SPEECHTOTEXT_GLOSSARY", str(tmp_path / "absent-glossary.txt"))
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    monkeypatch.delenv("SPEECHTOTEXT_PROMPT", raising=False)
    monkeypatch.setenv("SPEECHTOTEXT_BACKEND", "local")
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_BIN", str(toolchain["whisper"]))
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_MODEL", str(toolchain["model"]))
    monkeypatch.setenv("SPEECHTOTEXT_FFMPEG_BIN", str(toolchain["ffmpeg"]))
    if diarize:
        monkeypatch.setenv("SPEECHTOTEXT_DIARIZE_BIN", str(toolchain["diarize"]))
        monkeypatch.setenv("SPEECHTOTEXT_DIARIZE_SEGMENTATION", str(toolchain["segmentation"]))
        monkeypatch.setenv("SPEECHTOTEXT_DIARIZE_EMBEDDING", str(toolchain["embedding"]))


def _meeting(tmp_path: Path, monkeypatch, body: str) -> Path:
    """meeting CLI 대역 — 마지막 stdout 줄에 JSON 하나를 찍는 계약만 흉내낸다."""
    record = tmp_path / "meeting-argv.json"
    script = tmp_path / "fake_meeting_cli.py"
    script.write_text(
        "import json, os, sys\n"
        f"json.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n" + body,
        encoding="utf-8",
    )
    monkeypatch.setenv("SPEECHTOTEXT_MEETING_CLI", str(script))
    return record


_MEETING_NAMES = (
    "print('회의록 초안을 작성했습니다.')\n"
    "print(json.dumps({'ok': True, 'speakers': "
    "[{'label': '화자2', 'name': '이영희', 'basis': '호명'}]}, ensure_ascii=False))\n"
)
_MEETING_NO_JSON = "print('LLM 응답을 받지 못했습니다.')\nsys.exit(6)\n"


def _audio(tmp_path: Path) -> Path:
    audio = tmp_path / "20260901_킥오프.wav"
    audio.write_bytes(b"RIFF0000")
    return audio


def _transcript_of(summary: dict[str, object]) -> str:
    return Path(str(summary["transcript_path"])).read_text(encoding="utf-8")


def test_transcribe_labels_blocks_and_names_the_speaker_who_introduced_himself(
    tmp_path: Path, monkeypatch, toolchain, capsys
) -> None:
    """자기소개한 사람은 이름이 붙고, 이름을 모르는 화자도 화자N 으로 남는다."""
    _env(monkeypatch, tmp_path, toolchain)
    _meeting(tmp_path, monkeypatch, "sys.exit(0)\n")

    assert speechtotext_cli.main(
        ["transcribe", "--file", str(_audio(tmp_path)), "--label", "킥오프"]
    ) == 0

    summary = json.loads(capsys.readouterr().out)
    document = _transcript_of(summary)
    header, body = stt_polish.split_document(document)
    assert "- 화자: 화자1=김민수 [자기소개 00:00:00] · 화자2=미상" in header
    blocks = [block for block in body.strip().split("\n\n") if block.strip()]
    assert blocks[0].splitlines()[0] == "[00:00:00] 화자1 · 김민수"
    assert blocks[0].splitlines()[1:] == ["안녕하세요, 저는 김민수입니다.", "오늘 안건을 정리하겠습니다."]
    assert blocks[1].splitlines()[0] == "[00:00:04] 화자2"
    assert blocks[1].splitlines()[1:] == ["네, 알겠습니다.", "다음 주까지 초안을 보내겠습니다."]
    assert summary["diarized"] is True
    assert summary["speakers"] == [
        {"label": "화자1", "name": "김민수", "source": "자기소개 00:00:00"},
        {"label": "화자2", "name": "", "source": ""},
    ]


def test_ingest_absorbs_the_meeting_llm_names_into_the_transcript(
    tmp_path: Path, monkeypatch, toolchain, capsys
) -> None:
    """meeting 이 호명으로 알아낸 이름은 전사본 범례와 블록 머리글까지 되돌아온다."""
    _env(monkeypatch, tmp_path, toolchain)
    _meeting(tmp_path, monkeypatch, _MEETING_NAMES)

    assert speechtotext_cli.main(
        ["ingest", "--file", str(_audio(tmp_path)), "--label", "킥오프"]
    ) == 0

    captured = capsys.readouterr()
    assert "회의록 초안을 작성했습니다." in captured.out
    summary = json.loads(captured.out.strip().splitlines()[-1])
    document = _transcript_of(summary)
    header, body = stt_polish.split_document(document)
    assert "화자1=김민수 [자기소개 00:00:00]" in header
    assert "화자2=이영희 [LLM]" in header
    assert "[00:00:04] 화자2 · 이영희" in body
    assert summary["speakers"] == [
        {"label": "화자1", "name": "김민수", "source": "자기소개 00:00:00"},
        {"label": "화자2", "name": "이영희", "source": "LLM"},
    ]


def test_a_failing_diarizer_never_costs_the_transcript(
    tmp_path: Path, monkeypatch, toolchain, capsys
) -> None:
    """화자 분리는 부가 기능이다 — 실패해도 전사본은 그대로 나온다."""
    _env(monkeypatch, tmp_path, toolchain)
    toolchain["diarize"].write_text("#!/bin/sh\necho 'model load failed' >&2\nexit 1\n", encoding="utf-8")
    toolchain["diarize"].chmod(0o755)
    _meeting(tmp_path, monkeypatch, "sys.exit(0)\n")

    assert speechtotext_cli.main(
        ["transcribe", "--file", str(_audio(tmp_path)), "--label", "킥오프"]
    ) == 0

    captured = capsys.readouterr()
    assert "DIARIZE-FAIL" in captured.err
    summary = json.loads(captured.out)
    document = _transcript_of(summary)
    header, body = stt_polish.split_document(document)
    assert "화자" not in header
    assert "화자1" not in body
    assert "안녕하세요, 저는 김민수입니다." in body
    assert summary["diarized"] is False
    assert summary["speakers"] == []


def test_no_diarize_never_runs_the_binary(
    tmp_path: Path, monkeypatch, toolchain, capsys
) -> None:
    _env(monkeypatch, tmp_path, toolchain)
    _meeting(tmp_path, monkeypatch, "sys.exit(0)\n")

    assert speechtotext_cli.main(
        ["transcribe", "--file", str(_audio(tmp_path)), "--label", "킥오프", "--no-diarize"]
    ) == 0

    assert not toolchain["marker"].exists()
    summary = json.loads(capsys.readouterr().out)
    assert summary["diarized"] is False
    assert "화자" not in _transcript_of(summary).split("---", 1)[0]


def test_owner_override_wins_and_survives_a_later_polish(
    tmp_path: Path, monkeypatch, toolchain, capsys
) -> None:
    """소유자가 준 이름이 최상위다 — 다음 polish 가 그것을 지우면 안 된다."""
    _env(monkeypatch, tmp_path, toolchain)
    _meeting(tmp_path, monkeypatch, _MEETING_NAMES)
    assert speechtotext_cli.main(
        ["ingest", "--file", str(_audio(tmp_path)), "--label", "킥오프"]
    ) == 0
    transcript = Path(
        str(json.loads(capsys.readouterr().out.strip().splitlines()[-1])["transcript_path"])
    )

    assert speechtotext_cli.main(
        ["polish", "--file", str(transcript), "--speakers", "화자2=박철수"]
    ) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["speakers"] == [
        {"label": "화자1", "name": "김민수", "source": "자기소개 00:00:00"},
        {"label": "화자2", "name": "박철수", "source": "소유자"},
    ]
    document = transcript.read_text(encoding="utf-8")
    assert "화자2=박철수 [소유자]" in document
    assert "[00:00:04] 화자2 · 박철수" in document
    assert document.count("- 화자:") == 1

    assert speechtotext_cli.main(["polish", "--file", str(transcript)]) == 0
    capsys.readouterr()
    assert transcript.read_text(encoding="utf-8") == document


def test_a_meeting_that_prints_no_json_propagates_its_exit_and_changes_nothing(
    tmp_path: Path, monkeypatch, toolchain, capsys
) -> None:
    _env(monkeypatch, tmp_path, toolchain)
    _meeting(tmp_path, monkeypatch, _MEETING_NO_JSON)

    assert speechtotext_cli.main(
        ["ingest", "--file", str(_audio(tmp_path)), "--label", "킥오프"]
    ) == speechtotext_cli.MEETING_CHAIN_EXIT

    captured = capsys.readouterr()
    assert "LLM 응답을 받지 못했습니다." in captured.out
    summary = json.loads(captured.out.strip().splitlines()[-1])
    document = _transcript_of(summary)
    assert "- 화자: 화자1=김민수 [자기소개 00:00:00] · 화자2=미상" in document
    assert "이영희" not in document


def _local_transcription(tmp_path, monkeypatch, toolchain, *, diarize=True):
    """실제 창 실행·병합까지 거치고 문서화 직전 결과를 돌려준다."""
    from dataclasses import replace

    import stt_audio
    import stt_diarize
    import stt_local

    _env(monkeypatch, tmp_path, toolchain)
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(tmp_path / "window-cache"))
    monkeypatch.setenv("SPEECHTOTEXT_MAX_REPEAT", "1")
    monkeypatch.delenv("SPEECHTOTEXT_WHISPER_DTW", raising=False)
    local = stt_local.resolve_toolchain(dict(os.environ))
    assert local is not None
    diarizer = stt_diarize.resolve_toolchain(dict(os.environ)) if diarize else None
    return stt_local.transcribe(
        stt_audio.check_audio(_audio(tmp_path)), replace(local, ffprobe=None),
        diarizer=diarizer,
    )


def test_todo12_characterization_local_timing_and_fail_soft(tmp_path, monkeypatch, toolchain):
    result = _local_transcription(tmp_path, monkeypatch, toolchain, diarize=False)
    assert result.sentences[0].start_ms == 0
    assert result.sentences[0].words[0].word.start_ms == 0
    assert result.sentences[0].words[0].word.timing_source == "token"
    assert all(s.speaker == "" and s.attribution is None for s in result.sentences)


def test_todo12_characterization_toolchain_and_fingerprint(tmp_path, monkeypatch, toolchain):
    from dataclasses import replace

    import stt_local

    _env(monkeypatch, tmp_path, toolchain)
    local = stt_local.resolve_toolchain(dict(os.environ))
    assert isinstance(local, stt_local.LocalToolchain)
    assert local.binary == toolchain["whisper"]
    assert local.model == toolchain["model"]
    fingerprint = stt_local.asr_fingerprint(local, prompt="", env={})
    assert fingerprint == stt_local.asr_fingerprint(replace(local, threads=2), prompt="", env={})
    assert fingerprint != stt_local.asr_fingerprint(local, prompt="용어", env={})
    assert fingerprint != stt_local.asr_fingerprint(local, prompt="", env={"SPEECHTOTEXT_WHISPER_DTW": "large.v3"})


def _write_whisper(toolchain, segments):
    """대역 실행 파일이 고정 JSON만 출력하도록 한다."""
    payload = json.dumps({"transcription": segments}, ensure_ascii=False)
    toolchain["whisper"].write_text(
        "#!/bin/sh\nof=''\n"
        'while [ $# -gt 0 ]; do case "$1" in -of) of="$2"; shift 2;; *) shift;; esac; done\n'
        "cat > \"$of.json\" <<'EOF'\n" + payload + "\nEOF\n", encoding="utf-8",
    )


def _backchannel_fixture(toolchain):
    """실녹음이 아닌 합성 발화: 200ms 응답과 10초 뒤 무근거 문장."""
    _write_whisper(toolchain, [{
        "text": "시작 네 계속. 근거 없음.", "offsets": {"from": 0, "to": 12000},
        "tokens": [
            {"text": text, "offsets": {"from": start, "to": end}}
            for text, start, end in (
                ("시작", 0, 2000), (" 네", 2000, 2200), (" 계속.", 2200, 4000),
                (" 근거", 10000, 11000), (" 없음.", 11000, 12000),
            )
        ],
    }])
    toolchain["diarize"].write_text(
        '#!/bin/sh\nprintf "0.000 -- 2.000 speaker_00\\n'
        '2.000 -- 2.200 speaker_01\\n2.200 -- 4.000 speaker_00\\n"\n',
        encoding="utf-8",
    )


def test_todo12_short_backchannel_and_unsupported_sentence(tmp_path, monkeypatch, toolchain):
    import stt_attribute
    import stt_blocks

    _backchannel_fixture(toolchain)
    seen = []
    attribute = stt_attribute.attribute_words

    def record(words, turns, *, policy):
        words = tuple(words)
        seen.append(words)
        return attribute(words, turns, policy=policy)

    monkeypatch.setattr(stt_attribute, "attribute_words", record)
    result = _local_transcription(tmp_path, monkeypatch, toolchain)
    grouped = stt_blocks.group(result.sentences)
    # 200ms 응답은 문장을 자르지 않고 그 문장 안에 남는다(stt_settle). 무근거 문장은 그대로 화자0.
    assert [b.speaker for b in grouped] == ["화자1", "화자0"]
    assert grouped[0].sentences == ("시작 네 계속.",)
    assert result.sentences[0].asides == (stt_blocks.Aside(3, 4, "화자2"),)
    assert grouped[-1].attribution == stt_attribute.SpeakerTag("UNKNOWN")
    assert seen and [(w.start_ms, w.end_ms) for w in seen[0]][1] == (2000, 2200)
    assert [(s.start_ms, s.end_ms) for s in result.sentences][0] == (0, 4000)
    refs = [w for s in result.sentences for w in s.words]
    assert [w.source_index for w in refs] == list(range(5))
    body = stt_blocks.render(grouped)
    assert "시작 [화자2: 네] 계속." in body.splitlines()
    assert stt_blocks.render(stt_blocks.group(stt_blocks.parse(body))) == body


@pytest.mark.parametrize("bounds", [(5000, 4000), (-2, -2), (3000, 3000)])
def test_todo12_bad_word_timing_never_inherits(tmp_path, monkeypatch, toolchain, bounds):
    import stt_attribute

    _backchannel_fixture(toolchain)
    _write_whisper(toolchain, [{
        "text": "시작. 미상.", "offsets": {"from": 0, "to": 5000},
        "tokens": [
            {"text": "시작.", "offsets": {"from": 0, "to": 1000}},
            {"text": " 미상.", "offsets": {"from": bounds[0], "to": bounds[1]}},
        ],
    }])
    result = _local_transcription(tmp_path, monkeypatch, toolchain)
    assert result.sentences[-1].speaker == "화자0"
    assert result.sentences[-1].attribution == stt_attribute.SpeakerTag("UNKNOWN")


def test_todo12_legacy_unsupported_sentence_never_inherits(tmp_path, monkeypatch, toolchain):
    import stt_attribute

    _backchannel_fixture(toolchain)
    _write_whisper(toolchain, [
        {"text": "시작.", "offsets": {"from": 0, "to": 1000}},
        {"text": "상대.", "offsets": {"from": 2000, "to": 2200}},
        {"text": "미상."},
    ])
    result = _local_transcription(tmp_path, monkeypatch, toolchain)
    assert [s.speaker for s in result.sentences] == ["화자1", "화자2", "화자0"]
    assert result.sentences[-1].attribution == stt_attribute.SpeakerTag("UNKNOWN")


def test_todo12_cached_words_are_attributed_again_with_current_turns(
    tmp_path, monkeypatch, toolchain, capsys,
):
    import stt_window
    import stt_window_run
    import stt_window_store

    _backchannel_fixture(toolchain)
    saved = []
    clear = stt_window_store.WindowStore.clear
    window = stt_window.Window(0, 0, 0)

    def record_clear(store):
        saved.append((store, store.load(window)))
        clear(store)

    monkeypatch.setattr(stt_window_store.WindowStore, "clear", record_clear)
    first = _local_transcription(tmp_path, monkeypatch, toolchain)
    assert [aside.speaker for aside in first.sentences[0].asides] == ["화자2"]
    store, raw = saved[0]
    assert raw is not None and not store.windows.exists()
    store.save(window, raw)
    toolchain["diarize"].write_text(
        '#!/bin/sh\nprintf "0.000 -- 12.000 speaker_00\\n"\n', encoding="utf-8",
    )

    def unexpected_decode(*args, **kwargs):
        raise AssertionError("캐시 적중 때 whisper를 다시 실행함")

    monkeypatch.setattr(stt_window_run, "_run_window", unexpected_decode)
    second = _local_transcription(tmp_path, monkeypatch, toolchain)
    assert all(s.speaker == "화자1" for s in second.sentences)
    assert all(s.asides == () for s in second.sentences)
    assert saved[1][0].key == store.key
    assert not store.windows.exists()
    assert "WHISPER-WINDOW-CACHED" in capsys.readouterr().err


def test_todo12_attribution_error_is_not_reported_as_inherited_success(
    tmp_path, monkeypatch, toolchain,
):
    import stt_attribute

    _backchannel_fixture(toolchain)

    def broken_assignment(*args, **kwargs):
        raise RuntimeError("attribution-fixture-failure")

    monkeypatch.setattr(stt_attribute, "attribute_words", broken_assignment)
    with pytest.raises(RuntimeError, match="attribution-fixture-failure"):
        _local_transcription(tmp_path, monkeypatch, toolchain)


def test_todo12_gap_marker_is_not_word_attribution_evidence() -> None:
    import stt_blocks
    import stt_diarize
    import stt_local_attribution
    import stt_window

    marker = stt_window.gap_marker(stt_window.Window(1, 1000, 59000), until=60000)
    words = (
        stt_blocks.TimedWord("시작", 0, 1000),
        stt_blocks.TimedWord(marker, 1000, 60000, "<details>합성 증거</details>", "segment"),
        stt_blocks.TimedWord(" 다음.", 60000, 61000),
    )
    turns = (stt_diarize.Turn(0, 1000, 0), stt_diarize.Turn(1000, 61000, 1))
    result = stt_local_attribution.sentences(words, turns)
    assert [s.speaker for s in result] == ["화자1", "", "화자2"]
    assert result[1].attribution is None
    assert result[1].folded == words[1].folded
    assert result[2].words[0].source_index == 2


def _document_roundtrip(document: str) -> str:
    """메타데이터는 그대로 두고 단일 본문 파서·렌더러로 왕복한다."""
    import stt_blocks

    header, body = stt_polish.split_document(document)
    rendered = stt_blocks.render(stt_blocks.group(stt_blocks.parse(body)))
    return header + "---\n\n" + rendered + "\n"


@pytest.mark.parametrize("mode", ["word", "legacy"])
def test_todo12_assignment_header_roundtrips_with_old_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, toolchain: dict[str, Path], mode: str,
) -> None:
    from datetime import datetime

    import stt_transcript

    _backchannel_fixture(toolchain)
    if mode == "legacy":
        _write_whisper(toolchain, [{"text": "합성 무시각 발화."}])
    result = _local_transcription(tmp_path, monkeypatch, toolchain)
    assert getattr(result, "attribution_mode", None) == mode
    document = stt_transcript.render(
        label="합성검증", source_name="synthetic.wav", transcription=result,
        now=datetime(2026, 1, 1), polish=stt_polish.polish_sentences(result.sentences),
    )
    header, _ = stt_polish.split_document(document)
    assignment = [line for line in header.splitlines() if line.startswith("- 화자 배정:")]
    assert assignment == [f"- 화자 배정: {mode}"]
    assert _document_roundtrip(document).encode() == document.encode()
    old = document.replace(assignment[0] + "\n", "")
    assert _document_roundtrip(old).encode() == old.encode()
    if mode == "legacy":
        assert all(s.speaker == "화자0" and s.attribution.kind == "UNKNOWN"
                   for s in result.sentences)


def test_todo12_api_transcription_defaults_to_legacy_header() -> None:
    from datetime import datetime

    import stt_client
    import stt_transcript

    result = stt_client.Transcription("합성 API 발화.", "synthetic", "api")
    document = stt_transcript.render(
        label="합성검증", source_name="synthetic.wav", transcription=result,
        now=datetime(2026, 1, 1),
    )
    header, _ = stt_polish.split_document(document)
    assert "- 화자 배정: legacy" in header.splitlines()
    assert _document_roundtrip(document) == document


def test_legend_lines_are_omitted_when_nobody_is_attributed() -> None:
    import stt_speaker_flow

    assert stt_speaker_flow.legend_lines(()) == ()
    assert stt_speaker_flow.legend_lines(
        (stt_speakers.SpeakerName("화자1", "김민수", "소유자"),)
    ) == ("- 화자: 화자1=김민수 [소유자]",)
