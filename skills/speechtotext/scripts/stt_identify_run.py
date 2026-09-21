"""식별의 오케스트레이션 — 캐시·자식 실행·fail-soft.

식별은 편의이고 전사는 본업이다. 그래서 이 층의 모든 실패는 표식 한 줄과 빈 결과로 끝난다:
카탈로그가 없든, C API 가 없든, 자식이 죽든, 캐시를 못 쓰든 전사는 그대로 간다. 임베딩 추출을
자식으로 띄우는 이유도 같다 — 네이티브 라이브러리가 죽을 때 두 시간짜리 전사를 데려가면 안 된다.

라벨 하나에 벡터 하나가 아니라 **블록마다 하나**를 뽑는다. 노드 실측(docs/qa/VC3)에서 60초
이어붙임은 채널 특성에 씻겨 확실한 양성이 다른 사람에게 졌고, 블록별로 재서 평균하면 순서가
바로잡혔다. 등록본 임베딩은 카탈로그의 sha256 로 캐시하므로 녹음마다 다시 뽑지 않는다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final

import stt_catalog
import stt_identify
import stt_speakers
import stt_voiceprint

EMBEDDINGS_DIR: Final = "embeddings"
DEFAULT_TIMEOUT: Final = 600.0
_ENROLLED: Final = "등록:"
_TIMEOUT_ENV: Final = "SPEECHTOTEXT_IDENTIFY_TIMEOUT"
#: 추출 자식의 경로. 기본은 형제 모듈이고, 시험·운영이 사본을 선언할 때만 쓴다
#: (선례 `VOICE_CATALOG_ENROLL_CLI`).
_CLI_ENV: Final = "SPEECHTOTEXT_VOICEPRINT_CLI"

Runner = Callable[..., subprocess.CompletedProcess[str]]
Vectors = dict[str, list[float]]


def _skip(reason: str) -> tuple[()]:
    print(f"IDENTIFY-SKIP reason={reason}", file=sys.stderr)
    return ()


def _timeout(env: Mapping[str, str]) -> float:
    try:
        value = float(env.get(_TIMEOUT_ENV, "") or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def _catalog(env: Mapping[str, str]) -> tuple[Path, stt_catalog.Catalog] | None:
    try:
        root = stt_catalog.catalog_root(env)
        file = root / stt_catalog.CATALOG_FILE
        catalog = stt_catalog.from_json(file.read_text(encoding="utf-8")) if file.is_file() else None
    except (stt_catalog.CatalogError, OSError, ValueError):
        return None
    if catalog is None or not catalog.people:
        return None
    return root, catalog


def _cache_path(root: Path, digest: str) -> Path:
    return root / EMBEDDINGS_DIR / f"{stt_identify.cache_key(digest)}.json"


def _load_cache(path: Path) -> Vectors:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: [float(one) for one in value]
        for key, value in payload.items()
        if isinstance(key, str) and isinstance(value, list)
    }


def _save_cache(path: Path, vectors: Vectors) -> None:
    """캐시를 못 써도 식별은 끝난다 — 다음 녹음이 다시 뽑을 뿐이다."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(vectors, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
    except (OSError, ValueError) as failure:
        print(f"IDENTIFY-CACHE-FAIL {type(failure).__name__}", file=sys.stderr)


