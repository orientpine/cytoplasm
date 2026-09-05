"""Join timed transcription, speaker naming, and the meeting child response."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

import stt_blocks
import stt_client
import stt_correction_log
import stt_polish
import stt_runtime
import stt_speakers
import stt_transcript


class TranscriptionLike(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def sentences(self) -> tuple[stt_blocks.TimedSentence, ...]: ...


def tidy(
    transcription: TranscriptionLike,
    glossary: stt_polish.Glossary,
    override: stt_speakers.SpeakerMap = (),
) -> tuple[stt_polish.Polished, stt_speakers.SpeakerMap]:
    """Tidy speech, infer self-introductions, and render attributed headers."""
    sentences = transcription.sentences or stt_blocks.parse(transcription.text)
    polished = stt_polish.polish_sentences(sentences, glossary=glossary)
    known_names = tuple(right for _wrong, right in glossary)
    rule = stt_speakers.infer(polished.timed, known_names=known_names)
    speakers = stt_speakers.merge(override, rule)
    named = stt_polish.polish_sentences(
        polished.timed, names=stt_speakers.names(speakers)
    )
    # Preserve the correction receipt from the first pass; the second pass only renders names.
    polished = stt_polish.Polished(
        body=named.body,
        sentences=polished.sentences,
        paragraphs=named.paragraphs,
        collapsed=polished.collapsed,
        substitutions=polished.substitutions,
        blocks=named.blocks,
        timed=named.timed,
        corrections=polished.corrections,
    )
    return polished, speakers


def _ordered(speakers: stt_speakers.SpeakerMap) -> stt_speakers.SpeakerMap:
    return tuple(sorted(speakers, key=lambda speaker: int(speaker.label[2:])))


def legend_lines(speakers: stt_speakers.SpeakerMap) -> tuple[str, ...]:
    legend = stt_speakers.render_legend(_ordered(speakers))
    return (legend,) if legend else ()


def speaker_summary(speakers: stt_speakers.SpeakerMap) -> list[dict[str, str]]:
    return [
        {"label": speaker.label, "name": speaker.name, "source": speaker.source}
        for speaker in _ordered(speakers)
    ]


def run_summary(
    transcription: stt_client.Transcription,
    polished: stt_polish.Polished,
    speakers: stt_speakers.SpeakerMap,
    *,
    label: str,
    project: str,
    source: str,
    transcript: Path,
    drive_link: str,
) -> dict[str, object]:
    coverage = transcription.coverage
    return {
        "label": label,
        "project": project,
        "source": source,
        "transcript_path": str(transcript),
        "model": transcription.model,
        "chars": len(transcription.text),
        "polish": stt_runtime.polish_summary(polished),
        "coverage": ({
            "ratio": round(coverage.ratio, 4),
            "complete": coverage.complete,
            "duration_ms": coverage.duration_ms,
            "gaps": len(coverage.gaps),
        } if coverage is not None else None),
        "drive_link": drive_link,
        "meeting_exit": None,
        "speakers": speaker_summary(speakers),
        "diarized": any(sentence.speaker for sentence in polished.timed),
    }


def meeting_argv(
    label: str,
    project: str,
    *,
    offline: bool,
    with_evidence: bool,
    notify_channel: str | None,
    notify_message_id: str | None,
    recorded: str | None,
) -> list[str]:
    argv = ["--label", label]
    for flag, value in (
        ("--project", project),
        ("--notify-channel", notify_channel),
        ("--notify-message-id", notify_message_id),
        ("--recorded-response", recorded),
    ):
        if value:
            argv += [flag, value]
    if offline:
        argv.append("--offline")
    if with_evidence:
        argv.append("--with-evidence")
    return argv


def run_meeting(
    transcript: Path, argv: Sequence[str]
) -> tuple[int, stt_speakers.SpeakerMap]:
    """Run meeting, mirror its captured output, and accept its final JSON object."""
    command = [
        sys.executable,
        str(stt_runtime.meeting_cli_path()),
        "ingest",
        "--file",
        str(transcript),
        *argv,
    ]
    completed = subprocess.run(  # noqa: S603 - configured local meeting entry point
        command,
        capture_output=True,
        check=False,
        env=stt_runtime.child_env(),
        text=True,
        timeout=1800,
    )
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    payload = _last_json_object(completed.stdout)
    raw_speakers = payload.get("speakers", []) if payload is not None else []
    items = raw_speakers if isinstance(raw_speakers, list) else []
    mappings = tuple(item for item in items if isinstance(item, Mapping))
    return completed.returncode, stt_speakers.parse_llm(mappings)


def _last_json_object(output: str) -> dict[str, object] | None:
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def absorb(
    transcript_path: Path,
    llm: stt_speakers.SpeakerMap,
    label: str,
    project: str,
    now: datetime,
) -> stt_speakers.SpeakerMap:
    """Merge meeting names back into the one canonical transcript, if changed."""
    document = transcript_path.read_text(encoding="utf-8")
    header, body = stt_polish.split_document(document)
    current = stt_speakers.parse_legend(header)
    owners = tuple(speaker for speaker in current if speaker.source == "소유자")
    merged = _ordered(stt_speakers.merge(owners, current, llm))
    if stt_speakers.render_legend(merged) == stt_speakers.render_legend(current):
        return merged
    polished = stt_polish.polish_sentences(
        stt_blocks.parse(body),
        glossary=stt_runtime.merged_glossary(project),
        names=stt_speakers.names(merged),
    )
    stt_correction_log.record(polished.corrections, label=label, project=project, stage="absorb")
    transcript_path.write_text(
        stt_transcript.rewrite(
            header,
            polished,
            label=label,
            extra_lines=legend_lines(merged),
            managed_prefixes=(stt_transcript.TIDY_PREFIX, stt_speakers.SPEAKERS_PREFIX),
        ),
        encoding="utf-8",
    )
    transcript_path.chmod(stt_transcript.FILE_MODE)
    stt_runtime.publish_transcript(transcript_path, label, now, project)
    return merged
