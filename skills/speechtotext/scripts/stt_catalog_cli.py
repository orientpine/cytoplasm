"""voice catalog 의 I/O 절반 — 전사본을 읽고, 원음을 받아, ffmpeg 로 잘라, 카탈로그에 적는다.

`propose` 는 읽기 전용이라 에이전트가 스스로 돌려도 된다. `enroll`·`remove` 는 타인의
목소리를 노드에 남기는 일이라 소유자가 이름을 명시해 지시했을 때만 돌린다(SKILL.md
「화자 등록」) — 저장은 `~/.hermes` 아래 로컬이고 음성은 노드 밖으로 나가지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Final

import speechtotext_governed
import stt_blocks
import stt_catalog
import stt_identify_run
import stt_runtime

SOURCE_PREFIX: Final = "- 원본 음성: "
EXIT_REFUSED: Final = 2
EXIT_STALE: Final = 3
EXIT_TOOL: Final = 4


class Refused(RuntimeError):
    pass


def _read_transcript(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    header, marker, body = text.partition("\n---\n")
    return (header, body) if marker else ("", text)


def _recording_id(header: str, transcript: Path) -> str:
    for line in header.splitlines():
        if line.startswith(SOURCE_PREFIX):
            return Path(line[len(SOURCE_PREFIX):].strip()).stem
    return transcript.stem


def _probe_seconds(audio: Path) -> float:
    completed = subprocess.run(  # noqa: S603 - local executable, local file
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(audio)],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(completed.stdout.strip())
    except ValueError:
        raise Refused(f"CATALOG-FFPROBE-FAIL {completed.stderr.strip()[:120]}") from None


def _fetch_from_archive(stems: tuple[str, ...], into: Path) -> Path:
    stem = stems[0]
    stt_runtime.load_secrets_into_environment()
    try:
        manifest = stt_runtime._repo("plaud_sync.audio_manifest")  # noqa: SLF001 - skill-local seam
        outputs = stt_runtime._repo("drive_outputs")  # noqa: SLF001
    except ImportError:
        raise Refused(f"CATALOG-AUDIO-MISSING {stem} (automation 런타임을 찾지 못해 아카이브를 읽을 수 없음)") from None
    try:
        path = manifest.manifest_path(os.environ)
        rows = manifest.rows(path) if path.is_file() else []
    except ValueError as failure:
        raise Refused(f"CATALOG-AUDIO-MISSING {stem} ({failure})") from None
    row = next((item for item in rows if item.get("transcript_stem") in stems), None)
    if row is None:
        raise Refused(f"CATALOG-AUDIO-MISSING {stem} (아카이브 manifest 에 {'/'.join(stems)} 행이 없음 — --audio 로 원음을 주세요)")
    target = into / "remote.bin"
    _ = outputs.client_from_environment().download_file(str(row["drive_file_id"]), target)
    target.chmod(0o600)
    return target


def _load(root: Path) -> stt_catalog.Catalog:
    file = root / stt_catalog.CATALOG_FILE
    return stt_catalog.from_json(file.read_text(encoding="utf-8")) if file.is_file() else stt_catalog.Catalog()


def _save(root: Path, catalog: stt_catalog.Catalog) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    file = root / stt_catalog.CATALOG_FILE
    tmp = file.with_suffix(".tmp")
    tmp.write_text(stt_catalog.to_json(catalog), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(file)


def _cut(audio: Path, intervals: tuple[stt_catalog.Interval, ...], out: Path) -> None:
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    completed = subprocess.run(  # noqa: S603 - local executable, local files
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(audio),
         "-filter_complex", stt_catalog.ffmpeg_filter(intervals), "-map", "[out]",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        raise Refused(f"CATALOG-FFMPEG-FAIL rc={completed.returncode} {completed.stderr.strip()[:160]}")
    out.chmod(0o600)


def _guard() -> int | None:
    message = speechtotext_governed.refusal(Path(__file__))
    if message:
        print(message, file=sys.stderr)
        return EXIT_STALE
    return None


def cmd_propose(args: argparse.Namespace) -> int:
    header, body = _read_transcript(args.transcript)
    sentences = stt_blocks.parse(body)
    duration = args.duration_ms if args.duration_ms else (
        int(_probe_seconds(args.audio) * 1000) if args.audio else None)
    rows = []
    for label, stat in sorted(stt_catalog.speaker_summary(sentences, duration_ms=duration).items()):
        planned = stt_catalog.plan_segments(sentences, label, duration_ms=duration)
        rows.append({"label": label, "blocks": stat.blocks, "speech_ms": stat.speech_ms,
                     "planned_ms": sum(i.length_ms for i in planned),
                     "intervals": [[i.start_ms, i.end_ms] for i in planned]})
        print(f"{label}: 블록 {stat.blocks} · 발화 {stat.speech_ms / 1000:.1f}초 · "
              f"등록 계획 {rows[-1]['planned_ms'] / 1000:.1f}초 ({len(planned)}구간)")
    if duration is None:
        print("(오디오 길이를 몰라 마지막 블록은 계획에서 뺐다 — --audio 또는 --duration-ms)")
    print(json.dumps({"recording_id": _recording_id(header, args.transcript), "speakers": rows},
                     ensure_ascii=False))
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    header, body = _read_transcript(args.transcript)
    sentences = stt_blocks.parse(body)
    recording_id = _recording_id(header, args.transcript)
    root = stt_catalog.catalog_root(os.environ)
    with tempfile.TemporaryDirectory(prefix="voice-catalog-") as scratch:
        audio = args.audio or _fetch_from_archive(
            stt_catalog.transcript_stems(header, args.transcript.stem), Path(scratch))
        duration = int(_probe_seconds(audio) * 1000)
        intervals = stt_catalog.plan_segments(sentences, args.speaker, duration_ms=duration)
        if not intervals:
            raise Refused(f"CATALOG-NO-SEGMENTS {args.speaker} — 그 화자의 {stt_catalog.DEFAULT_MIN_BLOCK_MS / 1000:g}초 이상 블록이 없음")
        wav = root / stt_catalog.SAMPLES_DIR / stt_catalog.sample_filename(args.name, recording_id, args.speaker)
        _cut(audio, intervals, wav)
    sample = stt_catalog.Sample(
        wav=wav.relative_to(root).as_posix(), sha256=hashlib.sha256(wav.read_bytes()).hexdigest(),
        seconds=_probe_seconds(wav), recording_id=recording_id, transcript_stem=args.transcript.stem,
        speaker_label=args.speaker, intervals=intervals,
    )
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    _save(root, stt_catalog.upsert(_load(root), args.name, sample, created_at=now))
    print(json.dumps({"name": args.name, "seconds": sample.seconds, "wav": str(wav),
                      "recording_id": recording_id, "intervals": len(intervals)}, ensure_ascii=False))
    return 0


def _normalize(audio: Path, out: Path) -> None:
    completed = subprocess.run(  # noqa: S603 - local executable, local files
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(audio),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0 or not out.is_file():
        raise Refused(f"CATALOG-FFMPEG-FAIL rc={completed.returncode} {completed.stderr.strip()[:160]}")


def cmd_match(args: argparse.Namespace) -> int:
    """읽기 전용 대조 — 이 전사본의 화자N 이 등록된 누구인지 점수와 함께 보여 준다.

    전사 경로가 같은 판정을 자동으로 하지만(`stt_local`), 소유자가 "이 녹음 누구야" 를
    물을 때 답할 명령이 따로 있어야 한다. 아무것도 쓰지 않으므로 게이트 대상이 아니다.
    """
    header, body = _read_transcript(args.transcript)
    sentences = stt_blocks.parse(body)
    with tempfile.TemporaryDirectory(prefix="voice-match-") as scratch:
        audio = args.audio or _fetch_from_archive(
            stt_catalog.transcript_stems(header, args.transcript.stem), Path(scratch))
        duration = int(_probe_seconds(audio) * 1000)
        wav = Path(scratch) / "input16k.wav"
        _normalize(audio, wav)
        per_label = {
            label: stt_catalog.plan_segments(sentences, label, duration_ms=duration)
            for label in sorted(stt_catalog.speaker_summary(sentences, duration_ms=duration))
        }
        verdicts, table = stt_identify_run.identify_intervals(wav, per_label, env=os.environ)
    for verdict in verdicts:
        others = sorted(
            (score for name, score in table[verdict.label].items() if name != verdict.name),
            reverse=True,
        )
        runner_up = f" · 다음 후보 {others[0]:.2f}" if others else ""
        print(f"{verdict.label}: {verdict.name or '미상'} {verdict.score:.2f}{runner_up} "
              f"→ {verdict.kind}")
    if not verdicts:
        print("(대조할 것이 없습니다 — 등록된 목소리·C API·화자 구간을 확인하세요)")
    print(json.dumps({
        "verdicts": [
            {"label": one.label, "name": one.name, "score": round(one.score, 4),
             "margin": round(one.margin, 4), "kind": one.kind}
            for one in verdicts
        ],
        "scores": {label: {name: round(score, 4) for name, score in row.items()}
                   for label, row in table.items()},
    }, ensure_ascii=False))
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    root = stt_catalog.catalog_root(os.environ)
    catalog = _load(root)
    for person in catalog.people:
        total = sum(sample.seconds for sample in person.samples)
        print(f"{person.name}: 등록본 {len(person.samples)}건 · {total:.1f}초 · 최초 {person.created_at}")
    if not catalog.people:
        print(f"(등록된 목소리 없음 — {root})")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    root = stt_catalog.catalog_root(os.environ)
    catalog = _load(root)
    person = stt_catalog.find(catalog, args.name)
    if person is None:
        raise Refused(f"CATALOG-NOT-FOUND {args.name}")
    for sample in person.samples:
        (root / sample.wav).unlink(missing_ok=True)
    _save(root, stt_catalog.remove(catalog, args.name))
    print(json.dumps({"removed": args.name, "samples": len(person.samples)}, ensure_ascii=False))
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stt_catalog_cli.py", description="등록된 목소리(voice catalog)")
    sub = parser.add_subparsers(dest="command", required=True)
    propose = sub.add_parser("propose", help="전사본의 화자N 별 발화·등록 계획 (읽기 전용)")
    propose.add_argument("transcript", type=Path)
    propose.add_argument("--audio", type=Path)
    propose.add_argument("--duration-ms", type=int)
    enroll = sub.add_parser("enroll", help="화자N 의 발화를 잘라 이름으로 등록 (소유자 지시 필수)")
    enroll.add_argument("--transcript", type=Path, required=True)
    enroll.add_argument("--speaker", required=True)
    enroll.add_argument("--name", required=True)
    enroll.add_argument("--audio", type=Path, help="원음 파일 — 없으면 Drive 아카이브 manifest 에서 받는다")
    match = sub.add_parser("match", help="등록된 목소리와 이 전사본의 화자를 대조 (읽기 전용)")
    match.add_argument("transcript", type=Path)
    match.add_argument("--audio", type=Path, help="원음 파일 — 없으면 Drive 아카이브 manifest 에서 받는다")
    _ = sub.add_parser("list", help="등록된 사람과 등록본 수")
    remove = sub.add_parser("remove", help="등록 취소 (등록본 파일도 삭제)")
    remove.add_argument("--name", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    handlers = {"propose": cmd_propose, "enroll": cmd_enroll, "list": cmd_list,
                "remove": cmd_remove, "match": cmd_match}
    if args.command in {"enroll", "remove"}:
        stale = _guard()
        if stale is not None:
            return stale
    try:
        return handlers[args.command](args)
    except (Refused, stt_catalog.CatalogError) as failure:
        print(str(failure), file=sys.stderr)
        return EXIT_TOOL if str(failure).startswith("CATALOG-FFMPEG") else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
