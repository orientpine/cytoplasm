"""등록된 목소리를 **수치로** 만드는 I/O 절반 — 노드의 sherpa-onnx C API 를 stdlib 로 부른다.

노드는 aarch64 이고 `sherpa-onnx` 파이썬 휠도 numpy 도 onnxruntime 파이썬 패키지도 없다.
그런데 화자 분리가 이미 쓰는 `libsherpa-onnx-c-api.so` 와 임베딩 모델이 거기 있으므로,
서드파티를 새로 들이는 대신 `ctypes` 로 **같은 모델**을 부른다. 분리기와 같은 모델을 쓰는
것은 취향이 아니라 전제다 — 다른 모델의 임베딩끼리는 코사인을 비교할 근거가 없다.

이 파일은 형제 모듈을 import 하지 않는다. 호출자가 `[sys.executable, 이 파일, --job …]` 로
따로 띄우기 때문이다: 네이티브 라이브러리가 죽을 때 두 시간짜리 전사 프로세스까지 데려가면
안 된다.
"""

from __future__ import annotations

import argparse
import array
import ctypes
import hashlib
import json
import os
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

C_API_LIBRARY: Final = "libsherpa-onnx-c-api.so"
RUNTIME_LIBRARY: Final = "libonnxruntime.so"
SAMPLE_RATE: Final = 16_000
_MAX_THREADS: Final = 4


class VoiceprintError(RuntimeError):
    """임베딩을 만들 수 없는 이유 — 호출자가 표식과 함께 stderr 로 낸다."""


@dataclass(frozen=True, slots=True)
class Toolchain:
    library_dir: Path
    model: Path
    threads: int


class ExtractorLike(Protocol):
    @property
    def dim(self) -> int: ...

    def compute(self, samples: array.array[float]) -> tuple[float, ...]: ...

    def close(self) -> None: ...


class _ExtractorConfig(ctypes.Structure):
    _fields_ = (
        ("model", ctypes.c_char_p),
        ("num_threads", ctypes.c_int32),
        ("debug", ctypes.c_int32),
        ("provider", ctypes.c_char_p),
    )


