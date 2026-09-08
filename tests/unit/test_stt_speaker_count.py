"""전사본이 말해 주는 화자 수를 얼마나 믿을지 정하는 순수 판정.

음향만으로는 화자 **수**를 정할 수 없다는 것이 실측이다(2026-09-07 노드): 임계값 사다리는
0.8→18 · 1.0→6 · 1.2→2 · 1.35→1 군집으로 절벽이고, 어느 칸도 두 녹음을 동시에 맞히지
못한다. 반면 `--clustering.num-clusters` 로 화자 수를 주면 sherpa 는 그 수를 상한으로 다시
묶는다. 그러니 모자란 것은 분리기가 아니라 **k 를 아는 일**이고, 그 근거(자기소개·호칭·
질문응답 짝)는 오디오가 아니라 전사본 텍스트에 있다.

여기는 그 답을 어떻게 받을지만 정한다 — 모델도 파일도 부르지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_speaker_count  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3", 3),
        ("  4  ", 4),
        ('{"speaker_count": 3}', 3),
        ('```json\n{"speaker_count": 2}\n```', 2),
        ('{"speaker_count": "3"}', 3),
        # 아래는 전부 "답이 아니다". 애매한 답을 반으로 읽으면 없는 화자를 만든다.
        ("화자는 세 명입니다", None),
        ("3~4", None),
        ("3명", None),
        ("0", None),
        ("-1", None),
        ("9", None),
        ("", None),
        ("null", None),
    ],
)
def test_parse_count_takes_only_an_unambiguous_integer_in_range(
    raw: str, expected: int | None
) -> None:
    assert stt_speaker_count.parse_count(raw, limit=8) == expected


def test_parse_count_of_nothing_is_nothing() -> None:
    assert stt_speaker_count.parse_count(None, limit=8) is None


@pytest.mark.parametrize(
    ("estimated", "observed", "expected"),
    [
        # 과분할 되돌리기: 임계값 1.0 이 두 목소리를 넷으로 쪼갠 실측 상황.
        (3, 4, 3),
        # 과병합 풀기: 배포 기본값이 두 녹음을 한 사람으로 뭉갠 실측 상황.
        (4, 1, 4),
        # 같은 답에 sherpa 를 한 번 더 돌리는 것은 GPU 만 쓰고 아무것도 바꾸지 않는다.
        (4, 4, None),
        (None, 4, None),
    ],
)
def test_should_redo_only_when_the_estimate_says_something_new(
    estimated: int | None, observed: int, expected: int | None
) -> None:
    assert stt_speaker_count.should_redo(estimated, observed) == expected


# --- 2-pass 배선: 초안을 읽고 화자 수를 다시 물어 그 수로 한 번만 재분리한다 -------------

import json  # noqa: E402
import stat  # noqa: E402

import stt_audio  # noqa: E402
import stt_diarize  # noqa: E402
import stt_local  # noqa: E402


def _fake_toolchains(tmp_path: Path) -> tuple[stt_audio.CheckedAudio, object, object, Path]:
    """실제 transcribe() 를 돌리기 위한 최소 도구 모음 — 바이너리만 가짜다."""
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_text('#!/bin/sh\nfor a in "$@"; do last="$a"; done\nprintf RIFF > "$last"\n')
    ffmpeg.chmod(0o755)
    whisper = tmp_path / "whisper"
    payload = json.dumps(
        {"transcription": [{"text": "합성 검증.", "offsets": {"from": 0, "to": 1000}}]}
    )
    whisper.write_text(
        f"#!{sys.executable}\nimport pathlib, sys\n"
        f"pathlib.Path(sys.argv[sys.argv.index('-of')+1]+'.json').write_text({payload!r})\n"
    )
    whisper.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"synthetic model")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF0000")

    sherpa = tmp_path / "diarize"
    log = tmp_path / "calls.json"
    # 임계값으로 부르면 화자 2, 화자 수를 못박아 부르면 3 — 두 번째 호출이 일어났는지가
    # 이 시험의 관찰 지점이다.
    sherpa.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "log = pathlib.Path(os.environ['RECOUNT_LOG'])\n"
        "calls = json.loads(log.read_text()) if log.exists() else []\n"
        "calls.append(sys.argv[1:])\n"
        "log.write_text(json.dumps(calls))\n"
        "forced = any(a.startswith('--clustering.num-clusters=') for a in sys.argv)\n"
        "for index in range(3 if forced else 2):\n"
        "    print('%d -- %d speaker_%02d' % (index, index + 1, index))\n"
    )
    sherpa.chmod(sherpa.stat().st_mode | stat.S_IXUSR)
    segmentation = tmp_path / "segmentation.onnx"
    embedding = tmp_path / "embedding.onnx"
    segmentation.touch()
    embedding.touch()

    local = stt_local.resolve_toolchain({
        "SPEECHTOTEXT_FFMPEG_BIN": str(ffmpeg),
        "SPEECHTOTEXT_WHISPER_BIN": str(whisper),
        "SPEECHTOTEXT_WHISPER_MODEL": str(model),
    })
    assert local is not None
    diarizer = stt_diarize.resolve_toolchain({
        "SPEECHTOTEXT_DIARIZE_BIN": str(sherpa),
        "SPEECHTOTEXT_DIARIZE_SEGMENTATION": str(segmentation),
        "SPEECHTOTEXT_DIARIZE_EMBEDDING": str(embedding),
    })
    assert diarizer is not None
    checked = stt_audio.CheckedAudio(path=audio, suffix=".wav", mime="audio/wav", size_bytes=8)
    return checked, local, diarizer, log


def test_a_different_answer_redoes_the_diarization_with_that_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """전사본이 3명이라고 말하면 그 수를 못박아 한 번 더 나눈다."""
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(tmp_path / "cache"))
    checked, local, diarizer, log = _fake_toolchains(tmp_path)
    monkeypatch.setenv("RECOUNT_LOG", str(log))
    asked: list[str] = []

    def ask(draft: str) -> str:
        asked.append(draft)
        return '{"speaker_count": 3}'

    result = stt_local.transcribe(checked, local, diarizer=diarizer, count_speakers=ask)

    calls = json.loads(log.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert any(arg.startswith("--clustering.cluster-threshold=") for arg in calls[0])
    assert "--clustering.num-clusters=3" in calls[1]
    # 초안에는 **화자 라벨이 없다**. 노드 실측(2026-09-07): 같은 오디오·같은 전사·같은
    # 프롬프트에서 라벨을 남기면 모델이 2명, 걷어내면 3명(소유자 기준점)이라 답했다.
    # 초안이 1차 분리 라벨로 조립되므로 모델은 문서에 보이는 라벨 종류를 그대로 세어
    # 돌려준다 — 프롬프트의 "라벨을 믿지 말라"가 그 앵커를 이기지 못한다.
    assert len(asked) == 1
    assert "화자" not in asked[0]
    assert "[00:00:00]" in asked[0]
    assert "DIARIZE-RECOUNT observed=2 asked=3 redo=3" in capsys.readouterr().err
    assert result.text == "합성 검증."


def test_the_same_answer_does_not_run_the_separator_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """같은 답에 분리기를 또 돌리는 것은 시간만 쓰고 문서를 바꾸지 않는다."""
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(tmp_path / "cache"))
    checked, local, diarizer, log = _fake_toolchains(tmp_path)
    monkeypatch.setenv("RECOUNT_LOG", str(log))

    _ = stt_local.transcribe(checked, local, diarizer=diarizer, count_speakers=lambda _draft: "2")

    assert len(json.loads(log.read_text(encoding="utf-8"))) == 1
    assert "DIARIZE-RECOUNT observed=2 asked=2 redo=None" in capsys.readouterr().err


def test_a_declared_speaker_count_is_never_second_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """소유자가 화자 수를 선언했으면 그 선언이 모든 추정을 이긴다 — 묻지도 않는다."""
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(tmp_path / "cache"))
    checked, local, diarizer, log = _fake_toolchains(tmp_path)
    monkeypatch.setenv("RECOUNT_LOG", str(log))

    def ask(_draft: str) -> str:
        raise AssertionError("선언된 화자 수를 두고 모델에게 물었다")

    _ = stt_local.transcribe(
        checked, local, diarizer=diarizer, num_speakers=2, count_speakers=ask
    )

    assert len(json.loads(log.read_text(encoding="utf-8"))) == 1


def test_a_failed_question_keeps_the_first_diarization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """질의가 실패해도 전사는 계속된다 — 화자 라벨은 1차 결과를 그대로 쓴다."""
    monkeypatch.setenv("SPEECHTOTEXT_WINDOW_CACHE", str(tmp_path / "cache"))
    checked, local, diarizer, log = _fake_toolchains(tmp_path)
    monkeypatch.setenv("RECOUNT_LOG", str(log))

    def ask(_draft: str) -> str:
        raise RuntimeError("transport")

    result = stt_local.transcribe(checked, local, diarizer=diarizer, count_speakers=ask)

    assert len(json.loads(log.read_text(encoding="utf-8"))) == 1
    assert "RECOUNT-FAIL RuntimeError" in capsys.readouterr().err
    assert result.text == "합성 검증."


def test_unlabelled_removes_the_speaker_anchor_but_keeps_the_clock() -> None:
    """세지 말라고 부탁하는 대신 셀 것을 주지 않는다."""
    draft = "[00:00:00] 화자1 · 김민수\n안녕하세요.\n\n[00:01:02] 화자0 · UNKNOWN\n네.\n\n[--:--:--] 화자2\n그렇군요."

    result = stt_speaker_count.unlabelled(draft)

    assert "화자" not in result
    assert "김민수" not in result
    assert result.splitlines()[0] == "[00:00:00]"
    assert "[--:--:--]" in result.splitlines()
    # 말은 한 글자도 바뀌지 않는다.
    for said in ("안녕하세요.", "네.", "그렇군요."):
        assert said in result
