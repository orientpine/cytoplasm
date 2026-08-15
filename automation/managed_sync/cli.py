"""Managed-skill sync CLI (MS-S6) — manual surface over fetch → verify → quarantine.

Subcommands (``python3 -m automation.managed_sync <subcommand>``):

- ``sync``: run one automatic pass (MS-S4 ``sync_all``, which ends at
  quarantine and already emits MS-S5 removal requests) and print the batched
  report in a deterministic, greppable line format (MS-E1 parses this into
  OBS-JSON — keep it stable).
- ``status``: render durable state (MS-S2) plus the pending-in-quarantine
  count per skill.
- ``mark-activated <skill>``: reconcile informational state from the
  authoritative live symlink after an independently confirmed mount/removal.
- ``activate-instructions <skill>``: print the EXACT owner-gated researcher
  command for the newest quarantined release; refuse on a live base-name
  collision (early advice mirroring the MS-N2 guard).

Runtime config lives at ``~/.hermes/managed-sync/config.json`` (outside the
checkout; tracked seed: ``configs/managed-sync.default.json``). Missing or
invalid config fails closed with exit 2 naming the offending key.

The publisher principal this subscriber trusts is NOT in that file: it is
``admin.publisher_principal`` in the group roster (``~/.hermes/roster.yaml``;
override ``AUTOPHAGY_ROSTER``), so the roster stays the single document that
declares who may publish. There is no built-in default — an install with no
readable, valid roster fails closed with exit 2 rather than trusting anyone.

``--allow-rollback <seq>`` is accepted ONLY by this manual ``sync``
subcommand (SI-6) and never by the cron wrapper. It threads into the
pipeline (``sync_all(..., allow_rollback=<seq>)``): the ONE tag matching
that sequence is re-verified (``verify_release(..., allow_rollback=True)``,
bypassing SEQUENCE-REPLAY) and re-staged to quarantine WITHOUT ever
lowering ``highest_sequence`` — activation still requires the owner ✅ gate
(SI-1). The deterministic ``SYNC-ROLLBACK-NOTE`` line records the
operator's intent in the state history (Codified decision 12).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Final, TypeAlias

from automation.managed_skills.manifest import MANAGED_PREFIX

from .fetch import ManagedFetchError
from .pipeline import SkillOptions, SyncConfig, SyncReport, sync_all
from .revoke import DEFAULT_LIVE_ROOT, LiveStateError, live_activated_digest
from .state import StateError, load_state, record_activated, save_state

CONFIG_ENV: Final = "MANAGED_SYNC_CONFIG"
DEFAULT_CONFIG_PATH: Final = Path("~/.hermes/managed-sync/config.json")
ROSTER_ENV: Final = "AUTOPHAGY_ROSTER"
DEFAULT_ROSTER_PATH: Final = Path("~/.hermes/roster.yaml")

_STRING_KEYS: Final = ("remote_url", "publisher")
_PATH_KEYS: Final = ("allowed_signers", "mirror_dir", "ssh_key_path", "quarantine_dir", "state_path")
_REQUIRED_KEYS: Final = frozenset({*_STRING_KEYS, *_PATH_KEYS, "skills"})
_SKILL_KEYS: Final = frozenset({"opt_in", "pin"})
_RELEASE_DIGEST: Final = re.compile(r"[0-9a-f]{64}\Z")

_JsonValue: TypeAlias = (
    str | int | float | bool | None | list["_JsonValue"] | dict[str, "_JsonValue"]
)


class ConfigError(Exception):
    """Runtime config is missing or invalid; the CLI must not run (exit 2)."""


class ActivateError(Exception):
    """No activation instruction can be produced safely (exit 1)."""


def config_path() -> Path:
    raw = os.environ.get(CONFIG_ENV)
    path = Path(raw) if raw else DEFAULT_CONFIG_PATH
    return path.expanduser()


def roster_path() -> Path:
    """Resolve the group roster path from the environment or the runtime default."""
    raw = os.environ.get(ROSTER_ENV, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_ROSTER_PATH.expanduser()


def _publisher_principal(path: Path) -> str:
    """Read the trusted publisher principal from the group roster, fail-closed."""
    try:
        # Lazy: the roster parser needs PyYAML, which must not break config-free surfaces.
        from automation.group_roster.parser import load_roster
        from automation.group_roster.validator import RosterError
    except ImportError as error:  # pragma: no cover - install defect, not a config defect
        raise ConfigError(f"cannot read group roster {path}: {error}") from error
    try:
        roster = load_roster(path)
    except RosterError as error:
        raise ConfigError(f"group roster does not declare a usable publisher: {error}") from error
    return roster.admin.publisher_principal


def _parse_skills(payload: _JsonValue) -> dict[str, SkillOptions]:
    if not isinstance(payload, dict):
        raise ConfigError("invalid value for key: skills")
    skills: dict[str, SkillOptions] = {}
    for name, options in payload.items():
        if not isinstance(options, dict) or frozenset(options) != _SKILL_KEYS:
            raise ConfigError(f"invalid value for key: skills.{name}")
        opt_in = options["opt_in"]
        if not isinstance(opt_in, bool):
            raise ConfigError(f"invalid value for key: skills.{name}.opt_in")
        pin = options["pin"]
        if pin is not None and (not isinstance(pin, int) or isinstance(pin, bool) or pin < 1):
            raise ConfigError(f"invalid value for key: skills.{name}.pin")
        skills[name] = SkillOptions(opt_in=opt_in, pin=pin)
    return skills


def load_config(path: Path) -> SyncConfig:
    """Parse the runtime config fail-closed, naming the offending key."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigError(f"managed-sync config not found: {path}") from error
    except OSError as error:
        raise ConfigError(f"cannot read managed-sync config: {path}") from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(f"managed-sync config is not valid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise ConfigError(f"managed-sync config must be a JSON object: {path}")
    for key in sorted(_REQUIRED_KEYS):
        if key not in payload:
            raise ConfigError(f"missing required key: {key}")
    for key in payload:
        if key not in _REQUIRED_KEYS:
            raise ConfigError(f"unknown config key: {key}")
    for key in (*_STRING_KEYS, *_PATH_KEYS):
        value = payload[key]
        if not isinstance(value, str) or not value:
            raise ConfigError(f"invalid value for key: {key}")
    paths = {key: Path(payload[key]).expanduser() for key in _PATH_KEYS}
    return SyncConfig(
        remote_url=payload["remote_url"],
        publisher=payload["publisher"],
        publisher_principal=_publisher_principal(roster_path()),
        allowed_signers=paths["allowed_signers"],
        mirror_dir=paths["mirror_dir"],
        ssh_key_path=paths["ssh_key_path"],
        quarantine_dir=paths["quarantine_dir"],
        state_path=paths["state_path"],
        skills=_parse_skills(payload["skills"]),
    )


def _render_report(report: SyncReport) -> None:
    for staged in report.staged:
        print(f"SYNC-STAGED skill={staged.skill} sequence={staged.sequence} digest={staged.digest}")
    for rollback in report.rolled_back:
        print(
            f"SYNC-ROLLBACK-STAGED skill={rollback.skill} sequence={rollback.sequence}"
            f" digest={rollback.digest}"
        )
    for skipped in report.skipped:
        print(f"SYNC-SKIPPED skill={skipped.skill} reason={skipped.reason}")
    for failed in report.failed:
        print(f"SYNC-FAILED skill={failed.skill} tag={failed.tag} reason={failed.reason}")
    for request in report.removal_requests:
        print(
            f"SYNC-REMOVAL-REQUEST skill={request.skill} digest={request.digest}"
            f" reason={request.reason}"
        )
    print(
        f"SYNC-SUMMARY staged={len(report.staged)} skipped={len(report.skipped)}"
        f" failed={len(report.failed)} removal_requests={len(report.removal_requests)}"
        f" rolled_back={len(report.rolled_back)}"
    )


def cmd_sync(args: argparse.Namespace) -> int:
    config = load_config(config_path())
    if args.allow_rollback is not None:
        print(
            f"SYNC-ROLLBACK-NOTE sequence={args.allow_rollback} note=manual rollback"
            " re-verify requested (SI-6); the matching sequence is re-staged to quarantine"
            " only — highest_sequence never moves backward and activation stays owner-gated"
        )
    try:
        state = load_state(config.state_path)
        report = sync_all(config, state, allow_rollback=args.allow_rollback)
    except (ManagedFetchError, StateError) as error:
        print(f"SYNC-FATAL reason={error}", file=sys.stderr)
        return 1
    _render_report(report)
    return 0


def _pending_count(quarantine_dir: Path, skill: str, activated_digest: str | None) -> int:
    skill_dir = quarantine_dir / skill
    if not skill_dir.is_dir():
        return 0
    return sum(
        1
        for entry in skill_dir.iterdir()
        if entry.is_dir()
        and _RELEASE_DIGEST.fullmatch(entry.name) is not None
        and entry.name != activated_digest
    )


def cmd_status(args: argparse.Namespace) -> int:
    del args
    config = load_config(config_path())
    try:
        state = load_state(config.state_path)
    except StateError as error:
        print(f"STATUS-ERROR: {error}", file=sys.stderr)
        return 1
    for name in sorted(set(config.skills) | set(state.skills)):
        record = state.skill(name)
        options = config.skills.get(name)
        opt_in = "true" if options is not None and options.opt_in else "false"
        activated = record.activated_digest if record.activated_digest is not None else "-"
        pending = _pending_count(config.quarantine_dir, name, record.activated_digest)
        print(
            f"STATUS skill={name} opt_in={opt_in} highest_sequence={record.highest_sequence}"
            f" activated_digest={activated} pending={pending}"
        )
    return 0


def cmd_mark_activated(args: argparse.Namespace) -> int:
    """Reconcile informational activation state from the authoritative live link."""
    skill: str = args.skill
    if not skill.startswith(MANAGED_PREFIX):
        print(f"ACTIVATION-MARK-ERROR: skill must start with {MANAGED_PREFIX}: {skill}", file=sys.stderr)
        return 2
    config = load_config(config_path())
    if skill not in config.skills:
        print(f"ACTIVATION-MARK-ERROR: skill is not configured: {skill}", file=sys.stderr)
        return 2
    live_root: Path = args.live_root if args.live_root is not None else DEFAULT_LIVE_ROOT
    try:
        digest = live_activated_digest(live_root / skill)
        state = record_activated(load_state(config.state_path), skill, digest)
        save_state(config.state_path, state)
    except (LiveStateError, StateError) as error:
        print(f"ACTIVATION-MARK-ERROR: {error}", file=sys.stderr)
        return 1
    rendered_digest = digest if digest is not None else "-"
    print(f"ACTIVATION-MARKED skill={skill} digest={rendered_digest}")
    return 0


def _newest_quarantined(quarantine_dir: Path, skill: str) -> Path:
    """Pick the quarantined release with the highest provenance sequence."""
    skill_dir = quarantine_dir / skill
    candidates: list[tuple[int, Path]] = []
    if skill_dir.is_dir():
        for entry in sorted(skill_dir.iterdir()):
            if not entry.is_dir() or _RELEASE_DIGEST.fullmatch(entry.name) is None:
                continue
            try:
                provenance = json.loads((entry / "provenance.json").read_text(encoding="utf-8"))
                sequence = provenance["sequence"]
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
                raise ActivateError(f"unreadable provenance for {skill}/{entry.name}") from error
            if not isinstance(sequence, int) or isinstance(sequence, bool):
                raise ActivateError(f"unreadable provenance for {skill}/{entry.name}")
            candidates.append((sequence, entry))
    if not candidates:
        raise ActivateError(f"no quarantined release for skill: {skill}")
    return max(candidates, key=lambda candidate: candidate[0])[1]


def cmd_activate_instructions(args: argparse.Namespace) -> int:
    skill: str = args.skill
    if not skill.startswith(MANAGED_PREFIX):
        print(
            f"ACTIVATE-ERROR: managed skill name must start with {MANAGED_PREFIX}: {skill}",
            file=sys.stderr,
        )
        return 2
    config = load_config(config_path())
    base = skill.removeprefix(MANAGED_PREFIX)
    live_root: Path = args.live_root if args.live_root is not None else DEFAULT_LIVE_ROOT
    live_base = live_root / base
    if live_base.is_symlink() or live_base.exists():
        print(
            f"COLLISION-BLOCK: managed skill {skill} conflicts with live base {base};"
            " remove one with --remove",
            file=sys.stderr,
        )
        return 1
    try:
        release = _newest_quarantined(config.quarantine_dir, skill)
    except ActivateError as error:
        print(f"ACTIVATE-ERROR: {error}", file=sys.stderr)
        return 1
    print(f"automation/deploy-skill.sh {skill} --activate-managed {release}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.managed_sync",
        description="Managed-skill channel subscriber CLI (fetch/verify/quarantine only).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="run one fetch/verify/quarantine pass")
    sync_parser.add_argument(
        "--allow-rollback",
        type=int,
        metavar="SEQ",
        default=None,
        help="record intent to manually re-verify an already-seen sequence (SI-6; manual only)",
    )
    sync_parser.set_defaults(handler=cmd_sync)

    status_parser = subparsers.add_parser("status", help="render durable sync state")
    status_parser.set_defaults(handler=cmd_status)

    mark_parser = subparsers.add_parser(
        "mark-activated",
        help="reconcile informational activation state from the authoritative live symlink",
    )
    mark_parser.add_argument("skill", help="configured managed skill name (managed-*)")
    mark_parser.add_argument(
        "--live-root",
        type=Path,
        default=None,
        help="live skill store root (test/ops injection; default /srv/autophagy-skills/live)",
    )
    mark_parser.set_defaults(handler=cmd_mark_activated)

    activate_parser = subparsers.add_parser(
        "activate-instructions",
        help="print the owner-gated activation command for the newest quarantined release",
    )
    activate_parser.add_argument("skill", help="managed skill name (managed-*)")
    activate_parser.add_argument(
        "--live-root",
        type=Path,
        default=None,
        help="live skill store root (test/ops injection; default /srv/autophagy-skills/live)",
    )
    activate_parser.set_defaults(handler=cmd_activate_instructions)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except ConfigError as error:
        print(f"CONFIG-ERROR: {error}", file=sys.stderr)
        return 2
