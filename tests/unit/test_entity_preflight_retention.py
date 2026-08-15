from __future__ import annotations

import stat
from datetime import datetime, timezone
from pathlib import Path

from automation.entity_preflight.audit import rotate_entity_preflight_logs


def _write(root: Path, name: str, body: str) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / name
    path.write_text(body, encoding="utf-8")
    return path


def test_rotation_applies_private_and_operational_retention_in_one_pass(tmp_path: Path) -> None:
    # Given
    now = datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)
    private_root = tmp_path / "audit"
    operational_root = tmp_path / "operational"
    _write(private_root, "entity-preflight.jsonl", "private-current\n")
    private_expired = _write(
        private_root,
        "entity-preflight.20260703T000000Z.jsonl",
        "private-expired\n",
    )
    private_retained = _write(
        private_root,
        "entity-preflight.20260705T000000Z.jsonl",
        "private-retained\n",
    )
    _write(operational_root, "entity-preflight.jsonl", "operational-current\n")
    operational_expired = _write(
        operational_root,
        "entity-preflight.20260203T000000Z.jsonl",
        "operational-expired\n",
    )
    operational_retained = _write(
        operational_root,
        "entity-preflight.20260205T000000Z.jsonl",
        "operational-retained\n",
    )

    # When
    rotate_entity_preflight_logs(private_root, operational_root, now=now)

    # Then
    assert not private_expired.exists()
    assert private_retained.exists()
    assert not operational_expired.exists()
    assert operational_retained.exists()
    for root, expected in (
        (private_root, "private-current\n"),
        (operational_root, "operational-current\n"),
    ):
        backup = root / "entity-preflight.20260803T000000Z.jsonl"
        active = root / "entity-preflight.jsonl"
        assert backup.read_text(encoding="utf-8") == expected
        assert active.read_text(encoding="utf-8") == ""
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert stat.S_IMODE(active.stat().st_mode) == 0o600
