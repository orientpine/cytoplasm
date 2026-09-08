"""양쪽 가설이 있는 녹음만 쌍으로 삼고 그룹 단위 bootstrap으로 비교한다."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

from .compare_store import JSON, Groups, digest, failures, manifest, metadata, records
from .model import EvalRecordError
from .normalize import NORMALIZATION_VERSION
from .report_metrics import METRICS, Count, measure, micro, ratio


@dataclass(frozen=True, slots=True)
class Pair:
    sha: str
    group: str
    domain: str
    a: Count
    b: Count

    @property
    def delta(self) -> float | None:
        a, b = ratio(self.a), ratio(self.b)
        return None if a is None or b is None else b - a


def _interval(clusters: list[list[float]], samples: int, seed: int) -> list[JSON] | None:
    if not clusters:
        return None
    rng = random.Random(seed)
    totals = [(sum(cluster), len(cluster)) for cluster in clusters]
    draws: list[float] = []
    for _ in range(samples):
        selected = [totals[rng.randrange(len(totals))] for _ in totals]
        draws.append(sum(total for total, _ in selected) / sum(size for _, size in selected))
    draws.sort()
    # 95% 구간은 최근접 순위 분위수다. 시드 이외의 전역 난수/시계는 쓰지 않는다.
    return [draws[max(0, math.ceil(samples * tail) - 1)] for tail in (0.025, 0.975)]


def _summary(pairs: list[Pair], samples: int, seed: int) -> dict[str, JSON]:
    clusters: dict[str, list[float]] = {}
    deltas: list[float] = []
    for pair in pairs:
        delta = pair.delta
        if delta is not None:
            clusters.setdefault(pair.group, []).append(delta)
            deltas.append(delta)
    a, b = micro(pair.a for pair in pairs), micro(pair.b for pair in pairs)
    return {"n": len(pairs), "scored_n": len(deltas), "n_groups": len(clusters),
            "insufficient": len(pairs) < 3, "inconclusive": len(deltas) < 3,
            "mean_delta": sum(deltas) / len(deltas) if deltas else None,
            "ci95": _interval(list(clusters.values()), samples, seed),
            "micro": {"a": a, "b": b, "delta": None if a is None or b is None else b - a}}


def paired_compare(
    refs: Path, hyp_a: Path, hyp_b: Path, *, metric: str, groups: Groups,
    samples: int = 10_000, seed: int = 20260906,
) -> dict[str, JSON]:
    """입력은 reference/와 hyp/<해시>/ 디렉터리다. groups는 sha→그룹 또는
    sha→{group_id, domain}; 생략된 항목은 manifest, 이어서 녹음 자체로 묶는다.
    n은 완전 쌍 수, scored_n은 양쪽 지표 분모가 있는 쌍 수다. 결측은 0이 아니다.
    """
    if metric not in METRICS or type(samples) is not int or samples <= 0 or type(seed) is not int:
        raise EvalRecordError("compare: metric/samples/seed가 잘못되었습니다")
    root = refs.parent
    references = records(refs)
    sides = {"A": records(hyp_a, hypothesis=True), "B": records(hyp_b, hypothesis=True)}
    reasons = {"A": failures(root, digest(hyp_a.name)), "B": failures(root, digest(hyp_b.name))}
    defaults = manifest(root)
    missing: list[JSON] = []
    pairs: list[Pair] = []
    for sha, ref in references.items():
        if ref.status == "failed":
            raise EvalRecordError("reference: 실패한 참조입니다")
        complete = True
        for side, hypotheses in sides.items():
            hyp = hypotheses.get(sha)
            if hyp is None or hyp.status == "failed":
                complete = False
                missing.append({"audio_sha8": sha[:8], "side": side,
                                "reason": reasons[side].get(sha, "failed" if hyp is not None else "not-run")})
        if complete:
            group, domain = metadata(sha, groups, defaults)
            pairs.append(Pair(sha, group, domain, measure(ref, sides["A"][sha], metric),
                              measure(ref, sides["B"][sha], metric)))
    result = _summary(pairs, samples, seed)
    result.update({"metric": metric, "normalization": NORMALIZATION_VERSION,
                   "config_a": hyp_a.name[:8], "config_b": hyp_b.name[:8],
                   "samples": samples, "seed": seed, "missing": missing,
                   "records": [{"recording_id": pair.sha[:8], "a": ratio(pair.a),
                                "b": ratio(pair.b), "delta": pair.delta} for pair in pairs],
                   "domains": {domain: _summary([pair for pair in pairs if pair.domain == domain], samples, seed)
                               for domain in ("meeting", "lifelog", "unknown")}})
    return result
