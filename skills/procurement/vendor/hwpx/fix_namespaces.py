"""Adapted honeypot namespace cleanup for HWPX viewer compatibility."""
from __future__ import annotations

import os
from pathlib import Path
import re
import zipfile

_LINESEG_RE = re.compile(r"\s*<(?P<prefix>[A-Za-z_][\w.-]*):linesegarray\b[^>]*?(?:/>|>.*?</(?P=prefix):linesegarray\s*>)", re.DOTALL)
_STANDARD_PREFIXES = {
    "http://www.hancom.co.kr/hwpml/2011/head": "hh",
    "http://www.hancom.co.kr/hwpml/2011/core": "hc",
    "http://www.hancom.co.kr/hwpml/2011/paragraph": "hp",
    "http://www.hancom.co.kr/hwpml/2011/section": "hs",
}


def fix_hwpx_namespaces(path: Path) -> None:
    """Canonicalize known namespace aliases without changing ZIP timestamps."""
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as output:
        for member in source.infolist():
            data = source.read(member.filename)
            if member.filename.startswith("Contents/") and member.filename.endswith(".xml"):
                text = data.decode("utf-8")
                aliases = {match.group(1): _STANDARD_PREFIXES[match.group(2)] for match in re.finditer(r'xmlns:(ns\d+)="([^"]+)"', text) if match.group(2) in _STANDARD_PREFIXES}
                for old, new in aliases.items():
                    text = text.replace(f"xmlns:{old}=", f"xmlns:{new}=").replace(f"<{old}:", f"<{new}:").replace(f"</{old}:", f"</{new}:")
                data = _LINESEG_RE.sub("", text).encode("utf-8")
            output.writestr(member, data, compress_type=member.compress_type)
    os.replace(temporary, path)
