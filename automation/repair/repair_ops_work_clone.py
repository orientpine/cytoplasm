"""Dedicated origin-backed work clone preparation for repairs."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from automation.repair.repair_ops_git import GitRunner, RepairOpsError, SubprocessGitRunner
from automation.repair.repair_redaction import redact

# A ticket id and nothing else. This is what keeps `main` — or any path that
# could resolve to it — out of the push refspec (root AGENTS.md 「수리 반영 경로 규칙」).
_TICKET_ID: Final = re.compile(r"^t_[0-9a-f]{6,}$")
BRANCH_PREFIX: Final = "repair/"


@dataclass(frozen=True, slots=True)
class RepairWorkClone:
    """Refresh the mutable repair clone while treating the deploy checkout as read-only."""

    deploy_checkout: Path
    work_clone: Path
    runner: GitRunner = field(default_factory=SubprocessGitRunner)

    def prepare(self) -> Path:
        """Clone from origin when absent, then reset the work clone to origin/main."""
        origin = self._read_origin()
        if not self.work_clone.exists():
            _ = self._run(("git", "clone", origin, str(self.work_clone)), self.work_clone.parent)
        _ = self._run(("git", "fetch", "origin"), self.work_clone)
        _ = self._run(("git", "reset", "--hard", "origin/main"), self.work_clone)
        _ = self._run(("git", "clean", "-fd"), self.work_clone)
        return self.work_clone

    def push_branch(
        self,
        ticket_id: str,
        ssh_key: Path | None = None,
        known_hosts: Path | None = None,
    ) -> str:
        """Publish the repaired work clone as ``repair/<ticket>`` — never as main.

        The owner merges the branch on GitHub; automation must not fast-forward
        main itself. A non-ticket argument is refused BEFORE git runs so a crafted
        id can never widen the refspec.

        ``known_hosts`` is pinned rather than bypassed: this is the one path that
        carries a write credential, and ``accept-new`` would trust whatever key
        answers. The unit cannot reach ``~/.ssh/known_hosts`` (ProtectHome), so
        the file has to live somewhere the sandbox can see.
        """
        if not _TICKET_ID.match(ticket_id):
            raise RepairOpsError(f"refusing to push: {redact(ticket_id)[:40]!r} is not a ticket id")
        branch = f"{BRANCH_PREFIX}{ticket_id}"
        argv: tuple[str, ...] = ("git",)
        if ssh_key is not None:
            ssh = f"ssh -i {ssh_key} -o IdentitiesOnly=yes"
            if known_hosts is not None:
                ssh += f" -o UserKnownHostsFile={known_hosts} -o StrictHostKeyChecking=yes"
            argv += ("-c", f"core.sshCommand={ssh}")
        argv += ("push", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}")
        _ = self._run(argv, self.work_clone)
        return branch

    def _read_origin(self) -> str:
        origin = self._run(("git", "remote", "get-url", "origin"), self.deploy_checkout).stdout.strip()
        if not origin:
            raise RepairOpsError("repair deploy checkout has no origin remote")
        return origin

    def _run(self, argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
        completed = self.runner.run(argv, cwd=cwd)
        if completed.returncode != 0:
            raise RepairOpsError(f"git operation failed: {redact(completed.stderr)[:180]}")
        return completed
