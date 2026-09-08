"""원문·원본 ID·경로를 받지 않는 마스킹 보고 조립과 JSON/마크다운 출력."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from .adoption import AdoptionRule, Verdict, decide
from .compare import paired_compare
from .compare_store import JSON, configurations, failures, manifest, metadata, private_root, records, resolve_config
from .entities import KINDS, EntityStats, entity_metrics, entity_stats
from .model import EvalRecordError, dump_record
from .normalize import NORMALIZATION_VERSION
from .report_metrics import METRICS, Count, measure, micro, ratio


@dataclass(frozen=True, slots=True)
class Scored:
    sha8: str
    domain: str
    metrics: dict[str, Count]
    entities: dict[str, EntityStats]


def _entity_payload(stats: EntityStats) -> dict[str, JSON]:
    return {"tp": stats["tp"], "fp": stats["fp"], "fn": stats["fn"], "precision": stats["precision"],
            "recall": stats["recall"], "f1": stats["f1"], "miss_rate": stats["miss_rate"]}


def _summary(scored: list[Scored]) -> dict[str, JSON]:
    entities: dict[str, JSON] = {}
    for kind in KINDS:
        tp = sum(row.entities[kind]["tp"] for row in scored)
        fp = sum(row.entities[kind]["fp"] for row in scored)
        fn = sum(row.entities[kind]["fn"] for row in scored)
        entities[kind] = _entity_payload(entity_stats(tp, fp, fn))
    return {"n": len(scored), "micro": {metric: micro(row.metrics[metric] for row in scored) for metric in METRICS},
            "entities": entities}


def evaluate(refs: Path, hypotheses: Path) -> dict[str, JSON]:
    """정답이 있는 녹음의 가설만 평가하며 결측과 실패는 별도로 보고한다."""
    references, hyps = records(refs), records(hypotheses, hypothesis=True)
    defaults = manifest(refs.parent)
    reasons = failures(refs.parent, hypotheses.name)
    scored: list[Scored] = []
    missing: list[JSON] = []
    for sha, ref in references.items():
        if ref.status == "failed":
            raise EvalRecordError("reference: 실패한 참조입니다")
        hyp = hyps.get(sha)
        if hyp is None or hyp.status == "failed":
            missing.append({"audio_sha8": sha[:8], "side": "hyp",
                            "reason": reasons.get(sha, "failed" if hyp is not None else "not-run")})
            continue
        _, domain = metadata(sha, {}, defaults)
        scored.append(Scored(sha[:8], domain, {metric: measure(ref, hyp, metric) for metric in METRICS},
                             entity_metrics(ref, hyp)))
    result = _summary(scored)
    result.update({"config": hypotheses.name[:8], "normalization": NORMALIZATION_VERSION, "missing": missing,
                   "records": [{"recording_id": row.sha8, "domain": row.domain,
                                "metrics": {metric: ratio(count) for metric, count in row.metrics.items()},
                                "entities": {kind: _entity_payload(stats) for kind, stats in row.entities.items()}}
                               for row in scored],
                   "domains": {domain: _summary([row for row in scored if row.domain == domain])
                               for domain in ("meeting", "lifelog", "unknown")}})
    return result


def adoption_payload(verdict: Verdict) -> dict[str, JSON]:
    """자동 채택은 없으며 후보 표시는 사람의 검토에만 쓰인다."""
    return {"status": verdict.status, "reasons": list(verdict.reasons),
            "default_candidate": verdict.status == "adopt", "default_changed": False}


def compare_report(refs: Path, hyp_a: Path, hyp_b: Path, *, rule: AdoptionRule,
                   metric: str = "cpcer_strict", samples: int = 10_000, seed: int = 20260906,
                   self_test: bool = False) -> dict[str, JSON]:
    """요청한 지표 계약은 유지하고 cpcer_strict·가드 비교로 채택 절만 덧붙인다."""
    comparisons = {name: paired_compare(refs, hyp_a, hyp_b, metric=name, groups={}, samples=samples, seed=seed)
                   for name in dict.fromkeys((metric, rule.metric, *(name for name, _ in rule.guards)))}
    guards: dict[str, JSON] = {name: comparisons[name] for name, _ in rule.guards}
    primary = comparisons[rule.metric]
    verdict = decide({**primary, "guards": guards, "self_test": self_test}, rule)
    return {**comparisons[metric], "adoption_primary": primary, "guards": guards,
            "self_test": self_test, "adoption": adoption_payload(verdict)}


def self_test_report(root: Path, *, rule: AdoptionRule, baseline: str | None = None,
                     samples: int = 10_000, seed: int = 20260906) -> dict[str, JSON]:
    """현행 가설의 임시 복사만 참조로 쓴다. 진짜 reference/와 원장은 쓰지 않는다.

    현행 설정은 --baseline으로 지정한다. 생략 시 정렬된 첫 설정을 택하고 보고한다.
    이미 정답이 있으면 복사 없이 평가하되 self-test의 판정은 여전히 insufficient다.
    """
    root = private_root(root)
    configs = configurations(root)
    selected = resolve_config(root, baseline) if baseline else next(iter(configs), None)
    if selected is not None and selected not in configs:
        raise EvalRecordError("self-test: 현행 가설 설정이 없습니다")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    refs = root / "reference"
    copied = not records(refs)
    with tempfile.TemporaryDirectory(prefix="self-test-", dir=root) as directory:
        if copied:
            refs = Path(directory)
            if selected is not None:
                for sha, hyp in records(root / "hyp" / selected, hypothesis=True).items():
                    if hyp.status != "failed":
                        dump_record(replace(hyp, config_sha256=None), refs / f"{sha}.json")
        evaluations: list[JSON] = [evaluate(refs, root / "hyp" / config) for config in configs]
        comparisons: list[JSON] = []
        if selected is not None:
            comparisons = [compare_report(refs, root / "hyp" / selected, root / "hyp" / config,
                                           rule=rule, samples=samples, seed=seed, self_test=True)
                           for config in configs]
    return {"self_test": True, "baseline": selected[:8] if selected else None,
            "baseline_selection": "explicit" if baseline else "sorted-first",
            "reference_source": "hypothesis-copy" if copied else "reference",
            "adoption": adoption_payload(decide({"self_test": True}, rule)),
            "evaluations": evaluations, "comparisons": comparisons}


def render_json(payload: dict[str, JSON]) -> str:
    """조립 단계에서 허용 필드만 담은 지표 객체를 직렬화한다."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def render_markdown(payload: dict[str, JSON]) -> str:
    """동일 지표를 손실 없이 읽을 수 있게 마크다운 코드 블록에 담는다."""
    text = "# STT evaluation\n"
    adoption = payload.get("adoption")
    if isinstance(adoption, dict):
        text += (f"\n## 채택\n\n{adoption['status']}\n\n"
                 "기본값 불변. adopt는 후보 표시이며 변경에는 사람의 명시 커밋이 필요합니다.\n")
        reasons = adoption["reasons"]
        if isinstance(reasons, list):
            text += "\n".join(f"- {reason}" for reason in reasons) + "\n"
    if payload.get("self_test") is True:
        text = "insufficient\n\n" + text
    return text + "\n```json\n" + render_json(payload) + "\n```\n"


def save_report(root: Path, name: str, payload: dict[str, JSON]) -> None:
    """CLI가 만든 고정 이름 아래에 마스킹 산출물만 0700/0600으로 남긴다."""
    directory = root / "reports"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    for suffix, text in (("json", render_json(payload) + "\n"), ("md", render_markdown(payload))):
        fd = os.open(directory / f"{name}.{suffix}", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            _ = handle.write(text)
