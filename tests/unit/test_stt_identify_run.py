"""voice catalog ③ 의 오케스트레이션 — 캐시·자식 실행·fail-soft.

식별은 편의이고 전사는 본업이다. 그래서 이 층의 모든 실패는 표식 한 줄과 빈 결과로 끝나야
한다 — 카탈로그가 없든, C API 가 없든, 자식이 죽든, 캐시를 못 쓰든 전사는 그대로 간다.
자식으로 띄우는 이유도 같다: 네이티브 라이브러리가 죽을 때 두 시간짜리 전사를 데려가면 안 된다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills" / "speechtotext" / "scripts"))

import stt_catalog  # noqa: E402
import stt_client  # noqa: E402
import stt_identify  # noqa: E402
import stt_identify_run  # noqa: E402
import stt_speaker_flow  # noqa: E402
import stt_speakers  # noqa: E402


@dataclass(frozen=True, slots=True)
class _Said:
    text: str
    start_ms: int | None
    end_ms: int | None
    speaker: str


@dataclass(frozen=True, slots=True)
class _Completed:
    returncode: int
    stdout: str
    stderr: str


class _Child:
    """자식 대역 — 받은 job 을 기록하고 정해 둔 임베딩을 돌려준다."""

    def __init__(self, embeddings: dict[str, list[float]], *, returncode: int = 0, dim: int = 3) -> None:
        self.embeddings = embeddings
        self.returncode = returncode
        self.dim = dim
        self.payloads: list[dict[str, object]] = []

    def __call__(self, argv: list[str], **_kwargs: object) -> _Completed:
        payload = json.loads(Path(argv[argv.index("--job") + 1]).read_text(encoding="utf-8"))
        self.payloads.append(payload)
        wanted = [str(job["id"]) for job in payload["jobs"]]
        served: dict[str, list[float]] = {}
        for key in wanted:
            # 라벨은 블록마다 job 하나(`화자1#0`)라 접두어로도 응답한다.
            base = key.split("#", 1)[0]
            if key in self.embeddings:
                served[key] = self.embeddings[key]
            elif base in self.embeddings:
                served[key] = self.embeddings[base]
        if self.returncode != 0:
            return _Completed(self.returncode, "", "VOICEPRINT-LIB-FAIL OSError")
        return _Completed(
            0,
            json.dumps(
                {
                    "dim": self.dim,
                    "model_sha256": "d" * 64,
                    "embeddings": served,
                    "failed": {key: "없음" for key in wanted if key not in served},
                },
                ensure_ascii=False,
            )
            + "\n",
            "",
        )


def _catalog(root: Path, *, people: dict[str, str]) -> None:
    """people = {이름: 등록본 sha256} — 등록본 하나짜리 사람들."""
    samples = root / stt_catalog.SAMPLES_DIR
    samples.mkdir(parents=True, exist_ok=True)
    catalog = stt_catalog.Catalog()
    for name, digest in people.items():
        wav = samples / f"{name}.wav"
        wav.write_bytes(b"RIFF")
        catalog = stt_catalog.upsert(
            catalog,
            name,
            stt_catalog.Sample(
                wav=f"{stt_catalog.SAMPLES_DIR}/{name}.wav",
                sha256=digest,
                seconds=60.0,
                recording_id="of_test",
                transcript_stem="stem",
                speaker_label="화자1",
                intervals=(stt_catalog.Interval(0, 60_000),),
            ),
            created_at="2026-09-18T00:00:00+09:00",
        )
    (root / stt_catalog.CATALOG_FILE).write_text(stt_catalog.to_json(catalog), encoding="utf-8")


def _toolchain(tmp_path: Path) -> dict[str, str]:
    binary = tmp_path / "sherpa" / "bin" / "diarize"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"")
    library = tmp_path / "sherpa" / "lib"
    library.mkdir(parents=True, exist_ok=True)
    (library / "libsherpa-onnx-c-api.so").write_bytes(b"")
    model = tmp_path / "sherpa" / "models" / "embedding.onnx"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"onnx-bytes")
    return {
        "SPEECHTOTEXT_DIARIZE_BIN": str(binary),
        "SPEECHTOTEXT_DIARIZE_EMBEDDING": str(model),
    }


def _sentences() -> tuple[_Said, ...]:
    return (
        _Said("첫 화자의 긴 발화.", 0, 20_000, "화자1"),
        _Said("두 번째 화자의 긴 발화.", 20_000, 40_000, "화자2"),
    )


def _wav(tmp_path: Path) -> Path:
    path = tmp_path / "input16k.wav"
    path.write_bytes(b"RIFF")
    return path


def test_identify_stays_out_of_the_way_when_the_owner_turned_it_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "catalog"
    _catalog(root, people={"김민수": "aa"})
    child = _Child({})
    env = {
        **_toolchain(tmp_path),
        "SPEECHTOTEXT_VOICE_CATALOG": str(root),
        "SPEECHTOTEXT_IDENTIFY": "0",
    }

    assert stt_identify_run.identify(_wav(tmp_path), _sentences(), env=env, run=child) == ()
    assert "IDENTIFY-SKIP reason=disabled" in capsys.readouterr().err
    assert child.payloads == []


def test_identify_skips_when_nobody_is_enrolled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "catalog"
    root.mkdir()
    child = _Child({})
    env = {**_toolchain(tmp_path), "SPEECHTOTEXT_VOICE_CATALOG": str(root)}

    assert stt_identify_run.identify(_wav(tmp_path), _sentences(), env=env, run=child) == ()
    assert "IDENTIFY-SKIP reason=no-catalog" in capsys.readouterr().err
    assert child.payloads == []


def test_identify_skips_when_the_c_api_is_not_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "catalog"
    _catalog(root, people={"김민수": "aa"})
    child = _Child({})
    env = {"SPEECHTOTEXT_VOICE_CATALOG": str(root)}

    assert stt_identify_run.identify(_wav(tmp_path), _sentences(), env=env, run=child) == ()
    assert "IDENTIFY-SKIP reason=no-toolchain" in capsys.readouterr().err


def test_identify_excludes_a_label_without_a_long_enough_span(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    _catalog(root, people={"김민수": "aa"})
    child = _Child({})
    env = {**_toolchain(tmp_path), "SPEECHTOTEXT_VOICE_CATALOG": str(root)}
    said = (*_sentences(), _Said("짧다.", 41_000, 42_000, "화자3"))

    stt_identify_run.identify(_wav(tmp_path), said, env=env, run=child)

    identifiers = [str(job["id"]) for job in child.payloads[0]["jobs"]]  # type: ignore[index]
    labels = {one.split("#", 1)[0] for one in identifiers if not one.startswith("등록:")}
    assert "화자3" not in labels
    assert labels == {"화자1", "화자2"}


def test_identify_gives_up_quietly_when_the_child_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "catalog"
    _catalog(root, people={"김민수": "aa"})
    child = _Child({}, returncode=1)
    env = {**_toolchain(tmp_path), "SPEECHTOTEXT_VOICE_CATALOG": str(root)}

    assert stt_identify_run.identify(_wav(tmp_path), _sentences(), env=env, run=child) == ()
    assert "IDENTIFY-FAIL" in capsys.readouterr().err


def test_identify_names_the_confirmed_speaker_without_logging_the_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "catalog"
    _catalog(root, people={"김민수": "aa"})
    env = {**_toolchain(tmp_path), "SPEECHTOTEXT_VOICE_CATALOG": str(root)}
    child = _Child(
        {
            "등록:김민수:aa": [1.0, 0.0, 0.0],
            "화자1": [1.0, 0.0, 0.0],
            "화자2": [0.0, 0.0, 1.0],
        }
    )

    identified = stt_identify_run.identify(_wav(tmp_path), _sentences(), env=env, run=child)

    assert identified == (stt_speakers.SpeakerName("화자1", "김민수", "카탈로그 1.00"),)
    diagnostics = capsys.readouterr().err
    assert "IDENTIFY-RESULT 화자1 확정 1.00" in diagnostics
    assert "김민수" not in diagnostics


def test_identify_reuses_the_cached_enrolment_embedding(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    _catalog(root, people={"김민수": "aa"})
    env = {**_toolchain(tmp_path), "SPEECHTOTEXT_VOICE_CATALOG": str(root)}
    child = _Child({"등록:김민수:aa": [1.0, 0.0, 0.0], "화자1": [1.0, 0.0, 0.0], "화자2": [0.0, 0.0, 1.0]})

    first = stt_identify_run.identify(_wav(tmp_path), _sentences(), env=env, run=child)
    second = stt_identify_run.identify(_wav(tmp_path), _sentences(), env=env, run=child)

    assert first == second
    assert any(str(job["id"]).startswith("등록:") for job in child.payloads[0]["jobs"])  # type: ignore[index]
    assert not any(str(job["id"]).startswith("등록:") for job in child.payloads[1]["jobs"])  # type: ignore[index]
    assert all(
        str(job["id"]).split("#", 1)[0] in {"화자1", "화자2"}
        for job in child.payloads[1]["jobs"]  # type: ignore[index]
    )


def test_identify_still_answers_when_the_cache_cannot_be_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "catalog"
    _catalog(root, people={"김민수": "aa"})
    blocked = root / stt_identify_run.EMBEDDINGS_DIR
    blocked.write_text("not a directory", encoding="utf-8")
    env = {**_toolchain(tmp_path), "SPEECHTOTEXT_VOICE_CATALOG": str(root)}
    child = _Child({"등록:김민수:aa": [1.0, 0.0, 0.0], "화자1": [1.0, 0.0, 0.0]})

    identified = stt_identify_run.identify(_wav(tmp_path), _sentences(), env=env, run=child)

    assert identified == (stt_speakers.SpeakerName("화자1", "김민수", "카탈로그 1.00"),)
    assert "IDENTIFY-CACHE-FAIL" in capsys.readouterr().err


def test_identify_intervals_answers_with_verdicts_and_the_score_table(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    _catalog(root, people={"김민수": "aa"})
    env = {**_toolchain(tmp_path), "SPEECHTOTEXT_VOICE_CATALOG": str(root)}
    child = _Child({"등록:김민수:aa": [1.0, 0.0, 0.0], "화자1": [1.0, 0.0, 0.0]})

    verdicts, table = stt_identify_run.identify_intervals(
        _wav(tmp_path),
        {"화자1": (stt_catalog.Interval(0, 20_000),)},
        env=env,
        run=child,
    )

    assert [(one.label, one.kind) for one in verdicts] == [("화자1", "확정")]
    assert table["화자1"]["김민수"] == pytest.approx(1.0)


def test_tidy_merges_the_identified_speakers_into_the_legend() -> None:
    transcription = stt_client.Transcription(
        text="[00:00:00] 화자1\n안녕하세요.\n",
        model="local:test",
        endpoint="local",
        sentences=(),
        speakers=(stt_speakers.SpeakerName("화자1", "김민수", "카탈로그 0.86"),),
    )

    _polished, speakers = stt_speaker_flow.tidy(transcription)

    assert stt_speakers.render_legend(speakers) == "- 화자: 화자1=김민수 [카탈로그 0.86]"


def test_tidy_lets_the_owner_override_the_catalogue() -> None:
    transcription = stt_client.Transcription(
        text="[00:00:00] 화자1\n안녕하세요.\n",
        model="local:test",
        endpoint="local",
        sentences=(),
        speakers=(stt_speakers.SpeakerName("화자1", "김민수", "카탈로그 0.86"),),
    )

    _polished, speakers = stt_speaker_flow.tidy(
        transcription, stt_speakers.parse_override("화자1=이영희")
    )

    assert stt_speakers.names(speakers) == {"화자1": "이영희"}


def test_identify_is_reachable_from_the_pure_layer_defaults() -> None:
    assert stt_identify.DEFAULTS.suggest <= stt_identify.DEFAULTS.accept
