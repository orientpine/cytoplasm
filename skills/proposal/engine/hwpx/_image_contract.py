from __future__ import annotations

import struct
import zlib
from typing import Final


HWP_UNITS_PER_PIXEL_96_DPI: Final = 75
DEFAULT_BODY_WIDTH: Final = 42_520
# 40_500 HWPUNIT ≈ 142.9mm. The earlier 95mm square cap read as too small on the
# printed page and the owner asked for figures half again as large (2026-08-28):
# 27_000 × 1.5 = 40_500, still inside the 150mm text column, and the width cap
# moves with it so a square figure is not silently width-bound below the target.
MAX_DISPLAY_WIDTH: Final = 40_500
MAX_DISPLAY_HEIGHT: Final = 40_500
MAX_U32: Final = 2**32 - 1
PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"


class ImageEmbedError(ValueError):
    """Raised before an invalid or partially linked HWPX can be published."""


def png_dimensions(payload: bytes, identifier: str) -> tuple[int, int]:
    """Validate a PNG chunk stream and return its positive IHDR dimensions."""

    if not payload:
        raise ImageEmbedError(f"empty PNG for {identifier}")
    if len(payload) < len(PNG_SIGNATURE) or payload[:8] != PNG_SIGNATURE:
        raise ImageEmbedError(f"invalid PNG signature for {identifier}")
    if len(payload) < 33:
        raise ImageEmbedError(f"truncated PNG header for {identifier}")

    offset = len(PNG_SIGNATURE)
    chunk_index = 0
    dimensions: tuple[int, int] | None = None
    saw_image_data = False
    saw_end = False
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ImageEmbedError(f"truncated PNG chunk header for {identifier}")
        chunk_length = int.from_bytes(payload[offset : offset + 4], "big")
        chunk_type = payload[offset + 4 : offset + 8]
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + chunk_length
        chunk_end = chunk_data_end + 4
        if chunk_end > len(payload):
            name = chunk_type.decode("ascii", "replace")
            raise ImageEmbedError(f"truncated PNG {name} chunk for {identifier}")

        chunk_data = payload[chunk_data_start:chunk_data_end]
        expected_crc = int.from_bytes(payload[chunk_data_end:chunk_end], "big")
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            name = chunk_type.decode("ascii", "replace")
            raise ImageEmbedError(f"invalid PNG {name} CRC for {identifier}")

        if chunk_index == 0:
            if chunk_type != b"IHDR" or chunk_length != 13:
                raise ImageEmbedError(f"invalid PNG IHDR for {identifier}")
            width, height = struct.unpack(">II", chunk_data[:8])
            if width == 0 or height == 0:
                raise ImageEmbedError(f"PNG dimensions must be positive for {identifier}")
            dimensions = (width, height)
        elif chunk_type == b"IHDR":
            raise ImageEmbedError(f"duplicate PNG IHDR for {identifier}")

        if chunk_type == b"IDAT":
            saw_image_data = True
        if chunk_type == b"IEND":
            if chunk_length != 0:
                raise ImageEmbedError(f"invalid PNG IEND for {identifier}")
            saw_end = True
            if chunk_end != len(payload):
                raise ImageEmbedError(f"unexpected data after PNG IEND for {identifier}")
            break
        offset = chunk_end
        chunk_index += 1

    if dimensions is None:
        raise ImageEmbedError(f"missing PNG IHDR for {identifier}")
    if not saw_image_data:
        raise ImageEmbedError(f"missing PNG IDAT for {identifier}")
    if not saw_end:
        raise ImageEmbedError(f"missing PNG IEND for {identifier}")
    return dimensions