def _extract(
    toolchain: stt_voiceprint.Toolchain,
    jobs: list[dict[str, object]],
    *,
    env: Mapping[str, str],
    run: Runner,
) -> tuple[Vectors, int] | None:
    if not jobs:
        return {}, 0
    with tempfile.TemporaryDirectory(prefix="stt-identify-") as workdir:
        job_file = Path(workdir) / "job.json"
        job_file.write_text(
            json.dumps(
                {
                    "library_dir": str(toolchain.library_dir),
                    "model": str(toolchain.model),
                    "threads": toolchain.threads,
                    "jobs": jobs,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        declared = (env.get(_CLI_ENV) or "").strip()
        cli = Path(declared) if declared else Path(__file__).with_name("stt_voiceprint.py")
        argv = [sys.executable, str(cli), "--job", str(job_file)]
        try:
            completed = run(
                argv, capture_output=True, text=True, check=False, timeout=_timeout(env)
            )
        except (OSError, subprocess.SubprocessError) as failure:
            print(f"IDENTIFY-FAIL {type(failure).__name__}", file=sys.stderr)
            return None
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()
        print(f"IDENTIFY-FAIL rc={completed.returncode} {tail[-1] if tail else ''}", file=sys.stderr)
        return None
    try:
        payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
        return (
            {key: [float(one) for one in value] for key, value in payload["embeddings"].items()},
            int(payload.get("dim") or 0),
        )
    except (IndexError, KeyError, TypeError, ValueError) as failure:
        print(f"IDENTIFY-FAIL {type(failure).__name__}", file=sys.stderr)
        return None


def _people(
    catalog: stt_catalog.Catalog, vectors: Mapping[str, Sequence[float]], dim: int
) -> dict[str, list[Sequence[float]]]:
    people: dict[str, list[Sequence[float]]] = {}
    for person in catalog.people:
        for sample in person.samples:
            vector = vectors.get(f"{_ENROLLED}{person.name}:{sample.sha256}")
            # 모델이 바뀌면 캐시 키도 바뀌므로 길이가 어긋날 수 없다 — 어긋나면 깨진 파일이다.
            if vector and (dim <= 0 or len(vector) == dim):
                people.setdefault(person.name, []).append(vector)
    return people


def _decide(
    root: Path,
    catalog: stt_catalog.Catalog,
    toolchain: stt_voiceprint.Toolchain,
    per_label: Mapping[str, Sequence[stt_catalog.Interval]],
    wav: Path,
    *,
    env: Mapping[str, str],
    run: Runner,
) -> tuple[tuple[stt_identify.Verdict, ...], stt_identify.ScoreTable]:
    try:
        digest = stt_voiceprint.model_digest(toolchain.model)
    except OSError as failure:
        print(f"IDENTIFY-FAIL {type(failure).__name__}", file=sys.stderr)
        return (), {}
    cache_file = _cache_path(root, digest)
    cached = _load_cache(cache_file)
    jobs: list[dict[str, object]] = []
    for person in catalog.people:
        for sample in person.samples:
            key = f"{_ENROLLED}{person.name}:{sample.sha256}"
            if key not in cached:
                jobs.append({"id": key, "wav": str(root / sample.wav), "spans": None})
    blocks: dict[str, list[str]] = {}
    for label in sorted(per_label):
        for index, interval in enumerate(per_label[label]):
            key = f"{label}#{index}"
            jobs.append(
                {"id": key, "wav": str(wav), "spans": [[interval.start_ms, interval.end_ms]]}
            )
            blocks.setdefault(label, []).append(key)
    extracted = _extract(toolchain, jobs, env=env, run=run)
    if extracted is None:
        return (), {}
    vectors, dim = extracted
    fresh = {key: value for key, value in vectors.items() if key.startswith(_ENROLLED)}
    if fresh:
        _save_cache(cache_file, {**cached, **fresh})
    known = {**cached, **vectors}
    people = _people(catalog, known, dim)
    if not people:
        print("IDENTIFY-FAIL 등록본 임베딩이 없습니다", file=sys.stderr)
        return (), {}
    speakers = {
        label: [known[key] for key in keys if key in known]
        for label, keys in blocks.items()
    }
    speakers = {label: found for label, found in speakers.items() if found}
    if not speakers:
        return (), {}
    thresholds = stt_identify.thresholds_from_env(env)
    return stt_identify.match(speakers, people, thresholds), stt_identify.score_table(
        speakers, people
    )


def identify_intervals(
    wav: Path,
    per_label: Mapping[str, Sequence[stt_catalog.Interval]],
    *,
    env: Mapping[str, str],
    run: Runner = subprocess.run,
) -> tuple[tuple[stt_identify.Verdict, ...], stt_identify.ScoreTable]:
    """이미 고른 구간으로 대조한다 — 전사 경로와 `match` CLI 가 함께 쓰는 핵심."""
    if env.get("SPEECHTOTEXT_IDENTIFY", "").strip() == "0":
        _skip("disabled")
        return (), {}
    found = _catalog(env)
    if found is None:
        _skip("no-catalog")
        return (), {}
    toolchain = stt_voiceprint.resolve_toolchain(dict(env))
    if toolchain is None:
        _skip("no-toolchain")
        return (), {}
    usable = {label: tuple(spans) for label, spans in per_label.items() if spans}
    if not usable:
        _skip("no-spans")
        return (), {}
    root, catalog = found
    try:
        return _decide(root, catalog, toolchain, usable, wav, env=env, run=run)
    except Exception as failure:  # noqa: BLE001 - 식별은 전사를 절대 멈추지 않는다
        print(f"IDENTIFY-FAIL {type(failure).__name__}", file=sys.stderr)
        return (), {}


def identify(
    wav: Path,
    sentences: Iterable[stt_identify.TimedLike],
    *,
    env: Mapping[str, str],
    run: Runner = subprocess.run,
) -> stt_speakers.SpeakerMap:
    """전사 경로의 진입점 — 문장의 라벨·시각에서 구간을 골라 대조하고 이름만 돌려준다."""
    said = tuple(sentences)
    labels = {
        sentence.speaker
        for sentence in said
        if sentence.speaker and sentence.speaker != stt_catalog.UNKNOWN_LABEL
    }
    per_label = {label: stt_identify.spans(said, label) for label in sorted(labels)}
    verdicts, _table = identify_intervals(wav, per_label, env=env, run=run)
    for verdict in verdicts:
        # 이름은 표식에 싣지 않는다 — 진단 로그는 전사본보다 넓은 곳으로 흐른다.
        print(
            f"IDENTIFY-RESULT {verdict.label} {verdict.kind} {verdict.score:.2f}",
            file=sys.stderr,
        )
    return stt_identify.to_speaker_map(verdicts)
