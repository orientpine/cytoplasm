"""채택 계약·기존 CLI 특성·임시 참조 self-test를 합성 입력으로 검증한다."""
from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from automation.stt_eval.compare import paired_compare
from automation.stt_eval.compare_store import JSON
from automation.stt_eval.model import EvalEntity, EvalRecord, EvalTurn, EvalWord, SpeakerTag, dump_record
from automation.stt_eval.report import evaluate, render_json, render_markdown

REPO = Path(__file__).resolve().parents[2]
A, B = "a" * 64, "b" * 64
RULE = REPO / "configs/stt-eval/adoption-rule.json"


def obj(value: JSON) -> dict[str, JSON]:
    assert isinstance(value, dict)
    return value


def arr(value: JSON) -> list[JSON]:
    assert isinstance(value, list)
    return value


def parsed(text: str) -> dict[str, JSON]:
    return obj(cast(JSON, json.loads(text)))


def cli(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "automation.stt_eval", *map(str, args)],
                          cwd=REPO, text=True, capture_output=True, timeout=30)


def corpus(root: Path, *, references: bool = True, annotated: bool = True, n: int = 5) -> None:
    for i in range(n):
        sha = f"{i + 1:08x}" + "0" * 56
        ref = EvalRecord("PRIVATE-ID", sha, 1000, "abcd",
                         words=(EvalWord("w", 0, 4, 0, 1000, SpeakerTag("SPEAKER", ("s",)), "token"),),
                         turns=(EvalTurn(0, 1000, "s"),) if annotated else (),
                         der_regions=((0, 1000),) if annotated else (),
                         entities=(EvalEntity("e", "name", 0, 4, "abcd"),) if annotated else ())
        for directory, record in ((root / "reference", ref),
                                  (root / "hyp" / A, replace(ref, text="axcd", config_sha256=A)),
                                  (root / "hyp" / B, replace(ref, config_sha256=B))):
            if directory.name == "reference" and not references:
                continue
            directory.mkdir(parents=True, exist_ok=True)
            dump_record(record, directory / f"{sha}.json")


def summary(ci: list[JSON] | None = None, *, n: int = 5) -> dict[str, JSON]:
    return {"n": n, "scored_n": n, "ci95": [-3, -1] if ci is None else ci}


def comparison(*, n: int = 5) -> dict[str, JSON]:
    return {**summary(n=n), "metric": "cpcer_strict",
            "guards": {name: summary([-1, 1], n=n) for name in ("cer", "der", "entity_f1")}}


def test_characterize_existing_cli_and_report(tmp_path: Path) -> None:
    corpus(tmp_path)
    expected = paired_compare(tmp_path / "reference", tmp_path / "hyp" / A, tmp_path / "hyp" / B,
                              metric="cer", groups={}, samples=20, seed=7)
    actual = cli("compare", "--root", tmp_path, "--a", A, "--b", B,
                 "--metric", "cer", "--samples", "20", "--seed", "7", "--json")
    assert actual.returncode == 0, actual.stderr
    payload = parsed(actual.stdout)
    assert {key: payload[key] for key in expected} == expected
    evaluation = evaluate(tmp_path / "reference", tmp_path / "hyp" / A)
    assert parsed(render_json(evaluation)) == evaluation
    assert parsed(render_markdown(evaluation).split("```json\n")[1].split("```")[0]) == evaluation
    status = cli("status", "--root", tmp_path, "--json")
    assert status.returncode == 0, status.stderr
    assert parsed(status.stdout) == {"records": 0, "references": 5, "hypotheses": {A[:8]: 5, B[:8]: 5}}


@pytest.mark.parametrize(("n", "expected"), [(0, "insufficient"), (2, "insufficient"), (3, "adopt"), (5, "adopt")])
def test_sample_floor_and_adoption(n: int, expected: str) -> None:
    from automation.stt_eval.adoption import decide, load_rule
    result = comparison(n=n)
    original = deepcopy(result)
    verdict = decide(result, load_rule(RULE))
    assert verdict.status == expected
    assert verdict.reasons
    assert result == original


