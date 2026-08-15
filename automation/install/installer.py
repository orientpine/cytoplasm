from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from automation.install.apply import apply_plan
from automation.install.assets import InstallAssetError, build_inputs, render_plan
from automation.install.checks import CheckResult, Status, exit_code, render
from automation.install.components import OPT_IN_COMPONENTS, UnknownComponentError
from automation.install.executor import ExecutionContext, RealExecutor
from automation.install.plan import FileSpec, build_plan
from automation.install.state import inspect_state
from automation.install.trust_key_bootstrap import (
    TrustKeyError,
    fingerprints_match,
    plan_group_install,
    plan_install,
)
from automation.node_config import NodeConfigError, load_node_config

if TYPE_CHECKING:  # PyYAML lives behind this import; a plain install never needs it.
    from automation.group_roster.schema import Roster


class _Arguments(argparse.Namespace):
    config: Path | None
    update_trust_key: Path
    expect_update_trust_fingerprint: str | None
    discord_config: Path | None
    dry_run: bool
    with_component: list[str]
    group_roster: Path | None
    expect_group_skill_fingerprint: str | None

    def __init__(self) -> None:
        super().__init__()
        self.config = None
        self.update_trust_key = Path()
        self.expect_update_trust_fingerprint = None
        self.discord_config = None
        self.dry_run = False
        self.with_component = []
        self.group_roster = None
        self.expect_group_skill_fingerprint = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autophagy-node-install")
    _ = parser.add_argument("--config", type=Path, default=None)
    _ = parser.add_argument("--update-trust-key", type=Path, required=True)
    _ = parser.add_argument("--expect-update-trust-fingerprint", default=None)
    _ = parser.add_argument("--discord-config", type=Path, default=None)
    _ = parser.add_argument("--dry-run", action="store_true")
    _ = parser.add_argument(
        "--group-roster",
        type=Path,
        default=None,
        help="validated group roster whose admin signing key will be installed",
    )
    _ = parser.add_argument(
        "--expect-group-skill-fingerprint",
        default=None,
        help=(
            "required out-of-band admin fingerprint; "
            "GROUP-DISCORD-FORBIDDEN: never receive it through the group Discord channel"
        ),
    )
    # Opt-in: repeatable, empty by default. An unnamed component is not installed at all,
    # so omitting this flag reproduces the plan that existed before components existed.
    _ = parser.add_argument(
        "--with-component",
        action="append",
        default=[],
        metavar="NAME",
        choices=sorted(OPT_IN_COMPONENTS),
        help="install an optional component (repeatable); default: none",
    )
    return parser


def _install_errors() -> tuple[type[Exception], ...]:
    """Error classes main() renders as INSTALL-BLOCK.

    RosterError sits behind PyYAML and only a group install reaches it, so the tuple
    is resolved here — at raise time, not at import time. Importing the roster parser
    at module scope made even `--dry-run` die with a bare ModuleNotFoundError on a host
    that has nothing but CPython, which is precisely the silent traceback the install
    guide promises never to show (docs/qa/P0-5: every unmet precondition is named).
    """
    base: tuple[type[Exception], ...] = (
        InstallAssetError,
        NodeConfigError,
        OSError,
        TrustKeyError,
        UnknownComponentError,
    )
    try:
        from automation.group_roster.validator import RosterError
    except ImportError:
        return base
    return (*base, RosterError)


def _load_roster(path: Path) -> Roster:
    """Parse the roster, importing its PyYAML-backed parser only when one is given."""
    try:
        from automation.group_roster.parser import load_roster
    except ImportError as error:
        raise TrustKeyError(
            "GROUP-ROSTER-DEPENDENCY-MISSING: --group-roster는 PyYAML을 요구한다 "
            + f"(배포판 패키지 또는 `pip install PyYAML`로 설치한다): {error}"
        ) from error
    return load_roster(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = _Arguments()
    _ = _parser().parse_args(argv, namespace=args)
    repo_root = Path(__file__).resolve().parents[2]
    try:
        config = load_node_config(args.config)
        key_text = args.update_trust_key.read_text(encoding="utf-8")
        trust = plan_install(key_text)
        expected = args.expect_update_trust_fingerprint
        if expected is not None and not fingerprints_match(trust.fingerprint, expected):
            result = CheckResult(
                "update-trust",
                Status.FAIL,
                f"bundled fingerprint {trust.fingerprint} does not match the published value",
            )
            print(render((result,), verdict_label="INSTALLED"))
            return 1
        inputs = build_inputs(repo_root, config, key_text, components=args.with_component)
        group_expected = args.expect_group_skill_fingerprint
        if args.group_roster is None and group_expected is not None:
            raise TrustKeyError(
                "GROUP-ROSTER-REQUIRED: --expect-group-skill-fingerprint에는 --group-roster가 필요하다"
            )
        if args.group_roster is not None:
            if group_expected is None:
                raise TrustKeyError(
                    "GROUP-TRUST-FINGERPRINT-REQUIRED: 관리자 지문을 대역외로 받아야 한다; "
                    + "GROUP-DISCORD-FORBIDDEN"
                )
            roster = _load_roster(args.group_roster)
            group_trust = plan_group_install(
                roster.admin.signing_public_key,
                principal=roster.admin.publisher_principal,
            )
            if not fingerprints_match(group_trust.fingerprint, group_expected):
                raise TrustKeyError(
                    "GROUP-TRUST-FINGERPRINT-MISMATCH: roster 서명키가 대역외 지문과 다르다; "
                    + "GROUP-DISCORD-FORBIDDEN"
                )
            group_spec = FileSpec(
                group_trust.path,
                group_trust.content,
                group_trust.mode,
                "root",
                "root",
            )
            inputs = replace(
                inputs,
                files=(*inputs.files, group_spec),
                trust_checks=(*inputs.trust_checks, "group-skill-trust"),
            )
        plan = build_plan(inputs, inspect_state(inputs))
    except _install_errors() as error:
        print(f"INSTALL-BLOCK: {error}", file=sys.stderr)
        return 1

    print("INSTALL PLAN")
    print(render_plan(plan))
    if args.dry_run:
        return 0
    if os.geteuid() != 0:
        result = CheckResult("root", Status.FAIL, "run the installer as root; dry-run needs no privilege")
        print(render((result,), verdict_label="INSTALLED"))
        return 1

    discord_config = args.discord_config or config.agent_home / ".hermes" / "interop" / "config.json"
    executor = RealExecutor(
        ExecutionContext(
            config,
            repo_root,
            discord_config,
            expected,
            args.expect_group_skill_fingerprint,
        )
    )
    results = apply_plan(plan, executor)
    print(render(results, verdict_label="INSTALLED"))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
