"""One independently reported roster refresh after the shared mirror fetch."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

from automation.group_roster import (
    RosterFetchConfig,
    RosterFetchError,
    refresh_roster,
)
from automation.managed_sync.fetch import ManagedFetchError, sync_roster_ref


class RosterTickConfig(Protocol):
    @property
    def mirror_dir(self) -> Path: ...

    @property
    def ssh_key_path(self) -> Path: ...

    @property
    def allowed_signers(self) -> Path: ...

    @property
    def publisher_principal(self) -> str: ...


def run(config: RosterTickConfig, roster_path: Path) -> None:
    """Refresh roster state without producing skill-pipeline state or failures."""
    try:
        sync_roster_ref(config)
    except ManagedFetchError:
        print("ROSTER-REJECTED reason=ROSTER-FETCH", file=sys.stderr)
        return
    try:
        result = refresh_roster(
            RosterFetchConfig(
                mirror_dir=config.mirror_dir,
                roster_path=roster_path,
                allowed_signers=config.allowed_signers,
                expected_principal=config.publisher_principal,
            )
        )
    except RosterFetchError as error:
        print(f"ROSTER-REJECTED reason={error.reason}", file=sys.stderr)
        return
    status = "UPDATED" if result.updated else "UNCHANGED"
    print(f"ROSTER-{status} path={roster_path}")