@pytest.mark.parametrize("metric", ["cer", "der", "entity_f1"])
@pytest.mark.parametrize("missing", ["absent", "null", "interval", "few"])
def test_missing_guards_are_inconclusive(metric: str, missing: str) -> None:
    from automation.stt_eval.adoption import decide, load_rule
    result = comparison()
    guards = obj(result["guards"])
    if missing == "absent":
        del guards[metric]
    elif missing == "null":
        guards[metric] = None
    elif missing == "interval":
        obj(guards[metric])["ci95"] = None
    else:
        obj(guards[metric])["scored_n"] = 2
    assert decide(result, load_rule(RULE)).status == "inconclusive"


@pytest.mark.parametrize(("metric", "ci"), [("cer", [1, 3]), ("der", [1, 3]), ("entity_f1", [-3, -1])])
def test_guard_regression_rejects(metric: str, ci: list[JSON]) -> None:
    from automation.stt_eval.adoption import decide, load_rule
    result = comparison()
    obj(obj(result["guards"])[metric])["ci95"] = ci
    assert decide(result, load_rule(RULE)).status == "reject"


@pytest.mark.parametrize(("ci", "expected"), [([-1, 0], "inconclusive"), ([0, 0], "inconclusive"),
                                               ([-1, 1], "inconclusive"), ([0, 1], "inconclusive"),
                                               ([1, 3], "reject"), (None, "inconclusive")])
def test_primary_interval_direction_and_zero(ci: JSON, expected: str) -> None:
    from automation.stt_eval.adoption import decide, load_rule
    result = comparison()
    result["ci95"] = ci
    assert decide(result, load_rule(RULE)).status == expected


def test_self_test_always_insufficient() -> None:
    from automation.stt_eval.adoption import decide, load_rule
    result = comparison()
    result["self_test"] = True
    assert decide(result, load_rule(RULE)).status == "insufficient"


@pytest.mark.parametrize("mutation", ["unknown", "nested", "missing", "confidence", "floor", "bool", "metric", "direction", "json"])
def test_rule_rejects_invalid_input(tmp_path: Path, mutation: str) -> None:
    from automation.stt_eval.adoption import AdoptionRuleError, load_rule
    raw = parsed(RULE.read_text())
    if mutation == "unknown":
        raw["surprise"] = True
    elif mutation == "nested":
        obj(raw["guards"])["surprise"] = "lower"
    elif mutation == "missing":
        del raw["guards"]
    elif mutation == "confidence":
        raw["confidence"] = 0.90
    elif mutation == "floor":
        raw["min_references"] = 2
    elif mutation == "bool":
        raw["min_references"] = True
    elif mutation == "metric":
        raw["metric"] = "cer"
    elif mutation == "direction":
        obj(raw["guards"])["entity_f1"] = "lower"
    path = tmp_path / "rule.json"
    _ = path.write_text("{" if mutation == "json" else json.dumps(raw))
    with pytest.raises(AdoptionRuleError):
        _ = load_rule(path)


@pytest.mark.parametrize("annotated", [True, False])
def test_compare_cli_adoption_preserves_existing_metric(tmp_path: Path, annotated: bool) -> None:
    corpus(tmp_path, annotated=annotated)
    result = cli("compare", "--root", tmp_path, "--a", A, "--b", B,
                 "--metric", "cer", "--samples", "20", "--json")
    assert result.returncode == 0, result.stderr
    payload = parsed(result.stdout)
    assert payload["metric"] == "cer"
    adoption = obj(payload["adoption"])
    assert adoption["status"] == ("adopt" if annotated else "inconclusive")
    assert adoption["default_changed"] is False
    assert adoption["default_candidate"] is annotated
    assert obj(payload["adoption_primary"])["metric"] == "cpcer_strict"
    assert set(obj(payload["guards"])) == {"cer", "der", "entity_f1"}


