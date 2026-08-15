"""Minimal XML helpers required by the vendored deterministic form mapper."""
from __future__ import annotations

from typing import Final
import xml.etree.ElementTree as ET

NS: Final = {
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}


def get_text(element: ET.Element | None) -> str:
    """Return visible HWPX text in document order."""
    return "" if element is None else "".join(element.itertext())
