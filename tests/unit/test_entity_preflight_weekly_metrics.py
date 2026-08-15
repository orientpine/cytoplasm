from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from automation.entity_preflight.contracts import VerificationOutcome
from automation.entity_preflight.gate_quality import (
    ConfidenceBucket,
    GateQualityRecord,
    QualityDecision,
)
from automation.entity_preflight.gate_metrics import weekly_quality_section


def _quality_record() -> GateQualityRecord:
    return GateQualityRecord(
        decision_id="corr-weekly-1",
        sensitive_audit_ref="private://entity-preflight/corr-weekly-1",
        channel="google_tasks",
        surface="create",
        decision=QualityDecision.AUTO_NORMALIZED,
        reason="single_high_confidence",
        confidence_bucket=ConfidenceBucket.VERY_HIGH,
        source_kinds=("personal_rag",),
        entity_count=1,
        latency_ms=12,
        external_write_blocked=False,
        verification=VerificationOutcome.MATCH,
        policy_version="entity-preflight-v1",
    )


def test_weekly_quality_section_aggregates_only_operational_records(tmp_path: Path) -> None:
    # Given
    operational_root = tmp_path / "operational"
    operational_root.mkdir()
    (operational_root / "entity-preflight.jsonl").write_text(
        json.dumps(_quality_record().to_event()) + "\n",
        encoding="utf-8",
    )
    (operational_root / "entity-preflight.20260727T000000Z.jsonl").write_text(
        json.dumps(_quality_record().to_event()) + "\n",
        encoding="utf-8",
    )
    private_root = tmp_path / "audit"
    private_root.mkdir()
    private_log = private_root / "entity-preflight.jsonl"
    private_log.write_text("raw personal audit must remain untouched\n", encoding="utf-8")

    # When
    section = weekly_quality_section(
        operational_root,
        private_root,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    # Then
    assert '"metric":"entity_preflight_quality"' in section
    assert '"total":2' in section
    assert "raw personal audit" not in section
    assert private_log.read_text(encoding="utf-8") == ""
    assert (private_root / "entity-preflight.20260803T000000Z.jsonl").exists()
