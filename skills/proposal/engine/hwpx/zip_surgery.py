from __future__ import annotations

import io
import re
import zlib
import zipfile
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import BinaryIO, Final


PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_SEED_PATH: Final = PROJECT_ROOT / "resource" / "R&D 연구계획서 양식.hwpx"

MIMETYPE_ENTRY: Final = "mimetype"
HWPX_MIMETYPE: Final = b"application/hwp+zip"
ZIP_EPOCH: Final = (1980, 1, 1, 0, 0, 0)
NEW_ENTRY_EXTERNAL_ATTR: Final = 2172649504
NEW_ENTRY_CREATE_SYSTEM: Final = 11


class HwpxEntries(OrderedDict[str, bytes]):
    def __init__(
        self,
        entries: Mapping[str, bytes] | None = None,
        *,
        compress_types: Mapping[str, int] | None = None,
        external_attrs: Mapping[str, int] | None = None,
        create_systems: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(entries or {})
        self.compress_types: dict[str, int] = dict(compress_types or {})
        self.external_attrs: dict[str, int] = dict(external_attrs or {})
        self.create_systems: dict[str, int] = dict(create_systems or {})


def unpack(path: str) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as hwpx:
        infos = hwpx.infolist()
        entries = OrderedDict((info.filename, hwpx.read(info.filename)) for info in infos)
        return HwpxEntries(
            entries,
            compress_types={info.filename: info.compress_type for info in infos},
            external_attrs={info.filename: info.external_attr for info in infos},
            create_systems={info.filename: info.create_system for info in infos},
        )


LINE_LAYOUT_CACHE_RE: Final = re.compile(
    rb"<hp:linesegarray\b[^>]*/>|<hp:linesegarray\b[^>]*>.*?</hp:linesegarray>",
    re.DOTALL,
)


def is_section_entry(name: str) -> bool:
    return name.startswith("Contents/section") and name.endswith(".xml")


def strip_line_layout_cache(entries: dict[str, bytes]) -> dict[str, bytes]:
    """Drop every <hp:linesegarray> from the section entries about to be written.

    It is Hangul's per-paragraph cache of where each line of the OLD text sat, and
    Hangul trusts it over its own layout engine. Replacing text is not the only way
    to make it stale: deleting or inserting paragraphs moves every later paragraph
    to a different page, so a paragraph whose own text never changed still points at
    coordinates that no longer exist. The document then opens with lines crammed
    together and the letter spacing ignored until someone forces a relayout.
    Dropping it costs nothing — Hangul recomputes exact layout on open.
    """
    changed = entries
    for name, payload in list(entries.items()):
        if not is_section_entry(name):
            continue
        stripped = LINE_LAYOUT_CACHE_RE.sub(b"", payload)
        if stripped != payload:
            changed = replace_entry(changed, name, stripped)
    return changed


def repack(entries: dict[str, bytes], out: str) -> None:
    _repack(strip_line_layout_cache(entries), out)


def _repack(entries: dict[str, bytes], out: str | BinaryIO) -> None:
    with zipfile.ZipFile(out, mode="w") as hwpx:
        for name, payload in _ordered_for_hwpx(entries).items():
            info = _zip_info_for(name, payload, entries)
            hwpx.writestr(info, payload)


def roundtrip(seed_path: str) -> bytes:
    entries = unpack(seed_path)
    output = io.BytesIO()
    _repack(entries, output)
    return output.getvalue()


def replace_entry(entries: dict[str, bytes], name: str, new_bytes: bytes) -> dict[str, bytes]:
    if name not in entries:
        raise KeyError(name)

    replaced = OrderedDict((entry_name, new_bytes if entry_name == name else payload) for entry_name, payload in entries.items())
    if isinstance(entries, HwpxEntries):
        return HwpxEntries(
            replaced,
            compress_types=entries.compress_types,
            external_attrs=entries.external_attrs,
            create_systems=entries.create_systems,
        )
    return replaced


def get_entry_compression(seed_path: str) -> dict[str, int]:
    with zipfile.ZipFile(seed_path) as hwpx:
        return {info.filename: info.compress_type for info in hwpx.infolist()}


def _ordered_for_hwpx(entries: Mapping[str, bytes]) -> MutableMapping[str, bytes]:
    if MIMETYPE_ENTRY not in entries:
        raise KeyError(MIMETYPE_ENTRY)

    ordered: OrderedDict[str, bytes] = OrderedDict([(MIMETYPE_ENTRY, entries[MIMETYPE_ENTRY])])
    for name, payload in entries.items():
        if name != MIMETYPE_ENTRY:
            ordered[name] = payload
    return ordered


def _zip_info_for(name: str, payload: bytes, entries: dict[str, bytes]) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_EPOCH)
    info.compress_type = _compress_type_for(name, entries)
    info.extra = b""
    info.create_system = _create_system_for(name, entries)
    info.external_attr = _external_attr_for(name, entries)
    info.file_size = len(payload)
    info.CRC = zlib.crc32(payload) & 0xFFFFFFFF
    return info


def _compress_type_for(name: str, entries: dict[str, bytes]) -> int:
    if name == MIMETYPE_ENTRY:
        return zipfile.ZIP_STORED
    if isinstance(entries, HwpxEntries) and name in entries.compress_types:
        return entries.compress_types[name]
    return zipfile.ZIP_STORED


def _external_attr_for(name: str, entries: dict[str, bytes]) -> int:
    if isinstance(entries, HwpxEntries) and name in entries.external_attrs:
        return entries.external_attrs[name]
    return _default_seed_external_attrs().get(name, NEW_ENTRY_EXTERNAL_ATTR)


def _create_system_for(name: str, entries: dict[str, bytes]) -> int:
    if isinstance(entries, HwpxEntries) and name in entries.create_systems:
        return entries.create_systems[name]
    return _default_seed_create_systems().get(name, NEW_ENTRY_CREATE_SYSTEM)


def _default_seed_external_attrs() -> dict[str, int]:
    with zipfile.ZipFile(DEFAULT_SEED_PATH) as hwpx:
        return {info.filename: info.external_attr for info in hwpx.infolist()}


def _default_seed_create_systems() -> dict[str, int]:
    with zipfile.ZipFile(DEFAULT_SEED_PATH) as hwpx:
        return {info.filename: info.create_system for info in hwpx.infolist()}
