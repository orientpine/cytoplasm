"""Opt-in installer components — unit sets the installer converges only when asked.

Opt-in means ABSENT by default, not disabled by default: a component nobody named
contributes no file and no timer at all, so a plan built without it is byte-for-byte the
plan that existed before the component was written. That property is what lets an
optional feature ship without changing every existing install.

사용자 유닛은 system 범위로 변환하지 않고 별도 scope로 보존해 수동 배포와 일치시킨다.
"""
from __future__ import annotations

import pwd
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal


class UnknownComponentError(RuntimeError):
    """A requested component name is not in the registry; nothing is installed."""


@dataclass(frozen=True, slots=True)
class OptInComponent:
    """One optional unit set, rendered and enabled exactly like the always-on units."""

    name: str
    #: Directory of `$NODE_*` unit templates, relative to the repository root.
    source: Path
    units: tuple[str, ...]
    scope: Literal["system", "user"] = "system"
    env_variables: tuple[str, ...] = ()

    @property
    def timers(self) -> tuple[str, ...]:
        """Only timers are enabled — a `.service` is started by its timer, not directly."""
        return tuple(unit for unit in self.units if unit.endswith(".timer"))


#: The one registry. `installer.py --with-component <name>` validates against these keys.
OPT_IN_COMPONENTS: Final[Mapping[str, OptInComponent]] = {
    "managed-sync": OptInComponent(
        name="managed-sync",
        source=Path("automation/managed_sync/systemd"),
        units=(
            "autophagy-managed-sync.service",
            "autophagy-managed-sync.timer",
        ),
    ),
    "report-hub": OptInComponent(
        name="report-hub",
        source=Path("automation/report_hub/systemd"),
        units=("report-hub-collector.service", "report-hub-dashboard.service"),
        scope="user",
        env_variables=(
            "DISCORD_BOT_TOKEN", "REPORT_HUB_GUILD_ID", "REPORT_HUB_CHANNEL_NAME",
            "REPORT_HUB_DB", "REPORT_HUB_QUARANTINE_LOG", "REPORT_HUB_PEERS_FILE",
            "REPORT_HUB_POLL_SECONDS", "REPORT_HUB_BIND_HOST", "REPORT_HUB_BIND_PORT",
            "REPORT_HUB_DASHBOARD_USER", "REPORT_HUB_DASHBOARD_PASSWORD_SHA256",
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class EnsureSymlink:
    path: Path
    target: Path
    owner: str


@dataclass(frozen=True, slots=True)
class EnableUserUnit:
    name: str
    owner: str

    def command(self) -> tuple[str, ...]:
        uid = pwd.getpwnam(self.owner).pw_uid
        return ("runuser", "-u", self.owner, "--", "env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}", "systemctl", "--user")


def resolve_components(names: Sequence[str]) -> tuple[OptInComponent, ...]:
    """Resolve requested component names, refusing unknown ones fail-closed.

    Silently dropping a name the operator typed would install less than they asked for
    and still report success — the one outcome an installer must never produce.
    """
    unknown = sorted(set(names) - set(OPT_IN_COMPONENTS))
    if unknown:
        known = ", ".join(sorted(OPT_IN_COMPONENTS))
        raise UnknownComponentError(
            f"알 수 없는 설치 컴포넌트: {', '.join(unknown)}; 알려진 이름: {known}"
        )
    return tuple(OPT_IN_COMPONENTS[name] for name in sorted(set(names)))
