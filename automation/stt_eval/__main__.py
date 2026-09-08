"""비공개 평가 루트의 evaluate/compare/status/run CLI와 채택 보고 배선."""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from automation.drive_outputs import client_from_environment
from automation.skill_mount import skill_scripts

from .adoption import DEFAULT_RULE, AdoptionRuleError, load_rule
from .compare_store import JSON, configurations, manifest, private_root, records, resolve_config
from .metrics_der import EvalLimitError as DerLimitError
from .metrics_text import EvalLimitError as TextLimitError
from .model import EvalRecordError
from .report import compare_report, evaluate, render_json, render_markdown, save_report, self_test_report
from .report_metrics import METRICS
from .runner import run


class _Args(argparse.Namespace):
    command: str = ""
    root: Path = Path("~/.hermes/stt-eval")
    json: bool = False
    config: str | None = None
    a: str = ""
    b: str = ""
    metric: str = "cpcer_strict"
    samples: int = 10_000
    seed: int = 20260906
    rule: Path = DEFAULT_RULE
    self_test: bool = False
    baseline: str | None = None
    configs: Path | None = None
    manifest: Path | None = None
    cli: Path | None = None
    tmp: Path | None = None


def status(root: Path) -> dict[str, JSON]:
    """상태 조회는 디렉터리를 만들거나 원장을 고치지 않는다."""
    configs = configurations(root)
    return {"records": len(manifest(root)), "references": len(records(root / "reference")),
            "hypotheses": {config[:8]: len(records(root / "hyp" / config, hypothesis=True)) for config in configs}}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m automation.stt_eval")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("evaluate", "compare", "status", "run"):
        child = commands.add_parser(command)
        _ = child.add_argument("--root", type=Path, default=os.environ.get("STT_EVAL_ROOT", "~/.hermes/stt-eval"))
        _ = child.add_argument("--json", action="store_true")
        if command == "evaluate":
            _ = child.add_argument("--config", help="config hash or candidate label; omitted: all configs")
        if command == "compare":
            _ = child.add_argument("--a", required=True)
            _ = child.add_argument("--b", required=True)
            _ = child.add_argument("--metric", choices=METRICS, default="cpcer_strict")
        if command in ("compare", "run"):
            _ = child.add_argument("--samples", type=int, default=10_000)
            _ = child.add_argument("--seed", type=int, default=20260906)
            _ = child.add_argument("--rule", type=Path, default=DEFAULT_RULE)
        if command == "run":
            mode = child.add_mutually_exclusive_group(required=True)
            _ = mode.add_argument("--self-test", action="store_true")
            _ = mode.add_argument("--configs", type=Path)
            _ = child.add_argument("--baseline", help="self-test current config hash or label; omitted: sorted first")
            _ = child.add_argument("--manifest", type=Path, help="omitted: ROOT/manifest.jsonl")
            _ = child.add_argument("--cli", type=Path, help="speechtotext CLI path")
            _ = child.add_argument("--tmp", type=Path, help="omitted: ROOT/tmp")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = cast(_Args, _parser().parse_args(argv))
    payload: dict[str, JSON]
    try:
        root = private_root(Path(args.root))
        if args.command == "status":
            payload = status(root)
            if not payload["records"] and not payload["references"] and not payload["hypotheses"]:
                print("no records")
                return 0
        elif args.command == "compare":
            a, b = resolve_config(root, args.a), resolve_config(root, args.b)
            payload = compare_report(root / "reference", root / "hyp" / a, root / "hyp" / b,
                                     rule=load_rule(args.rule), metric=args.metric, samples=args.samples, seed=args.seed)
            save_report(root, f"compare-{a}-{b}-{args.metric}", payload)
        elif args.command == "run":
            if not args.self_test:
                if args.configs is None:
                    raise EvalRecordError("run: 후보 설정이 필요합니다")
                configured_cli = os.environ.get("SPEECHTOTEXT_CLI", "").strip()
                cli = args.cli or (Path(configured_cli) if configured_cli else
                                   skill_scripts("speechtotext", env_var="SPEECHTOTEXT_SCRIPTS") / "speechtotext_cli.py")
                return run(args.manifest or root / "manifest.jsonl", args.configs, root,
                           drive=client_from_environment(), cli=cli, tmp=args.tmp or root / "tmp", env=os.environ)
            payload = self_test_report(root, rule=load_rule(args.rule), baseline=args.baseline,
                                       samples=args.samples, seed=args.seed)
            save_report(root, "self-test", payload)
        else:
            configs = [resolve_config(root, args.config)] if args.config else configurations(root)
            if not configs:
                print("no records")
                return 0
            evaluations = [evaluate(root / "reference", root / "hyp" / config) for config in configs]
            for config, evaluation in zip(configs, evaluations, strict=True):
                save_report(root, f"evaluate-{config}", evaluation)
            payload = evaluations[0] if len(evaluations) == 1 else {"evaluations": list(evaluations)}
        print(render_json(payload) if args.json else render_markdown(payload))
        return 0
    except AdoptionRuleError:
        print("AdoptionRuleError", file=sys.stderr)
        return 2
    except EvalRecordError as error:
        if str(error) == "STT-EVAL-ROOT-REFUSED":
            print("STT-EVAL-ROOT-REFUSED", file=sys.stderr)
            return 3
        # 파서 예외에는 원문/경로가 섞일 수 있으므로 상세는 출력하지 않는다.
        print("STT-EVAL-INPUT-ERROR", file=sys.stderr)
        return 2
    except (DerLimitError, TextLimitError):
        print("STT-EVAL-LIMIT-ERROR", file=sys.stderr)
        return 2
    except OSError:
        print("STT-EVAL-IO-ERROR", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
