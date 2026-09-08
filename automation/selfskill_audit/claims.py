"""Advisory scan for self-skills that claim an approval-review role.

A bare approval token in the SKILL.md body is sufficient today: the governed corpus
regression covers every shipped SKILL.md and is silent. If a governed skill later
legitimately needs one token, tighten this rule to require an approval kind or two
independent token classes rather than suppressing the advisory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.selfskill_audit.scan import _bundled_names, _skill_dirs

_APPROVAL_TOKENS: Final = (
    "[release]",
    "[skill-deploy]",
    "[skill-publish]",
    "DO-NOT-APPROVE",
    "승인 보류",
)


@dataclass(frozen=True, slots=True)
class ApprovalClaimHit:
    skill_name: str
    tokens: tuple[str, ...]


def _body(document: str) -> str:
    """Return the Markdown body, excluding a conventional YAML frontmatter block."""
    if document.startswith("---\n"):
        _, separator, body = document.partition("\n---\n")
        if separator:
            return body
    return document


def _matched_tokens(skill_md: Path) -> tuple[str, ...]:
    try:
        body = _body(skill_md.read_text(encoding="utf-8")).casefold()
    except (OSError, UnicodeDecodeError):
        return ()
    try:
        from automation.interop.approval_surface import ApprovalSurface
    except ImportError:
        approval_channel = ""
    else:
        approval_channel = f"#{ApprovalSurface.SKILL_APPROVALS.value.removeprefix('skill-')}"
    tokens = (*_APPROVAL_TOKENS, approval_channel)
    return tuple(token for token in tokens if token and token.casefold() in body)


def find_approval_claims(home: Path) -> tuple[ApprovalClaimHit, ...]:
    """Find non-bundled self-skills whose Markdown body claims approval review work."""
    skills_root = home / ".hermes" / "skills"
    if not skills_root.is_dir():
        return ()
    bundled = _bundled_names(skills_root)
    hits = tuple(
        ApprovalClaimHit(skill_dir.name, tokens)
        for skill_dir in _skill_dirs(skills_root)
        if skill_dir.name not in bundled
        if (tokens := _matched_tokens(skill_dir / "SKILL.md"))
    )
    return tuple(sorted(hits, key=lambda hit: hit.skill_name))
