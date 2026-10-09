from __future__ import annotations

import json
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import cast
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class RawDoc:
    path: str
    source_type: str
    raw_text: str
    meta: dict[str, object]


SUPPORTED_EXTENSIONS = {".md", ".txt", ".eml", ".docx", ".pdf", ".hwpx", ".json"}
HWPX_HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _parse_front_matter(text: str) -> tuple[str, dict[str, object]]:
    """Parse optional YAML-like front-matter block. Returns (body, meta)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text, {}
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return text, {}
    meta: dict[str, object] = {}
    for line in lines[1:close_idx]:
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "source_url":
                meta["source_url"] = value
    body = "\n".join(lines[close_idx + 1 :]).strip()
    return body, meta


def ingest_md(path: str) -> RawDoc:
    source = Path(path)
    source_type = source.suffix.lower().lstrip(".")
    if source_type not in {"md", "txt"}:
        source_type = "md"
    raw_full = source.read_text(encoding="utf-8")
    raw_text, meta = _parse_front_matter(raw_full)
    if not raw_text:
        raw_text = raw_full
        meta = {}
    return RawDoc(
        path=str(source),
        source_type=source_type,
        raw_text=raw_text,
        meta=meta,
    )


def ingest_eml(path: str) -> RawDoc:
    source = Path(path)
    with source.open("rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)

    body = _email_text_body(message)
    latest_lines: list[str] = []
    for line in body.split("\n"):
        if line.strip().startswith(">"):
            continue
        if _is_reply_boundary(line):
            break
        latest_lines.append(line)

    raw_text = "\n".join(latest_lines).strip()
    return RawDoc(
        path=str(source),
        source_type="eml",
        raw_text=raw_text,
        meta={
            "from": message.get("From", ""),
            "to": message.get("To", ""),
            "date": message.get("Date", ""),
        },
    )


def _document_text(source: Path) -> str:
    """파일→본문은 `automation/document_text.py` 가 이 저장소의 단일 정의다.

    마운트된 스킬은 `automation` 을 자동으로 찾지 못하므로 import 를 함수 안에 둔다 —
    모듈 import 는 어디서든 성공하고, 해석 실패는 docx·pdf 를 실제로 읽을 때만 난다.
    """
    from automation.document_text import OK, extract_document

    extracted = extract_document(source)
    if extracted.status != OK:
        raise ValueError(f"{source.name}: {extracted.status}")
    return extracted.text


def ingest_docx(path: str) -> RawDoc:
    source = Path(path)
    return RawDoc(
        path=str(source), source_type="docx", raw_text=_document_text(source), meta={}
    )


def ingest_pdf(path: str) -> RawDoc:
    source = Path(path)
    try:
        raw_text = _document_text(source)
    except Exception as exc:
        sidecar = source.with_suffix(".txt")
        if not sidecar.exists():
            raise
        return RawDoc(
            path=str(source),
            source_type="pdf",
            raw_text=sidecar.read_text(encoding="utf-8"),
            meta={"fallback": "txt_sidecar", "error": str(exc)},
        )

    return RawDoc(path=str(source), source_type="pdf", raw_text=raw_text, meta={})


def ingest_hwpx(path: str) -> RawDoc:
    source = Path(path)
    with tempfile.TemporaryDirectory() as tmpdir:
        unpack_root = _unpack_hwpx(source, Path(tmpdir))
        if isinstance(unpack_root, Mapping):
            section_payload = _section0_payload(unpack_root)
            root = ET.fromstring(section_payload)
        else:
            section_path = _find_section0(unpack_root)
            root = ET.parse(section_path).getroot()
        texts = [node.text or "" for node in root.iter(f"{{{HWPX_HP_NS}}}t")]
        section_text = "\n".join(texts)
        raw_text = f"## section0\n{section_text}".strip()

    return RawDoc(
        path=str(source),
        source_type="hwpx",
        raw_text=raw_text,
        meta={"section": "section0.xml"},
    )


def ingest_transcript(path: str) -> RawDoc:
    source = Path(path)
    segments = cast(
        Sequence[Mapping[str, object]],
        json.loads(source.read_text(encoding="utf-8")),
    )
    raw_text = "".join(f"[{seg['speaker']}] {seg['text']}\n" for seg in segments)
    speakers = sorted({str(seg["speaker"]) for seg in segments})
    return RawDoc(
        path=str(source),
        source_type="transcript",
        raw_text=raw_text,
        meta={"speakers": speakers},
    )


def ingest_path(path: str) -> RawDoc:
    source = Path(path)
    extension = source.suffix.lower()
    if extension in {".md", ".txt"}:
        return ingest_md(str(source))
    if extension == ".eml":
        return ingest_eml(str(source))
    if extension == ".docx":
        return ingest_docx(str(source))
    if extension == ".pdf":
        return ingest_pdf(str(source))
    if extension == ".hwpx":
        return ingest_hwpx(str(source))
    if extension == ".json":
        return ingest_transcript(str(source))
    raise ValueError(f"Unsupported source extension: {extension}")


def ingest_dir(dir: str) -> list[RawDoc]:
    root = Path(dir)
    docs: list[RawDoc] = []
    for source in sorted((item for item in root.iterdir() if item.is_file()), key=lambda item: item.name):
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        docs.append(ingest_path(str(source)))
    return docs


def _email_text_body(message: EmailMessage) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                return _decode_email_part(part)
        return ""
    return _decode_email_part(message)


def _decode_email_part(part: EmailMessage) -> str:
    payload = cast(bytes | None, part.get_payload(decode=True))
    if payload is not None:
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    content = cast(object, part.get_content())
    if isinstance(content, str):
        return content
    return str(content)


def _is_reply_boundary(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("-----Original Message-----") or (
        stripped.startswith("On ") and stripped.endswith(" wrote:")
    )


def _unpack_hwpx(source: Path, destination: Path) -> Path | Mapping[str, object]:
    unpack = _resolve_unpack()
    if unpack is None:
        with zipfile.ZipFile(source) as archive:
            archive.extractall(destination)
        return destination

    try:
        result = unpack(str(source), str(destination))
    except TypeError:
        result = unpack(str(source))

    if result is None:
        return destination
    if isinstance(result, Mapping):
        return cast(Mapping[str, object], result)
    return Path(str(result))


def _resolve_unpack() -> Callable[..., object] | None:
    from ..hwpx.zip_surgery import unpack

    return unpack


def _section0_payload(entries: Mapping[str, object]) -> bytes:
    for name in ("Contents/section0.xml", "section0.xml"):
        if name in entries:
            payload = entries[name]
            return payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
    for name, payload in entries.items():
        if str(name).endswith("section0.xml"):
            return payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
    raise FileNotFoundError("section0.xml not found in HWPX entries")


def _find_section0(root: Path) -> Path:
    direct_candidates = [root / "Contents" / "section0.xml", root / "section0.xml"]
    for candidate in direct_candidates:
        if candidate.exists():
            return candidate
    for candidate in root.rglob("section0.xml"):
        return candidate
    raise FileNotFoundError(f"section0.xml not found under {root}")
