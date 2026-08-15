"""The approval message must show the change, and never the patch body."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from automation.interop.approval_surface import POLICY_VERSION, ApprovalKind, ApprovalSurface
from automation.repair import repair_approval_render
from automation.repair.repair_approval_render import (
    MAX_VISIBLE_FILES,
    ApprovalRenderError,
    approval_request_content,
)
from automation.repair.repair_patch_binding import PatchFileDelta, content_action_hash

TICKET = "t_repair01"
NONCE = "n" * 32
SOURCE = "/srv/autophagy-private/repair-plans/t_repair01/patch.diff"
LEGACY_HASH = hashlib.sha256(b"repair:t_repair01:patch.diff").hexdigest()

# Frozen on 2026-07-29 from the shipped renderer. Records already posted to
# Discord are compared against this by exact equality, so it must never move.
V1_GOLDEN = (
    "[repair] 승인 요청\n"
    f"- ticket: `{TICKET}`\n"
    f"- sha256: `{LEGACY_HASH}`\n"
    f"- repair_nonce: `{NONCE}`\n"
    "- sandbox: PASS (offline-subset bank + repro GREEN)\n"
    "- cha가 이 메시지에 ✅ 승인 또는 ⛔ 취소 리액션"
)

CHANGES = (
    PatchFileDelta(None, "automation/added.py", 2, 0),
    PatchFileDelta("docs/removed.md", None, 0, 1),
    PatchFileDelta("tests/old_name.py", "tests/new_name.py", 1, 1),
)


@dataclass(frozen=True, slots=True)
class _Record:
    """Minimal stand-in for the persisted record the renderer reads."""

    ticket_id: str = TICKET
    action_hash: str = LEGACY_HASH
    nonce: str = NONCE
    kind: ApprovalKind | None = ApprovalKind.REPAIR
    surface: ApprovalSurface | None = ApprovalSurface.OWNER_DM
    policy_version: int | None = POLICY_VERSION
    content_binding_version: int | None = None
    patch_sha256: str | None = None
    changes: tuple[PatchFileDelta, ...] | None = None
    patch_source_path: str | None = None


def _v2(**overrides: object) -> _Record:
    digest = hashlib.sha256(b"unified diff bytes").hexdigest()
    changes = overrides.pop("changes", CHANGES)
    assert isinstance(changes, tuple)
    fields: dict[str, object] = {
        "action_hash": content_action_hash(TICKET, "patch.diff", digest, changes),
        "content_binding_version": 2,
        "patch_sha256": digest,
        "changes": changes,
        "patch_source_path": SOURCE,
    }
    fields.update(overrides)
    return _Record(**fields)  # pyright: ignore[reportArgumentType]


def test_legacy_record_renders_the_frozen_v1_message_byte_for_byte() -> None:
    # Given: a record written before content binding existed.
    legacy = _Record()

    # When / Then: its already-posted Discord message still matches exactly,
    # so migration cannot paralyse the gate with a binding mismatch.
    assert approval_request_content(legacy) == V1_GOLDEN


def test_v2_shows_the_digest_the_totals_the_per_file_deltas_and_the_private_path() -> None:
    # Given: an approval bound to a three-file patch.
    record = _v2()

    # When: it is rendered for the owner.
    content = approval_request_content(record)

    # Then: everything cha needs to consent to the change is present.
    assert f"- action_hash: `{record.action_hash}`" in content
    assert f"- patch_sha256: `{record.patch_sha256}`" in content
    assert "- changed_files: 3 total, +3/-2" in content
    assert "automation/added.py" in content
    assert "(+2/-0)" in content
    assert "docs/removed.md" in content
    assert "tests/old_name.py → tests/new_name.py" in content
    assert SOURCE in content
    assert "patch_body" in content
    assert content.endswith("리액션")
    assert not content.endswith("\n")


def test_v2_never_carries_the_patch_body() -> None:
    # Given: a summary derived from a patch whose hunk holds a sentinel.
    sentinel = "PATCH_BODY_SENTINEL_9F3A"
    record = _v2()

    # When / Then: only counts and paths cross the boundary, never content.
    assert sentinel not in approval_request_content(record)


def test_v2_truncates_the_file_list_while_totals_still_cover_every_file() -> None:
    # Given: far more changed files than a Discord message can list.
    many = tuple(
        PatchFileDelta(None, f"automation/deep/nested/module_number_{index:03d}_with_a_long_name.py", 3, 1)
        for index in range(37)
    )
    record = _v2(changes=many)

    # When: the request is rendered.
    content = approval_request_content(record)

    # Then: exactly ten files are shown, the omission is stated, and the
    # totals plus the hash still speak for all thirty-seven.
    assert content.count("\n  - ") == MAX_VISIBLE_FILES
    assert "27" in content
    assert "- changed_files: 37 total, +111/-37" in content
    assert len(content) <= repair_approval_render.MAX_APPROVAL_CONTENT_CHARS


def test_v2_keeps_long_unicode_paths_within_the_discord_limit() -> None:
    # Given: pathological field lengths from every direction at once.
    long_paths = tuple(
        PatchFileDelta("docs/" + "아주긴한글경로" * 30 + f"/{i}.md", "docs/" + "b" * 300 + f"/{i}.md", 9, 9)
        for i in range(20)
    )
    record = _v2(changes=long_paths, ticket_id="t_" + "x" * 200, patch_source_path="/srv/" + "p" * 400)

    # When / Then: the message stays postable rather than being rejected at 2000.
    assert len(approval_request_content(record)) <= repair_approval_render.MAX_APPROVAL_CONTENT_CHARS


def test_render_fails_closed_instead_of_slicing_a_finished_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a cap so small that no honest message could fit.
    monkeypatch.setattr(repair_approval_render, "MAX_APPROVAL_CONTENT_CHARS", 10)

    # When / Then: it refuses rather than dropping the digest or the instruction.
    with pytest.raises(ApprovalRenderError):
        _ = approval_request_content(_v2())


def test_v2_without_a_summary_is_refused_rather_than_rendered_as_legacy() -> None:
    # Given: a record claiming v2 but carrying no summary — a partial write.
    broken = _Record(content_binding_version=2, patch_sha256="a" * 64, changes=None, patch_source_path=SOURCE)

    # When / Then: an incomplete binding never renders as if it were complete.
    with pytest.raises(ApprovalRenderError):
        _ = approval_request_content(broken)