@pytest.mark.parametrize("metric", ["cpcer", "cpcer_strict"])
def test_empty_reference_cpcer_is_not_adoptable(tmp_path: Path, metric: str) -> None:
    from automation.stt_eval.adoption import decide, load_rule
    for i in range(5):
        sha = f"{i + 1:08x}" + "0" * 56
        for directory, config in ((tmp_path / "reference", None), (tmp_path / "hyp" / A, A),
                                  (tmp_path / "hyp" / B, B)):
            directory.mkdir(parents=True, exist_ok=True)
            dump_record(EvalRecord("synthetic", sha, 1000, "", config_sha256=config), directory / f"{sha}.json")
    result = paired_compare(tmp_path / "reference", tmp_path / "hyp" / A, tmp_path / "hyp" / B,
                            metric=metric, groups={}, samples=20)
    assert result["ci95"] is None and obj(result["micro"])["b"] is None
    result["guards"] = comparison()["guards"]
    assert decide(result, load_rule(RULE)).status == "inconclusive"


@pytest.mark.parametrize("references", [True, False])
def test_self_test_cli_all_configs_never_installs_references(tmp_path: Path, references: bool) -> None:
    corpus(tmp_path, references=references)
    before = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*.json")}
    result = cli("run", "--self-test", "--root", tmp_path, "--baseline", B, "--samples", "20")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "insufficient"
    payload = parsed((tmp_path / "reports/self-test.json").read_text())
    assert obj(payload["adoption"])["status"] == "insufficient"
    assert payload["baseline"] == B[:8]
    assert payload["reference_source"] == ("reference" if references else "hypothesis-copy")
    evaluations = arr(payload["evaluations"])
    assert len(evaluations) == 2
    assert [obj(obj(row)["micro"])["cer"] for row in evaluations] == [0.25, 0.0]
    assert all(obj(obj(row)["adoption"])["status"] == "insufficient" for row in arr(payload["comparisons"]))
    assert all((tmp_path / path).read_bytes() == value for path, value in before.items())
    assert not list(tmp_path.glob("self-test-*"))
    assert (tmp_path / "reference").exists() is references
    for path in (tmp_path / "reports").iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        assert "PRIVATE-ID" not in path.read_text()
    assert obj(payload["adoption"])["default_changed"] is False


def test_empty_self_test_and_invalid_rule_cli(tmp_path: Path) -> None:
    result = cli("run", "--self-test", "--root", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "insufficient"
    rule = parsed(RULE.read_text())
    rule["unknown"] = True
    path = tmp_path / "bad-rule.json"
    _ = path.write_text(json.dumps(rule))
    result = cli("run", "--self-test", "--root", tmp_path, "--rule", path)
    assert result.returncode == 2
    assert result.stderr.strip() == "AdoptionRuleError"


def test_run_requires_configs_and_refuses_checkout(tmp_path: Path) -> None:
    missing = cli("run", "--root", tmp_path)
    assert missing.returncode == 2
    refused = cli("run", "--self-test", "--root", REPO)
    assert refused.returncode == 3
    assert "STT-EVAL-ROOT-REFUSED" in refused.stderr


def test_run_wires_real_runner_busy_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    from automation.stt_eval.__main__ import main
    from tests.unit.test_stt_eval_gpu_guard import stub_environment
    environment = stub_environment(tmp_path)
    environment["UTIL"] = "5"
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "eval"
    root.mkdir()
    _ = (root / "manifest.jsonl").write_text("")
    configs = tmp_path / "configs.json"
    _ = configs.write_text("[]")
    assert main(["run", "--root", str(root), "--configs", str(configs)]) == 6
    assert "GPU-BUSY utilization" in capsys.readouterr().out
    assert not (root / "runs.jsonl").exists()
