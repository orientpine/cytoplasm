"""XML-first DOCX template filling without importing proprietary skill scripts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from skills.procurement.scripts.procure_core import PLACEHOLDER

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
DOCUMENT_XML = "word/document.xml"
W = {"w": W_NS}
ET.register_namespace("w", W_NS)

_DECLARATION = re.compile(rb"^(?P<declaration><\?xml\s+[^?]*\?>)(?P<linebreak>\r\n|\n|\r)?", re.IGNORECASE)
_ENCODING = re.compile(rb"\bencoding\s*=\s*(['\"])(?P<encoding>[^'\"]+)\1", re.IGNORECASE)
_NAMESPACE = re.compile(rb"\s+xmlns(?::(?P<prefix>[A-Za-z_][\w.-]*))?=(?P<quote>['\"])(?P<uri>.*?)(?P=quote)")


@dataclass(frozen=True, slots=True)
class SerializationContext:
    """Original XML declaration and namespace bindings required for safe output."""

    declaration: bytes
    linebreak: bytes
    encoding: str
    root_opening: bytes


class DocxTemplateError(RuntimeError):
    """An external DOCX archive cannot be safely transformed or validated."""


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def _unpack(source: Path, target: Path) -> tuple[zipfile.ZipInfo, ...]:
    members: list[zipfile.ZipInfo] = []
    names: set[str] = set()
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            if member.filename in names:
                raise DocxTemplateError(f"duplicate DOCX member: {member.filename}")
            names.add(member.filename)
            if not _safe_member(member.filename):
                raise DocxTemplateError(f"unsafe DOCX member: {member.filename}")
            if stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK:
                continue
            destination = target / member.filename
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member.filename))
            members.append(member)
    if DOCUMENT_XML not in {member.filename for member in members}:
        raise DocxTemplateError("DOCX lacks word/document.xml")
    return tuple(members)


def _signature(run: ET.Element) -> bytes:
    properties = run.find("w:rPr", W)
    return b"" if properties is None else ET.tostring(properties, encoding="utf-8")


def _text_run(run: ET.Element) -> bool:
    return all(child.tag in {f"{{{W_NS}}}rPr", f"{{{W_NS}}}t"} for child in run)


def _coalesce_runs(root: ET.Element) -> None:
    for paragraph in root.findall(".//w:p", W):
        index = 0
        while index + 1 < len(paragraph):
            current, following = paragraph[index], paragraph[index + 1]
            if current.tag != f"{{{W_NS}}}r" or following.tag != f"{{{W_NS}}}r":
                index += 1
                continue
            if _signature(current) != _signature(following) or not (_text_run(current) and _text_run(following)):
                index += 1
                continue
            target = current.find("w:t", W)
            source = following.find("w:t", W)
            if target is None or source is None:
                index += 1
                continue
            target.text = (target.text or "") + (source.text or "")
            paragraph.remove(following)


def _tokens(root: ET.Element) -> tuple[str, ...]:
    found: list[str] = []
    for paragraph in root.findall(".//w:p", W):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", W))
        for match in PLACEHOLDER.finditer(text):
            field = match[1].strip()
            if field and field not in found:
                found.append(field)
    return tuple(found)


def _replace(root: ET.Element, fields: dict[str, str]) -> None:
    for text_node in root.findall(".//w:t", W):
        original = text_node.text or ""
        filled = PLACEHOLDER.sub(lambda match: fields.get(match[1].strip(), match[0]), original)
        text_node.text = filled
        if filled[:1].isspace() or filled[-1:].isspace():
            text_node.set(f"{{{XML_NS}}}space", "preserve")


def _rezip(root: Path, output: Path, members: tuple[zipfile.ZipInfo, ...]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        for member in members:
            source = root / member.filename
            archive.writestr(member, b"" if member.is_dir() else source.read_bytes(), compress_type=member.compress_type)


def _read_document(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as error:
        raise DocxTemplateError(f"DOCX document.xml invalid: {error}") from None


def _serialization_context(source: bytes) -> SerializationContext:
    """Register source prefixes and retain its exact declaration for reserialization."""
    for match in _NAMESPACE.finditer(source):
        prefix = match["prefix"].decode("ascii") if match["prefix"] else ""
        if prefix != "xml":
            ET.register_namespace(prefix, match["uri"].decode("utf-8"))
    declaration = _DECLARATION.match(source)
    if declaration is None:
        return SerializationContext(b"", b"", "utf-8", _root_opening_tag(source))
    encoding = _ENCODING.search(declaration["declaration"])
    name = encoding["encoding"].decode("ascii") if encoding else "utf-8"
    return SerializationContext(
        declaration["declaration"], declaration["linebreak"] or b"\n", name, _root_opening_tag(source)
    )


def _root_opening_tag(source: bytes) -> bytes:
    """Extract the verbatim root tag while respecting quoted attribute values."""
    start = source.find(b"<w:document")
    if start == -1:
        raise DocxTemplateError("DOCX document.xml lacks a w:document root")
    quote = 0
    for index in range(start + len(b"<w:document"), len(source)):
        byte = source[index]
        if byte in (ord("'"), ord('"')):
            quote = 0 if quote == byte else byte
        elif byte == ord(">") and quote == 0:
            return source[start:index + 1]
    raise DocxTemplateError("DOCX document root opening tag is incomplete")


def _write_document(path: Path, document: ET.Element, context: SerializationContext) -> None:
    """Write the modified XML body without reformating its declaration or prefixes."""
    body = ET.tostring(document, encoding=context.encoding, xml_declaration=False)
    generated = _root_opening_tag(body)
    body = body.replace(generated, context.root_opening, 1)
    path.write_bytes(body if not context.declaration else context.declaration + context.linebreak + body)


def placeholders(template: Path) -> tuple[str, ...]:
    """Read placeholders after applying the documented same-format run coalescing."""
    with tempfile.TemporaryDirectory(prefix="procure-docx-") as temporary:
        root = Path(temporary)
        _unpack(template, root)
        document = _read_document(root / DOCUMENT_XML)
        _coalesce_runs(document)
        return _tokens(document)


def manifest(template: Path) -> dict[str, object]:
    """Build the registry analysis artifact for a reusable DOCX template."""
    return {"format": "docx", "method": "xml-unpack-coalesce-replace-rezip-validate", "placeholders": list(placeholders(template))}


def fill(template: Path, fields: dict[str, str], output: Path) -> None:
    """Unpack, coalesce runs, replace placeholders, rezip, and validate a DOCX."""
    with tempfile.TemporaryDirectory(prefix="procure-docx-") as temporary:
        root = Path(temporary)
        members = _unpack(template, root)
        document_path = root / DOCUMENT_XML
        context = _serialization_context(document_path.read_bytes())
        document = _read_document(document_path)
        _coalesce_runs(document)
        _replace(document, fields)
        _write_document(document_path, document, context)
        _rezip(root, output, members)
    validate(output)


def text_content(path: Path) -> str:
    """Return all Word document text for procurement read-back verification."""
    with tempfile.TemporaryDirectory(prefix="procure-docx-") as temporary:
        root = Path(temporary)
        _unpack(path, root)
        return "\n".join(text for text in _read_document(root / DOCUMENT_XML).itertext() if text)


def validate(path: Path) -> None:
    """Validate a re-zipped DOCX with stdlib ZIP and XML parsers."""
    with tempfile.TemporaryDirectory(prefix="procure-docx-") as temporary:
        root = Path(temporary)
        _unpack(path, root)
        _read_document(root / DOCUMENT_XML)
