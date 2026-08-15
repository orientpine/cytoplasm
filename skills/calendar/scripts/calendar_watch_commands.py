from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import calendar_confirm
import calendar_gate
from calendar_pending import PendingConfirm
from calendar_watch_diagnostics import (
    HTTP_STATUS,
    RETRY_AFTER,
    ConfirmWatchError,
    coerce_text,
    extract_int,
    extract_text,
)


@dataclass(frozen=True, slots=True)
class CliCommands:
    calendar_cli: Path

    def confirm(self, entry: PendingConfirm, owner_id: str) -> None:
        try:
            authorization = calendar_confirm.create_watcher_authorization(entry, owner_id)
        except (calendar_gate.GateError, OSError) as error:
            raise ConfirmWatchError("watcher authorization creation failed") from error
        try:
            self._run(
                "confirm",
                "--draft",
                entry.draft_id,
                "--watch-authorization",
                str(authorization),
            )
        finally:
            authorization.unlink(missing_ok=True)

    def discard(self, draft_id: str) -> None:
        self._run("discard", "--draft", draft_id)

    def _run(self, *arguments: str, environment: Mapping[str, str] | None = None) -> None:
        action = arguments[0] if arguments else "unknown"
        try:
            result = subprocess.run(
                [sys.executable, str(self.calendar_cli), *arguments],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                cwd=str(Path.home()),
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            combined = f"{coerce_text(error.stderr)}\n{coerce_text(error.stdout)}"
            raise ConfirmWatchError(
                "confirmation command timed out",
                stage=f"subprocess.{action}",
                fatal=True,
                stdout=error.stdout,
                stderr=error.stderr,
                timeout_seconds=180,
                http_status=extract_int(HTTP_STATUS, combined),
                retry_after=extract_text(RETRY_AFTER, combined),
                cause_type=type(error).__name__,
            ) from error
        except OSError as error:
            raise ConfirmWatchError(
                "confirmation command could not start",
                stage=f"subprocess.{action}",
                fatal=True,
                cause_type=type(error).__name__,
            ) from error
        if result.returncode != 0:
            combined = f"{result.stderr}\n{result.stdout}"
            raise ConfirmWatchError(
                "confirmation command rejected",
                stage=f"subprocess.{action}",
                fatal=True,
                child_returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                http_status=extract_int(HTTP_STATUS, combined),
                retry_after=extract_text(RETRY_AFTER, combined),
                cause_type="ChildProcessError",
            )
