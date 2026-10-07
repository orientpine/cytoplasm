"""Integer box downscaling of PNG payloads with the standard library only.

A figure generated at 1024x1024 is embedded at the body placement width, so the
artifact carries several times the pixels the page can print. Re-encoding at an
integer factor keeps the aspect ratio exact — the picture geometry read-back
compares declared sizes against the stored PNG — and ``zlib`` is the only codec
needed, because PNG is a zlib stream of filtered scanlines.
"""

from __future__ import annotations

import struct
import zlib
from math import gcd, isqrt
from typing import Final

from ._image_contract import (
    PNG_SIGNATURE,
    ImageEmbedError,
    png_dimensions,
)

# Channel count per PNG colour type; 3 (palette) is absent on purpose — averaging
# palette indexes produces colours nobody chose.
_CHANNELS: Final = {0: 1, 2: 3, 4: 2, 6: 4}
_SUPPORTED_BIT_DEPTH: Final = 8
_MAX_RASTER_BYTES: Final = 128 * 1024 * 1024

__all__ = ["downscale_png"]


def downscale_png(payload: bytes, identifier: str, max_width_px: int) -> bytes:
    """Box-average to an exact integer resolution no narrower than the target.

    The factor is the largest integer that divides both dimensions and keeps the
    result at or above the requested width, so the picture never has to be
    stretched back up on the page. A payload already narrow enough is returned
    unchanged.
    """
    if max_width_px < 1:
        raise ImageEmbedError(f"downscale width must be positive for {identifier}")

    width, height = png_dimensions(payload, identifier)
    factor = _box_factor(width, height, max_width_px)
    if factor == 1:
        return payload

    header, compressed = _read_image_chunks(payload, identifier)
    if len(header) != 13:
        raise ImageEmbedError(f"invalid PNG IHDR for {identifier}")
    bit_depth, colour_type, interlace = header[8], header[9], header[12]
    if bit_depth != _SUPPORTED_BIT_DEPTH or colour_type not in _CHANNELS or interlace != 0:
        raise ImageEmbedError(
            f"cannot downscale {identifier}: only 8-bit non-interlaced PNG colour types "
            f"{sorted(_CHANNELS)} are supported (got depth {bit_depth}, type {colour_type}, "
            f"interlace {interlace})"
        )

    channels = _CHANNELS[colour_type]
    expected = height * (width * channels + 1)
    if expected > _MAX_RASTER_BYTES:
        raise ImageEmbedError(f"PNG raster exceeds decoding limit for {identifier}")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, expected + 1)
    except zlib.error as error:
        raise ImageEmbedError(f"invalid PNG compressed data for {identifier}") from error
    if not decoder.eof:
        raise ImageEmbedError(f"truncated or oversized PNG data for {identifier}")
    rows = _unfilter(raw, width, height, channels, identifier)
    scaled = _box_average(rows, width, height, channels, factor)
    return _encode(scaled, width // factor, height // factor, bit_depth, colour_type)


def _box_factor(width: int, height: int, max_width_px: int) -> int:
    common = gcd(width, height)
    ceiling = width // max_width_px
    best = 1
    for small in range(1, isqrt(common) + 1):
        if common % small:
            continue
        large = common // small
        if large <= ceiling:
            return large
        if small <= ceiling:
            best = small
    return best


def _read_image_chunks(payload: bytes, identifier: str) -> tuple[bytes, bytes]:
    offset = len(PNG_SIGNATURE)
    header = b""
    data = bytearray()
    while offset < len(payload):
        length = int.from_bytes(payload[offset : offset + 4], "big")
        kind = payload[offset + 4 : offset + 8]
        chunk = payload[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            header = chunk
        elif kind == b"IDAT":
            data.extend(chunk)
        elif kind == b"IEND":
            break
        offset += length + 12
    if not header:
        raise ImageEmbedError(f"missing PNG IHDR for {identifier}")
    return header, bytes(data)


def _unfilter(
    raw: bytes, width: int, height: int, channels: int, identifier: str
) -> list[bytearray]:
    stride = width * channels
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ImageEmbedError(
            f"PNG scanline stream for {identifier} is {len(raw)} bytes, expected {expected}"
        )
    rows: list[bytearray] = []
    previous = bytearray(stride)
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        line = bytearray(raw[position + 1 : position + 1 + stride])
        position += stride + 1
        if filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                upper_left = previous[index - channels] if index >= channels else 0
                line[index] = (line[index] + _paeth(left, previous[index], upper_left)) & 0xFF
        elif filter_type != 0:
            raise ImageEmbedError(
                f"unknown PNG filter type {filter_type} in {identifier}"
            )
        rows.append(line)
        previous = line
    return rows


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distance_left = abs(estimate - left)
    distance_above = abs(estimate - above)
    distance_corner = abs(estimate - upper_left)
    if distance_left <= distance_above and distance_left <= distance_corner:
        return left
    if distance_above <= distance_corner:
        return above
    return upper_left


def _box_average(
    rows: list[bytearray], width: int, height: int, channels: int, factor: int
) -> list[bytearray]:
    samples = factor * factor
    half = samples // 2
    scaled: list[bytearray] = []
    for out_y in range(height // factor):
        block = rows[out_y * factor : out_y * factor + factor]
        line = bytearray(width // factor * channels)
        for out_x in range(width // factor):
            base = out_x * factor * channels
            for channel in range(channels):
                total = 0
                for source in block:
                    offset = base + channel
                    for _ in range(factor):
                        total += source[offset]
                        offset += channels
                line[out_x * channels + channel] = (total + half) // samples
        scaled.append(line)
    return scaled


def _encode(
    rows: list[bytearray], width: int, height: int, bit_depth: int, colour_type: int
) -> bytes:
    body = bytearray()
    for line in rows:
        body.append(0)
        body.extend(line)
    header = struct.pack(">IIBBBBB", width, height, bit_depth, colour_type, 0, 0, 0)
    return b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", header),
            _chunk(b"IDAT", zlib.compress(bytes(body), 9)),
            _chunk(b"IEND", b""),
        )
    )


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return len(payload).to_bytes(4, "big") + kind + payload + crc.to_bytes(4, "big")
