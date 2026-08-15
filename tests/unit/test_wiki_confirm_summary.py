"""The wiki confirm message may carry an OPTIONAL owner-DM-only summary.

The legacy confirm text is deliberately content-free (``저장 <id> sha256:<hash>``)
because a wiki note may be patent-sensitive and ``skills/AGENTS.md`` forbids
leaking a body/title outside cha's own DM. This suite pins the ONLY relaxation:
an explicitly supplied summary renders when — and only when — the effective
approval surface is the owner DM. Every other surface, and every unresolvable
surface, falls back to the byte-exact legacy line (fail-closed).

The raw ``sha256:<digest>`` substring must survive every rendering: both
``wiki_gate._verify_message_binding`` and ``WikiApprovalGate.probe`` substring
-check it, so dropping it would break every in-flight approval.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))

import wiki_gate  # noqa: E402

from automation.interop.approval_surface import ApprovalSurface  # noqa: E402

CHANNEL_ID = "1526487935975952385"
SUMMARY = "자체 메모리 → 트윈 승격(principle)\n승인 시 자체 메모리에서 삭제됩니다."
NOTE_TEXT = (
    "---\n"
    'title: "Confirm Summary"\n'
    "tags: [test]\n"
    "created: 2026-08-02T00:00:00Z\n"
    "updated: 2026-08-02T00:00:00Z\n"
    "links: []\n"
    "---\n"
    "본문\n"
)


@pytest.fixture
def gate_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WIKI_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path / "wiki"))
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setattr(wiki_gate, "GATE_DIR", tmp_path / "gate")
    return tmp_path / "gate"


def _legacy_line(draft: dict) -> str:
    return f"저장 {draft['id']} sha256:{draft['sha256']}"


def test_confirm_text_without_a_summary_is_unchanged(gate_dir: Path) -> None:
    # REGRESSION PIN: this already passes today — it fails only if the additive
    # change leaks into the summary-less path.
    # Given: a draft created the old way (no summary anywhere).
    draft = wiki_gate.create_draft("create", "no-summary", NOTE_TEXT, CHANNEL_ID)

    # When
    rendered = wiki_gate.confirm_text(draft)

    # Then
    assert rendered == _legacy_line(draft)
    assert "summary" not in draft


def test_confirm_text_with_a_summary_on_owner_dm_includes_it(gate_dir: Path) -> None:
    # Given
    draft = wiki_gate.create_draft(
        "create", "dm-summary", NOTE_TEXT, CHANNEL_ID, summary=SUMMARY
    )

    # When
    rendered = wiki_gate.confirm_text(draft, surface=str(ApprovalSurface.OWNER_DM))

    # Then
    assert rendered.startswith(_legacy_line(draft))
    assert SUMMARY in rendered
    assert f"sha256:{draft['sha256']}" in rendered


def test_confirm_text_with_a_summary_on_a_non_dm_surface_omits_it(gate_dir: Path) -> None:
    # Given: the patent fail-closed guard — a note body never leaves the owner DM.
    draft = wiki_gate.create_draft(
        "create", "approvals-summary", NOTE_TEXT, CHANNEL_ID, summary=SUMMARY
    )

    # When
    rendered = wiki_gate.confirm_text(
        draft, surface=str(ApprovalSurface.SKILL_APPROVALS)
    )

    # Then
    assert rendered == _legacy_line(draft)
    assert SUMMARY not in rendered


def test_confirm_text_when_the_surface_is_unknown_omits_the_summary(gate_dir: Path) -> None:
    # Given: a summary is present but no surface is resolvable anywhere.
    draft = wiki_gate.create_draft(
        "create", "unknown-surface", NOTE_TEXT, CHANNEL_ID, summary=SUMMARY
    )
    assert "surface" not in draft

    # When
    rendered = wiki_gate.confirm_text(draft)

    # Then
    assert rendered == _legacy_line(draft)


def test_confirm_text_caps_the_posted_length(gate_dir: Path) -> None:
    # Given
    draft = wiki_gate.create_draft(
        "create", "long-summary", NOTE_TEXT, CHANNEL_ID, summary="가" * 5000
    )

    # When
    rendered = wiki_gate.confirm_text(draft, surface=str(ApprovalSurface.OWNER_DM))

    # Then
    assert len(rendered) <= 1900
    assert rendered.startswith(_legacy_line(draft))
    assert f"sha256:{draft['sha256']}" in rendered
    assert rendered.endswith("…")
