"""Managed-skill sync pipeline (MS-S4): fetch → verify → quarantine → mark.

Runs the automatic half of the managed skill channel: sync the pre-approved
remote once, then for every opted-in skill walk its new release tags in
ascending sequence order, verify each (MS-S1), stage it into quarantine
(MS-S4 — NEVER activate, SI-1), and only after a successful stage mark the
durable state (MS-S2) and save it. Failure isolation is per RELEASE: one bad
release is recorded and the walk continues to later tags (verify's chain
check fails-closed any release that depends on a skipped one), and one
skill's failure never aborts the others; multiple staged releases of one
skill collapse into ONE batched summary entry (governance: update batching).
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final

from .fetch import GitRunner, ManagedFetchError, list_release_tags, sync_remote
from .quarantine import QuarantineError, stage_candidate
from .state import ManagedSyncState, StateError, add_revoked, record_verified, save_state
from .verify import ManagedVerifyError, verify_release


@dataclass(frozen=True, slots=True)
class SkillOptions:
    """Per-skill sync policy: explicit opt-in plus an optional sequence pin."""

    opt_in: bool
    pin: int | None = None


@dataclass(frozen=True, slots=True)
class SyncConfig:
    """Pipeline config; structurally satisfies fetch.FetchConfig AND verify.VerifyConfig."""

    remote_url: str
    publisher: str
    publisher_principal: str
    allowed_signers: Path
    mirror_dir: Path
    ssh_key_path: Path
    quarantine_dir: Path
    state_path: Path
    skills: Mapping[str, SkillOptions]
    live_root: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "skills", MappingProxyType(dict(self.skills)))


@dataclass(frozen=True, slots=True)
class StagedRelease:
    """One batched per-skill summary: the newest release staged this run."""

    skill: str
    sequence: int
    digest: str


@dataclass(frozen=True, slots=True)
class SkippedSkill:
    skill: str
    reason: str


@dataclass(frozen=True, slots=True)
class FailedRelease:
    """One per-skill failure with its stable reason-class string."""

    skill: str
    tag: str
    reason: str


@dataclass(frozen=True, slots=True)
class RemovalRequest:
    """Owner-gated request to detach a live skill whose digest is revoked (MS-S5)."""

    skill: str
    digest: str
    reason: str


@dataclass(frozen=True, slots=True)
class SyncReport:
    staged: tuple[StagedRelease, ...]
    skipped: tuple[SkippedSkill, ...]
    failed: tuple[FailedRelease, ...]
    removal_requests: tuple[RemovalRequest, ...] = ()
    rolled_back: tuple[StagedRelease, ...] = ()


_SKILL_ERRORS: Final = (ManagedFetchError, ManagedVerifyError, QuarantineError, StateError)


def _reason_class(error: Exception) -> str:
    if isinstance(error, ManagedVerifyError):
        return error.prefix
    if isinstance(error, QuarantineError):
        return "QUARANTINE"
    if isinstance(error, ManagedFetchError):
        return "FETCH"
    return "STATE"


@dataclass(slots=True)
class _SyncRun:
    """Mutable per-run accumulator keeping one skill's failure isolated."""

    config: SyncConfig
    runner: GitRunner
    mirror: Path
    staged: list[StagedRelease] = field(default_factory=list)
    skipped: list[SkippedSkill] = field(default_factory=list)
    failed: list[FailedRelease] = field(default_factory=list)
    rolled_back: list[StagedRelease] = field(default_factory=list)

    def sync_skill(
        self,
        skill: str,
        options: SkillOptions,
        state: ManagedSyncState,
        *,
        allow_rollback: int | None = None,
    ) -> ManagedSyncState:
        """Walk new release tags ascending; a failure stops only THAT release.

        A failed release is recorded and the walk continues: any later release
        that genuinely depends on it fails its own chain check (CHAIN-BREAK)
        in ``verify_release``, so skipping cannot smuggle in an unverified
        artifact. Only the tag LISTING failing aborts this skill's walk.

        ``allow_rollback`` (SI-6, manual sync only) force-processes the ONE tag
        whose sequence equals it even at/below ``highest_sequence``: re-verify
        with ``allow_rollback=True`` (bypasses SEQUENCE-REPLAY) and re-stage to
        quarantine WITHOUT ``record_verified`` — durable state never moves
        backward, and activation stays owner-gated.
        """
        latest: StagedRelease | None = None
        rolled_back: StagedRelease | None = None
        try:
            tags = list_release_tags(self.mirror, skill, self.runner)
        except ManagedFetchError as error:
            self.failed.append(FailedRelease(skill=skill, tag="", reason=_reason_class(error)))
            return state
        for tag in tags:
            current = state.skill(skill).highest_sequence
            is_rollback = (
                allow_rollback is not None
                and tag.sequence == allow_rollback
                and tag.sequence <= current
            )
            if tag.sequence <= current and not is_rollback:
                continue
            if options.pin is not None and tag.sequence > options.pin:
                break
            try:
                verified = verify_release(
                    self.mirror,
                    tag.tag_name,
                    self.config,
                    state.skill(skill),
                    self.runner,
                    allow_rollback=is_rollback,
                )
                _ = stage_candidate(verified, self.config.quarantine_dir)
                release = StagedRelease(
                    skill=skill, sequence=verified.sequence, digest=verified.digest
                )
                if is_rollback:
                    rolled_back = release
                else:
                    state = record_verified(state, skill, verified.sequence, verified.digest)
                    latest = release
                state = add_revoked(state, skill, verified.manifest.revoked_digests)
                save_state(self.config.state_path, state)
            except _SKILL_ERRORS as error:
                self.failed.append(
                    FailedRelease(skill=skill, tag=tag.tag_name, reason=_reason_class(error))
                )
        if latest is not None:
            self.staged.append(latest)
        if rolled_back is not None:
            self.rolled_back.append(rolled_back)
        return state


def sync_all(
    config: SyncConfig,
    state: ManagedSyncState,
    runner: GitRunner = subprocess.run,
    *,
    allow_rollback: int | None = None,
) -> SyncReport:
    """Fetch once, then verify → stage → state-mark every opted-in skill.

    State is marked AFTER each successful stage (per release) and saved
    immediately, so a crash never re-runs an already-staged release. The
    automatic pipeline ends at quarantine — activation stays owner-gated.
    ``allow_rollback`` (SI-6) re-verifies + re-stages exactly that sequence
    per opted-in skill without ever lowering ``highest_sequence``.
    """
    mirror = sync_remote(config, runner).mirror_dir
    run = _SyncRun(config=config, runner=runner, mirror=mirror)
    opted_in: list[str] = []
    for skill, options in sorted(config.skills.items()):
        if not options.opt_in:
            run.skipped.append(SkippedSkill(skill=skill, reason="not-opted-in"))
            continue
        opted_in.append(skill)
        state = run.sync_skill(skill, options, state, allow_rollback=allow_rollback)
    from .revoke import DEFAULT_LIVE_ROOT, check_live  # local import breaks the module cycle

    live_root = config.live_root if config.live_root is not None else DEFAULT_LIVE_ROOT
    return SyncReport(
        staged=tuple(run.staged),
        skipped=tuple(run.skipped),
        failed=tuple(run.failed),
        rolled_back=tuple(run.rolled_back),
        removal_requests=check_live(state, opted_in, live_root=live_root),
    )
