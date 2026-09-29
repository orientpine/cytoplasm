"""Keep the peer account on the agent account's main model and fallback chain.

Owner decision 2026-09-29: one pair of models serves everything, and the agent account's
``~/.hermes/config.yaml`` (the ``model:`` and ``fallback_providers:`` blocks) is where the owner
sets it. Each Hermes account still reads its own config, so the peer drifts the moment only
the agent file changes. This operator command copies those two blocks to the peer, leaves
every other peer setting alone, and refuses to switch the peer to a provider it has no login
for (a gateway pointed at a provider without credentials stops answering).

    python3 -m automation.model_sync            # compare; exit 1 on drift
    python3 -m automation.model_sync --apply    # back up, then rewrite the peer blocks

The gateways load the config at start, so ``--apply`` ends by naming the agent+peer restart.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from automation.node_config import NodeConfigError, load_node_config

KEYS: Final = ("model", "fallback_providers")
Run = Callable[[tuple[str, ...], str | None], tuple[int, str, str]]

_READ_CONFIG: Final = 'cat "$HOME/.hermes/config.yaml"'
_READ_LOGINS: Final = (
    "python3 -c 'import json,pathlib;p=pathlib.Path.home()/\".hermes\"/\"auth.json\";"
    "d=json.loads(p.read_text()) if p.is_file() else {};"
    "print(\"\\n\".join(sorted((d.get(\"credential_pool\") or {}).keys())))'"
)
_WRITE_CONFIG: Final = (
    "python3 -c 'import os,shutil,sys,time,pathlib;"
    "c=pathlib.Path.home()/\".hermes\"/\"config.yaml\";"
    "b=c.with_name(\"config.yaml.bak-model-sync-\"+time.strftime(\"%Y%m%dT%H%M%SZ\",time.gmtime()));"
    "shutil.copy2(c,b);t=c.with_name(\".config.yaml.model-sync\");"
    "fd=os.open(t,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600);os.write(fd,sys.stdin.read().encode());"
    "os.close(fd);os.replace(t,c);print(b)'"
)


class SyncError(RuntimeError):
    """An account's config or logins could not be read or written."""


@dataclass(frozen=True, slots=True)
class Plan:
    source: str
    target: str
    wanted: dict[str, str]
    current: dict[str, str]
    target_text: str
    missing_logins: tuple[str, ...]

    @property
    def in_sync(self) -> bool:
        return all(_norm(self.wanted.get(key)) == _norm(self.current.get(key)) for key in KEYS)


def _norm(block: str | None) -> tuple[str, ...]:
    return tuple(line.rstrip() for line in (block or "").splitlines() if line.strip())


def _spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Top-level key -> [start, end) line range; comments and blanks belong to no block."""
    starts = [
        (index, line.partition(":")[0].strip())
        for index, line in enumerate(lines)
        if line[:1] not in ("", " ", "\t", "#", "-")
    ]
    spans: dict[str, tuple[int, int]] = {}
    for position, (start, key) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
            end -= 1
        spans[key] = (start, end)
    return spans


def blocks(text: str) -> dict[str, str]:
    lines = text.splitlines()
    return {
        key: "\n".join(lines[start:end])
        for key, (start, end) in _spans(lines).items()
        if key in KEYS
    }


def replace_blocks(text: str, wanted: dict[str, str]) -> str:
    lines = text.splitlines()
    for key in KEYS:
        if key not in wanted:
            continue
        span = _spans(lines).get(key)
        replacement = wanted[key].splitlines()
        if span is None:
            lines = [*lines, "", *replacement]
        else:
            lines = [*lines[: span[0]], *replacement, *lines[span[1]:]]
    return "\n".join(lines) + "\n"


def providers(wanted: dict[str, str]) -> tuple[str, ...]:
    found: list[str] = []
    for block in wanted.values():
        for line in block.splitlines():
            key, _, value = line.strip().lstrip("- ").partition(":")
            name = value.strip().strip("'\"")
            if key.strip() == "provider" and name and not name.startswith("custom:"):
                found.append(name)
    return tuple(dict.fromkeys(found))


def _child(argv: tuple[str, ...], stdin: str | None) -> tuple[int, str, str]:
    try:
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", error.__class__.__name__
    return done.returncode, done.stdout, done.stderr


def _as(account: str, script: str, run: Run, stdin: str | None = None) -> str:
    code, out, err = run(("sudo", "-n", "-u", account, "-H", "bash", "-c", script), stdin)
    if code != 0:
        tail = (err.strip().splitlines() or [f"rc={code}"])[-1][:160]
        raise SyncError(f"{account}: {tail}")
    return out


def plan(source: str, target: str, run: Run = _child) -> Plan:
    wanted = blocks(_as(source, _READ_CONFIG, run))
    if "model" not in wanted:
        raise SyncError(f"{source}: config.yaml has no model: block")
    target_text = _as(target, _READ_CONFIG, run)
    logins = frozenset(_as(target, _READ_LOGINS, run).split())
    missing = tuple(name for name in providers(wanted) if name not in logins)
    return Plan(source, target, wanted, blocks(target_text), target_text, missing)


def apply(current: Plan, run: Run = _child) -> str:
    new_text = replace_blocks(current.target_text, current.wanted)
    return _as(current.target, _WRITE_CONFIG, run, new_text).strip()


def render(current: Plan) -> str:
    lines = [f"MODEL-SYNC source={current.source} target={current.target}"]
    for key in KEYS:
        lines += [f"  [{current.source}] {line}" for line in _norm(current.wanted.get(key))]
        lines += [f"  [{current.target}] {line}" for line in _norm(current.current.get(key))]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, run: Run = _child) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.model_sync")
    _ = parser.add_argument("--apply", action="store_true", help="back up and rewrite the peer blocks")
    args = parser.parse_args(argv)
    try:
        config = load_node_config()
        source, target = config.agent_account, config.peer_account
    except (NodeConfigError, OSError):
        source, target = "agent", "peer"
    try:
        current = plan(source, target, run)
    except SyncError as error:
        print(f"MODEL-SYNC-ERROR {error}", file=sys.stderr)
        return 3
    print(render(current))
    if current.in_sync:
        print("MODEL-SYNC in-sync — 변경 없음")
        return 0
    if not args.apply:
        print(f"MODEL-SYNC drift — {target} 가 {source} 와 다르다. --apply 로 맞춘다")
        return 1
    if current.missing_logins:
        for name in current.missing_logins:
            print(f"MODEL-SYNC-BLOCKED {target} 에 {name} 로그인이 없다 — "
                  f"sudo -u {target} -H hermes auth add {name} --type oauth --no-browser", file=sys.stderr)
        return 2
    try:
        backup = apply(current, run)
    except SyncError as error:
        print(f"MODEL-SYNC-ERROR {error}", file=sys.stderr)
        return 3
    print(f"MODEL-SYNC applied backup={backup}")
    print("MODEL-SYNC restart — agent·peer 게이트웨이를 함께 재시작한다(docs/guide/operations.md §2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
