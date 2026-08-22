from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("TOPICS_SCRIPTS", str(_ROOT / "skills" / "topics" / "scripts"))
sys.path.insert(0, str(_ROOT / "automation" / "research_trends"))

from automation.entity_preflight.contracts import VerificationOutcome  # noqa: E402
from automation.entity_preflight.gate_quality import (  # noqa: E402
    ConfidenceBucket,
    GateQualityRecord,
    QualityDecision,
)
from automation.research_trends import research_trends  # noqa: E402


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


def test_existing_research_trends_run_appends_quality_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    operational_root = tmp_path / "operational"
    operational_root.mkdir()
    (operational_root / "entity-preflight.jsonl").write_text(
        json.dumps(_quality_record().to_event()) + "\n",
        encoding="utf-8",
    )
    reports: list[str] = []
    monkeypatch.setattr(research_trends, "ENTITY_PREFLIGHT_OPERATIONAL_ROOT", operational_root)
    monkeypatch.setattr(research_trends, "_safe_topics", lambda: ("autophagy",))
    monkeypatch.setattr(research_trends.core, "run_topics", lambda *_: ())
    monkeypatch.setattr(research_trends, "_write_report", lambda report, _day: reports.append(report))
    monkeypatch.setenv("RESEARCH_TRENDS_DRY_RUN", "1")

    # When
    return_code = research_trends.run()

    # Then
    assert return_code == 0
    assert len(reports) == 1
    assert '"metric":"entity_preflight_quality"' in reports[0]
