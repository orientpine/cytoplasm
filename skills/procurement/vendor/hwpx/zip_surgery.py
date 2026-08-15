"""Offline archive surgery support for the vendored HWPX slot filler."""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

SECTION_XML = "Contents/section0.xml"


def xml_escape(value: str) -> str:
    """Escape text for inclusion in an HWPX `<hp:t>` element."""
    return escape(value, quote=True).replace("&#x27;", "&apos;")


@dataclass(slots=True)
class HwpxSurgeon:
    """Preserve every archive member while replacing only the HWPX section XML."""

    input_path: Path
    _members: tuple[zipfile.ZipInfo, ...] = field(init=False)
    _payloads: dict[str, bytes] = field(init=False)
    section_bytes: bytes = field(init=False)

    def __post_init__(self) -> None:
        with zipfile.ZipFile(self.input_path) as archive:
            self._members = tuple(archive.infolist())
            self._payloads = {item.filename: archive.read(item.filename) for item in self._members}
        self.section_bytes = self._payloads[SECTION_XML]

    def replace_text(self, replacements: dict[str, str]) -> None:
        """Apply an exact section-text replacement requested by the filler."""
        section = self.section_bytes.decode("utf-8")
        for old, new in replacements.items():
            section = section.replace(old, new)
        self.section_bytes = section.encode("utf-8")
        self._payloads[SECTION_XML] = self.section_bytes

    def save(self, output_path: Path) -> Path:
        """Write archive metadata and member order deterministically from the input."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w") as archive:
            for member in self._members:
                archive.writestr(member, self._payloads[member.filename], compress_type=member.compress_type)
        return output_path


def validate_surgery(source_path: Path, output_path: Path) -> list[str]:
    """Validate the rewritten section and unchanged archive-member inventory."""
    errors: list[str] = []
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(output_path) as output:
        if source.namelist() != output.namelist():
            errors.append("archive member list changed")
        try:
            _ = ET.fromstring(output.read(SECTION_XML))
        except (ET.ParseError, KeyError) as error:
            errors.append(f"section XML invalid: {error}")
        for member in source.namelist():
            if member != SECTION_XML and source.read(member) != output.read(member):
                errors.append(f"unchanged member modified: {member}")
    return errors
