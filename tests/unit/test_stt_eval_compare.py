"""쌍 결측, 군집 재표집과 마이크로 분모를 수치로 고정한다."""
import json
import random
from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path
from typing import cast

import pytest

from automation.stt_eval.compare_store import JSON
from automation.stt_eval.model import EvalRecord, EvalRecordError, dump_record

A, B = "a" * 64, "b" * 64


def api():
    assert find_spec("automation.stt_eval.compare") is not None, "paired comparison not implemented"
    from automation.stt_eval.compare import paired_compare
    return paired_compare


def obj(value: JSON) -> dict[str, JSON]:
    assert isinstance(value, dict)
    return value


def arr(value: JSON) -> list[JSON]:
    assert isinstance(value, list)
    return value


def corpus(root: Path, texts: tuple[str, ...] = ("abcd",) * 4,
           b_texts: tuple[str, ...] | None = None) -> tuple[Path, Path, Path, list[str]]:
    refs, a, b = root / "reference", root / "hyp" / A, root / "hyp" / B
    for folder in (refs, a, b):
        folder.mkdir(parents=True)
    shas = [f"{i + 1:08x}" + "0" * 56 for i in range(len(texts))]
    for i, (sha, text) in enumerate(zip(shas, texts, strict=True)):
        ref = EvalRecord("private-recording", sha, 1000, text)
        dump_record(ref, refs / f"{sha}.json")
        dump_record(replace(ref, config_sha256=A), a / f"{sha}.json")
        dump_record(replace(ref, config_sha256=B, text=b_texts[i] if b_texts else text), b / f"{sha}.json")
    return refs, a, b, shas


def test_missing_timeout_excluded_not_zero(tmp_path: Path) -> None:
    refs, a, b, shas = corpus(tmp_path, b_texts=("axcd",) * 4)
    (b / f"{shas[-1]}.json").unlink()
    _ = (tmp_path / "candidates.jsonl").write_text(json.dumps({"label": "candidate-B", "config_sha256": B}) + "\n")
    rows = [{"audio_sha256": shas[-1], "label": "candidate-B", "config_sha256": None,
             "status": "failed", "reason": reason} for reason in ("no-artifact", "timeout")]
    _ = (tmp_path / "runs.jsonl").write_text("\n".join(map(json.dumps, rows)))
    result = api()(refs, a, b, metric="cer", groups={}, samples=100)
    assert result["n"] == 3 and result["insufficient"] is False
    assert result["missing"] == [{"audio_sha8": shas[-1][:8], "side": "B", "reason": "timeout"}]
    assert result["micro"] == {"a": 0, "b": 0.25, "delta": 0.25}
    assert result["mean_delta"] == 0.25


def test_insufficient_and_both_sides_missing(tmp_path: Path) -> None:
    refs, a, b, shas = corpus(tmp_path)
    for sha in shas[2:]:
        (a / f"{sha}.json").unlink()
        (b / f"{sha}.json").unlink()
    result = api()(refs, a, b, metric="cer", groups={}, samples=20)
    assert result["n"] == 2 and result["insufficient"] is True
    assert len(arr(result["missing"])) == 4
    assert [obj(row)["reason"] for row in arr(result["missing"])] == ["not-run"] * 4


def test_cluster_bootstrap_seed_and_domains(tmp_path: Path) -> None:
    refs, a, b, shas = corpus(tmp_path, b_texts=("abcd", "abcd", "abcd", "xxxx"))
    groups = {sha: {"group_id": "private-group-1" if i < 3 else "private-group-2",
                    "domain": "meeting" if i < 3 else "lifelog"} for i, sha in enumerate(shas)}
    first = api()(refs, a, b, metric="cer", groups=groups, samples=100, seed=17)
    second = api()(refs, a, b, metric="cer", groups=groups, samples=100, seed=17)
    assert first == second
    rng = random.Random(17)
    draws: list[float] = []
    for _ in range(100):
        chosen = [rng.randrange(2) for _ in range(2)]
        draws.append(sum(chosen) / sum(3 if group == 0 else 1 for group in chosen))
    draws.sort()
    assert first["ci95"] == [draws[2], draws[97]]
    assert first["n_groups"] == 2
    assert obj(obj(first["domains"])["meeting"])["n"] == 3
    assert obj(obj(first["domains"])["lifelog"])["mean_delta"] == 1
    assert "private-group" not in json.dumps(first)


def test_micro_is_count_weighted_not_macro(tmp_path: Path) -> None:
    refs, a, b, _ = corpus(tmp_path, texts=("a", "abc", "abcdef"), b_texts=("x", "abc", "abcdef"))
    result = api()(refs, a, b, metric="cer", groups={}, samples=30)
    assert result["mean_delta"] == 1 / 3
    assert obj(result["micro"])["b"] == 0.1


def test_undefined_metric_is_not_zero(tmp_path: Path) -> None:
    refs, a, b, _ = corpus(tmp_path, texts=("", "", ""))
    result = api()(refs, a, b, metric="cer", groups={}, samples=30)
    assert result["n"] == 3 and result["scored_n"] == 0
    assert obj(result["micro"])["b"] is None and result["ci95"] is None


def test_drift_failure_resolves_by_original_candidate_label(tmp_path: Path) -> None:
    refs, a, b, shas = corpus(tmp_path)
    (b / f"{shas[-1]}.json").unlink()
    _ = (tmp_path / "candidates.jsonl").write_text(json.dumps({"label": "candidate", "config_sha256": B}))
    _ = (tmp_path / "runs.jsonl").write_text(json.dumps({"label": "candidate", "audio_sha256": shas[-1],
                                                   "config_sha256": "c" * 64, "status": "failed",
                                                   "reason": "CANDIDATE-DRIFT"}))
    result = api()(refs, a, b, metric="cer", groups={}, samples=10)
    assert obj(arr(result["missing"])[0])["reason"] == "CANDIDATE-DRIFT"


def test_private_failure_detail_is_masked_and_failed_hyp_excluded(tmp_path: Path) -> None:
    refs, a, b, shas = corpus(tmp_path)
    path = b / f"{shas[-1]}.json"
    raw = obj(cast(JSON, json.loads(path.read_text())))
    raw["status"] = "failed"
    _ = path.write_text(json.dumps(raw))
    _ = (tmp_path / "runs.jsonl").write_text(json.dumps({"audio_sha256": shas[-1], "config_sha256": B,
                                                   "status": "failed", "reason": "/private/path SECRET-ID"}))
    result = api()(refs, a, b, metric="cer", groups={}, samples=10)
    assert result["n"] == 3
    assert obj(arr(result["missing"])[0])["reason"] == "redacted"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("mutation", ["json", "kind", "span", "identity", "config"])
def test_malformed_hyp_is_typed_error(tmp_path: Path, mutation: str) -> None:
    refs, a, b, shas = corpus(tmp_path)
    path = b / f"{shas[0]}.json"
    raw = obj(cast(JSON, json.loads(path.read_text())))
    if mutation == "json":
        _ = path.write_text("{")
    else:
        if mutation in ("kind", "span"):
            raw["entities"] = [{"id": "e", "kind": "unknown" if mutation == "kind" else "name",
                                "char_start": 3, "char_end": 1 if mutation == "span" else 4,
                                "canonical": "x", "accepted": []}]
        elif mutation == "identity":
            raw["audio_sha256"] = "f" * 64
        else:
            raw["config_sha256"] = A
        _ = path.write_text(json.dumps(raw))
    with pytest.raises(EvalRecordError):
        _ = api()(refs, a, b, metric="cer", groups={}, samples=10)
