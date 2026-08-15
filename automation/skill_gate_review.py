from __future__ import annotations

import re
import stat
from pathlib import Path
from typing import Final

_REQUIRED_CHECKS: Final = ("content_digest", "frontmatter", "scenario", "secret_scan")
_VERDICT_LINE: Final = re.compile(
    "".join(
        (
            r'^\{"checks": \{"content_digest": (?P<content_digest>true|false), ',
            r'"frontmatter": (?P<frontmatter>true|false), "scenario": (?P<scenario>true|false), ',
            r'"secret_scan": (?P<secret_scan>true|false)\}, "hash": ',
            r'"(?P<digest>[0-9a-f]{64}|unavailable)", "skill": "(?P<skill>[a-z0-9][a-z0-9-]{1,40})", ',
            r'"timestamp": "[^\"]+", "verdict": "(?P<verdict>PASS|FAIL)"\}$',
        )
    )
)
_PASS_LINE: Final = "- review: ✅ PASS (frontmatter/scenario/secret_scan/content_digest 4/4, sha256-bound)"
_BLOCK_LINE: Final = "- review: ❌ 미검토/FAIL — 승인 금지"


def review_status_line(verdicts_path: Path, skill: str, digest: str) -> str:
    return _PASS_LINE if _has_matching_pass(verdicts_path, skill, digest) else _BLOCK_LINE


def _has_matching_pass(verdicts_path: Path, skill: str, digest: str) -> bool:
    try:
        file_mode = stat.S_IMODE(verdicts_path.stat().st_mode)
        lines = verdicts_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    if file_mode != 0o600:
        return False
    matching_pass = False
    for line in lines:
        matched = _VERDICT_LINE.fullmatch(line)
        if matched is None:
            return False
        if matched.group("skill") == skill and matched.group("digest") == digest:
            matching_pass = matched.group("verdict") == "PASS" and all(
                matched.group(check) == "true" for check in _REQUIRED_CHECKS
            )
    return matching_pass
