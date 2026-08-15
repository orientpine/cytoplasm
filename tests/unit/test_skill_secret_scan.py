"""Every skill must pass the deploy gate's secret scan, before a deploy discovers it.

`automation/skill_review.py` fails a deploy at stage 2 when any shipped file matches
its secret patterns. One of those patterns looks for `Bot `/`Bearer ` followed by a
token containing a digit or a dot -- so ordinary English like "the calendar bot
identity." matches, and a docstring blocks a production deploy with `secret_scan:
false` and no indication of which file or phrase caused it.

`AGENTS.md` warns about this in prose ("토큰 모양 문자열을 주석/문서에도 사용 —
secret-scan 오탐이 배포를 막는다") but nothing enforced it, so three skills shipped
the phrase and were only caught when their deploys failed. This test moves the
finding to the second it is introduced, and names the file, line and fragment.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[2]
SKILLS: Final = ROOT / "skills"
REVIEW: Final = ROOT / "automation" / "skill_review.py"


def _review_module():
    """`skill_review` loaded by path; it is a script, not an importable package member."""
    spec = importlib.util.spec_from_file_location("skill_review_under_test", REVIEW)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass(slots=True) resolves its own module during class creation.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_skill_passes_the_deploy_secret_scan() -> None:
    # Given: the same scanner and the same file set the deploy gate uses.
    review = _review_module()

    # When: every skill directory is scanned.
    findings: list[str] = []
    for skill in sorted(path for path in SKILLS.iterdir() if path.is_dir()):
        if review._secret_scan_passes(skill):
            continue
        for shipped in review._skill_files(skill):
            data = shipped.read_bytes()
            for pattern in review._SECRET_PATTERNS:
                match = pattern.search(data)
                if match is None:
                    continue
                line = data[: match.start()].count(b"\n") + 1
                fragment = data[match.start() : match.end()].decode("utf-8", "replace")
                findings.append(
                    f"{shipped.relative_to(ROOT)}:{line} matches the deploy secret scan: {fragment!r}"
                )

    # Then: no skill can reach a deploy with a scan hit, real or false positive.
    assert not findings, (
        "these would fail stage 2 REVIEW with secret_scan: false and block the deploy. "
        "A false positive is fixed by rewording (avoid 'Bot '/'Bearer ' followed by a "
        "dotted or digit-bearing token); a real secret must never have been committed:\n"
        + "\n".join(findings)
    )
