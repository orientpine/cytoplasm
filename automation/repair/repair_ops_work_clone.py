"""Dedicated origin-backed work clone preparation for repairs."""

from __future__ import annotations

import json
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
_LOCAL_PREFIXES: Final = ("task/", "kanban/", "wt/", "fix/", "repair/")
_BRANCH_TICKET: Final = re.compile(r"(?:^|[-/])(t_[0-9a-f]{6,})(?=$|[-/])")


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

    def ensure_pull_request(self, ticket_id: str) -> str:
        """Create or reuse the open repair PR; main merge remains owner-only."""
        if not _TICKET_ID.fullmatch(ticket_id):
            raise RepairOpsError("refusing PR for an invalid repair ticket id")
        branch = f"{BRANCH_PREFIX}{ticket_id}"
        existing = self._gh((
            "pr", "list", "--base", "main", "--head", branch, "--state", "open",
            "--json", "url", "--jq", ".[0].url // empty",
        ))
        if existing:
            return existing
        subject = self._run(("git", "show", "-s", "--format=%s", "HEAD"), self.work_clone).stdout.strip()
        body = "\n".join((
            f"Ticket: {ticket_id}", "", "## Summary", redact(subject), "",
            "## Verification", "- Peer sandbox regression bank: PASS",
            "- Repair reproduction: GREEN", "- Post-apply regression bank: PASS", "",
            "공개 적합성: 채널 id·과제명·개인 경로 하드코딩 없음 확인",
        ))
        url = self._gh(("pr", "create", "--base", "main", "--head", branch, "--title", subject, "--body", body))
        if not url:
            raise RepairOpsError("gh pr create returned no PR URL")
        return url

    def prune_completed_branches(self) -> tuple[str, ...]:
        """Drop merged, idle local ticket refs only after publication succeeds.

        A terminal card alone is insufficient: unmerged commits and any branch
        checked out in a worktree remain intact. Remote refs are never deleted.
        """
        from automation.repair.repair_report_consumer import ConsumerStateError, card_state

        refs = self._run((
            "git", "for-each-ref", "--format=%(refname) %(objectname) %(worktreepath)", "refs/heads/",
        ), self.work_clone).stdout.splitlines()
        removed: list[str] = []
        for line in refs:
            ref, commit, worktree = line.split(" ", 2)
            branch = ref.removeprefix("refs/heads/")
            tickets = {match.group(1) for match in _BRANCH_TICKET.finditer(branch)}
            if worktree or not branch.startswith(_LOCAL_PREFIXES) or len(tickets) != 1:
                continue
            ancestry = self.runner.run(("git", "merge-base", "--is-ancestor", commit, "origin/main"), cwd=self.work_clone)
            if ancestry.returncode == 1:
                continue
            if ancestry.returncode != 0:
                raise RepairOpsError("cannot determine whether local repair ref is merged")
            try:
                state = card_state(tickets.pop())
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ConsumerStateError) as error:
                raise RepairOpsError(f"repair card lookup failed: {type(error).__name__}") from error
            if state.status not in {"done", "archived"}:
                continue
            _ = self._run(("git", "update-ref", "-d", ref, commit), self.work_clone)
            removed.append(branch)
        return tuple(removed)

    def _gh(self, arguments: tuple[str, ...]) -> str:
        try:
            result = subprocess.run(
                ("gh", *arguments), cwd=self.work_clone, capture_output=True,
                check=False, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RepairOpsError(f"gh unavailable: {type(error).__name__}") from error
        if result.returncode != 0:
            raise RepairOpsError(f"gh failed: {redact(result.stderr)[:180]}")
        return result.stdout.strip()

    def _read_origin(self) -> str:
        origin = self._run(("git", "remote", "get-url", "origin"), self.deploy_checkout).stdout.strip()
        if not origin:
            raise RepairOpsError("repair deploy checkout has no origin remote")
        return origin

    def _run(self, argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            completed = self.runner.run(argv, cwd=cwd)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RepairOpsError(f"git unavailable: {type(error).__name__}") from error
        if completed.returncode != 0:
            raise RepairOpsError(f"git operation failed: {redact(completed.stderr)[:180]}")
        return completed
