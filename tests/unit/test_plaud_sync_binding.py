from __future__ import annotations

from dataclasses import replace

from automation.plaud_sync.binding import PlaudHashFields, plaud_action_hash


def _fields() -> PlaudHashFields:
    return PlaudHashFields(
        recording_id="rec-001",
        note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md",
        note_title="standup (2026-09-01)",
        body_sha256="a" * 64,
    )


def test_hash_is_deterministic_and_prefixed() -> None:
    first = plaud_action_hash(_fields())
    assert first == plaud_action_hash(_fields())
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64


def test_hash_binds_every_field() -> None:
    baseline = plaud_action_hash(_fields())
    changed = (
        replace(_fields(), recording_id="rec-002"),
        replace(_fields(), note_relpath="000_PARA/Area/Lifelog/2026/other.md"),
        replace(_fields(), note_title="other (2026-09-01)"),
        replace(_fields(), body_sha256="c" * 64),
    )
    hashes = {plaud_action_hash(fields) for fields in changed}
    assert baseline not in hashes
    assert len(hashes) == len(changed)