def _threads(env: dict[str, str] | None = None) -> int:
    raw = (env or {}).get("SPEECHTOTEXT_DIARIZE_THREADS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return min(os.cpu_count() or 1, _MAX_THREADS)


def resolve_toolchain(env: dict[str, str]) -> Toolchain | None:
    """분리기가 이미 선언한 경로에서 C API 와 임베딩 모델을 찾는다 — 새 설정을 요구하지 않는다."""
    if env.get("SPEECHTOTEXT_DIARIZE_BACKEND", "sherpa") != "sherpa":
        return None
    binary = Path(env.get("SPEECHTOTEXT_DIARIZE_BIN", "").strip() or ".").expanduser()
    model = Path(env.get("SPEECHTOTEXT_DIARIZE_EMBEDDING", "").strip() or ".").expanduser()
    library_dir = binary.parent.parent / "lib"
    if not model.is_file() or not (library_dir / C_API_LIBRARY).is_file():
        return None
    return Toolchain(library_dir=library_dir, model=model, threads=_threads(env))


def model_digest(model: Path) -> str:
    digest = hashlib.sha256()
    with model.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_pcm(
    wav: Path, spans: list[list[int]] | list[tuple[int, int]] | None
) -> array.array[float]:
    """16 kHz mono PCM16 만 받고, 요청한 구간만 이어 붙인다.

    형식을 넓히지 않는 이유는 분리기·전사기가 이미 그 형식으로 정규화하기 때문이다. 다른
    형식이 들어왔다면 정규화를 건너뛴 것이고, 그건 조용히 맞춰 줄 일이 아니다.
    """
    with wave.open(str(wav), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (
            SAMPLE_RATE, 1, 2
        ):
            raise VoiceprintError(
                f"VOICEPRINT-WAV-FORMAT {wav.name} 은 16 kHz mono PCM16 이 아닙니다"
            )
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) - len(raw) % 2])
    windows = [(0, len(pcm))] if spans is None else [
        (
            max(0, min(len(pcm), int(start) * SAMPLE_RATE // 1_000)),
            max(0, min(len(pcm), int(end) * SAMPLE_RATE // 1_000)),
        )
        for start, end in spans
    ]
    samples = array.array("f")
    for start, end in windows:
        samples.extend(value / 32_768.0 for value in pcm[start:end])
    if not samples:
        raise VoiceprintError(f"VOICEPRINT-EMPTY {wav.name} 에서 고른 구간이 비어 있습니다")
    return samples


def _declare(api: ctypes.CDLL) -> None:
    api.SherpaOnnxCreateSpeakerEmbeddingExtractor.argtypes = (
        ctypes.POINTER(_ExtractorConfig),
    )
    api.SherpaOnnxCreateSpeakerEmbeddingExtractor.restype = ctypes.c_void_p
    api.SherpaOnnxDestroySpeakerEmbeddingExtractor.argtypes = (ctypes.c_void_p,)
    api.SherpaOnnxDestroySpeakerEmbeddingExtractor.restype = None
    api.SherpaOnnxSpeakerEmbeddingExtractorDim.argtypes = (ctypes.c_void_p,)
    api.SherpaOnnxSpeakerEmbeddingExtractorDim.restype = ctypes.c_int32
    api.SherpaOnnxSpeakerEmbeddingExtractorCreateStream.argtypes = (ctypes.c_void_p,)
    api.SherpaOnnxSpeakerEmbeddingExtractorCreateStream.restype = ctypes.c_void_p
    api.SherpaOnnxOnlineStreamAcceptWaveform.argtypes = (
        ctypes.c_void_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_float), ctypes.c_int32,
    )
    api.SherpaOnnxOnlineStreamAcceptWaveform.restype = None
    api.SherpaOnnxOnlineStreamInputFinished.argtypes = (ctypes.c_void_p,)
    api.SherpaOnnxOnlineStreamInputFinished.restype = None
    api.SherpaOnnxSpeakerEmbeddingExtractorIsReady.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p,
    )
    api.SherpaOnnxSpeakerEmbeddingExtractorIsReady.restype = ctypes.c_int32
    api.SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p,
    )
    api.SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding.restype = ctypes.POINTER(
        ctypes.c_float
    )
    api.SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding.argtypes = (
        ctypes.POINTER(ctypes.c_float),
    )
    api.SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding.restype = None
    api.SherpaOnnxDestroyOnlineStream.argtypes = (ctypes.c_void_p,)
    api.SherpaOnnxDestroyOnlineStream.restype = None


def _load(library_dir: Path) -> ctypes.CDLL:
    # onnxruntime 은 C API 가 정적으로 품고 있을 수도 있다 — 있으면 먼저 전역으로 얹고, 없으면
    # 그대로 간다. 진짜 실패는 C API 를 못 열 때이고 그때만 거부한다.
    try:
        ctypes.CDLL(str(library_dir / RUNTIME_LIBRARY), mode=ctypes.RTLD_GLOBAL)
    except OSError:
        pass
    try:
        api = ctypes.CDLL(str(library_dir / C_API_LIBRARY))
    except OSError as failure:
        raise VoiceprintError(f"VOICEPRINT-LIB-FAIL {type(failure).__name__}: {failure}") from None
    try:
        _declare(api)
    except AttributeError as failure:
        raise VoiceprintError(f"VOICEPRINT-LIB-FAIL 심볼이 없습니다: {failure}") from None
    return api


class Extractor:
    """모델 하나를 열어 여러 구간의 임베딩을 뽑는다. 핸들은 컨텍스트 매니저로 닫는다."""

    def __init__(self, toolchain: Toolchain) -> None:
        self._api = _load(toolchain.library_dir)
        self._model = str(toolchain.model).encode("utf-8")
        self._provider = b"cpu"
        config = _ExtractorConfig(
            model=self._model, num_threads=toolchain.threads, debug=0, provider=self._provider
        )
        handle = self._api.SherpaOnnxCreateSpeakerEmbeddingExtractor(ctypes.byref(config))
        if not handle:
            raise VoiceprintError(
                f"VOICEPRINT-LIB-FAIL 임베딩 모델을 열지 못했습니다: {toolchain.model.name}"
            )
        self._handle = ctypes.c_void_p(handle)
        self.dim = int(self._api.SherpaOnnxSpeakerEmbeddingExtractorDim(self._handle))

    def __enter__(self) -> Extractor:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def compute(self, samples: array.array[float]) -> tuple[float, ...]:
        stream = self._api.SherpaOnnxSpeakerEmbeddingExtractorCreateStream(self._handle)
        if not stream:
            raise VoiceprintError("VOICEPRINT-STREAM-FAIL 스트림을 만들지 못했습니다")
        stream = ctypes.c_void_p(stream)
        try:
            buffer = (ctypes.c_float * len(samples)).from_buffer_copy(samples)
            self._api.SherpaOnnxOnlineStreamAcceptWaveform(
                stream, SAMPLE_RATE, buffer, len(samples)
            )
            self._api.SherpaOnnxOnlineStreamInputFinished(stream)
            if not self._api.SherpaOnnxSpeakerEmbeddingExtractorIsReady(self._handle, stream):
                raise VoiceprintError("VOICEPRINT-NOT-READY 구간이 모델에 너무 짧습니다")
            vector = self._api.SherpaOnnxSpeakerEmbeddingExtractorComputeEmbedding(
                self._handle, stream
            )
            if not vector:
                raise VoiceprintError("VOICEPRINT-EMBEDDING-FAIL 빈 임베딩")
            try:
                return tuple(float(vector[index]) for index in range(self.dim))
            finally:
                self._api.SherpaOnnxSpeakerEmbeddingExtractorDestroyEmbedding(vector)
        finally:
            self._api.SherpaOnnxDestroyOnlineStream(stream)

    def close(self) -> None:
        handle, self._handle = getattr(self, "_handle", None), None
        if handle is not None:
            self._api.SherpaOnnxDestroySpeakerEmbeddingExtractor(handle)


def main(
    argv: list[str] | None = None,
    *,
    extractor_factory: object = Extractor,
) -> int:
    parser = argparse.ArgumentParser(prog="stt_voiceprint.py", description="등록된 목소리 임베딩")
    parser.add_argument("--job", type=Path, required=True, help="추출 요청 JSON")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        payload = json.loads(args.job.read_text(encoding="utf-8"))
        toolchain = Toolchain(
            library_dir=Path(str(payload["library_dir"])).expanduser(),
            model=Path(str(payload["model"])).expanduser(),
            threads=int(payload.get("threads") or 1),
        )
        jobs = payload["jobs"]
        if not isinstance(jobs, list) or not jobs:
            raise ValueError("jobs 가 비어 있습니다")
        digest = model_digest(toolchain.model)
    except (OSError, TypeError, ValueError, KeyError) as failure:
        print(f"VOICEPRINT-JOB-INVALID {type(failure).__name__}", file=sys.stderr)
        return 2
    try:
        extractor = extractor_factory(toolchain)  # type: ignore[operator]
    except VoiceprintError as failure:
        print(str(failure), file=sys.stderr)
        return 1
    embeddings: dict[str, list[float]] = {}
    failed: dict[str, str] = {}
    try:
        dim = int(extractor.dim)
        for job in jobs:
            identifier = str(job.get("id", ""))
            try:
                samples = read_pcm(Path(str(job["wav"])).expanduser(), job.get("spans"))
                embeddings[identifier] = list(extractor.compute(samples))
            except (VoiceprintError, OSError, TypeError, ValueError, KeyError, wave.Error) as failure:
                failed[identifier] = f"{type(failure).__name__}: {failure}"[:200]
    finally:
        extractor.close()
    print(
        json.dumps(
            {"dim": dim, "model_sha256": digest, "embeddings": embeddings, "failed": failed},
            ensure_ascii=False,
        )
    )
    return 0 if embeddings else 1


if __name__ == "__main__":
    sys.exit(main())
