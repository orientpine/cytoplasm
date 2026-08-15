"""Personal proper-noun preflight contract and deterministic policy."""

from . import contracts
from .clarify import ENTITY_CLARIFY_EXIT_CODE, ENTITY_CLARIFY_MARKER, render_clarify
from .gate_metrics import (
    AlertKind,
    MetricsThresholds,
    QualityAlert,
    QualityMetrics,
    aggregate_quality,
    evaluate_alerts,
    load_metrics_thresholds,
)
from .gate_quality import ConfidenceBucket, GateQualityRecord, QualityDecision
from .policy import POLICY_SEED_PATH, PolicyError, PreflightPolicy, decide, load_policy

__all__ = [
    "ENTITY_CLARIFY_EXIT_CODE",
    "ENTITY_CLARIFY_MARKER",
    "POLICY_SEED_PATH",
    "AlertKind",
    "ConfidenceBucket",
    "GateQualityRecord",
    "MetricsThresholds",
    "PolicyError",
    "PreflightPolicy",
    "QualityAlert",
    "QualityDecision",
    "QualityMetrics",
    "aggregate_quality",
    "contracts",
    "decide",
    "evaluate_alerts",
    "load_metrics_thresholds",
    "load_policy",
    "render_clarify",
]
