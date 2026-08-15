#!/usr/bin/env python3
"""Root-owned helper that always operates on both configured Hermes gateways."""

from __future__ import annotations

import pwd
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, TypeAlias


Command: TypeAlias = tuple[str, ...]
Run: TypeAlias = Callable[[Command], int]
UidFor: TypeAlias = Callable[[str], int]


class GatewayAction(StrEnum):
    RESTART = "restart"
    HEALTH = "health"


_SYSTEMCTL_VERBS: Final[Mapping[GatewayAction, str]] = MappingProxyType({
    GatewayAction.RESTART: "restart",
    GatewayAction.HEALTH: "is-active",
})


@dataclass(frozen=True, slots=True)
class Gateway:
    account: str
    unit: str


@dataclass(frozen=True, slots=True)
class GatewayPair:
    agent: Gateway
    peer: Gateway


@dataclass(frozen=True, slots=True)
class GatewayEffects:
    uid_for: UidFor
    run: Run


def _systemctl_verb(action: GatewayAction) -> str:
    return _SYSTEMCTL_VERBS[action]


def _command(gateway: Gateway, uid: int, verb: str) -> Command:
    return (
        "/usr/sbin/runuser",
        "-u",
        gateway.account,
        "--",
        "/usr/bin/env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        "/usr/bin/systemctl",
        "--user",
        verb,
        gateway.unit,
    )


def run_pair(pair: GatewayPair, action: GatewayAction, effects: GatewayEffects) -> int:
    """Attempt the action for agent and peer without short-circuiting on one failure."""
    verb = _systemctl_verb(action)
    succeeded = True
    for gateway in (pair.agent, pair.peer):
        try:
            uid = effects.uid_for(gateway.account)
        except KeyError:
            succeeded = False
            continue
        if effects.run(_command(gateway, uid, verb)) != 0:
            succeeded = False
    return 0 if succeeded else 1


def _uid_for(account: str) -> int:
    return pwd.getpwnam(account).pw_uid


def _run(command: Command) -> int:
    try:
        return subprocess.run(command, check=False).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def configured_pair() -> GatewayPair:
    return GatewayPair(
        agent=Gateway(account="$NODE_AGENT_ACCOUNT", unit="$NODE_AGENT_GATEWAY_UNIT"),
        peer=Gateway(account="$NODE_PEER_ACCOUNT", unit="$NODE_PEER_GATEWAY_UNIT"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: autophagy-gateway-pair <restart|health>", file=sys.stderr)
        return 2
    try:
        action = GatewayAction(arguments[0])
    except ValueError:
        print("gateway pair action must be restart or health", file=sys.stderr)
        return 2
    return run_pair(configured_pair(), action, GatewayEffects(uid_for=_uid_for, run=_run))


if __name__ == "__main__":
    raise SystemExit(main())
