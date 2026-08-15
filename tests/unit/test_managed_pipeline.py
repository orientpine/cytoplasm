from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from automation.managed_skills.manifest import ManagedManifest
from automation.managed_sync import pipeline, quarantine
from automation.managed_sync.fetch import FetchResult, ManagedFetchError, ReleaseTag
from automation.managed_sync.state import ManagedSyncState, SkillState, load_state
from automation.managed_sync.verify import ManagedVerifyError, VerifiedRelease


def _digest(sequence: int) -> str:
    return "abcdef"[sequence] * 64


def _tag(skill: str, sequence: int) -> ReleaseTag:
    return ReleaseTag(skill=skill, sequence=sequence, tag_name=f"{skill}/v{sequence}")


def _manifest(skill: str, sequence: int) -> ManagedManifest:
    return ManagedManifest(
        schema_version=1,
        publisher="testlab",
        skill=skill,
        release_sequence=sequence,
        source_commit=None,
        skill_sha256=_digest(sequence),
        previous_sha256=None,
        compatibility="any",
        breaking=False,
        revoked_digests=(),
        changelog="test release",
        migration=None,
    )


def _unused_runner(
    args: list[str],
    /,
    *,
    env: dict[str, str],
    capture_output: bool,
    text: bool,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    del env, capture_output, text, timeout
    raise AssertionError(f"git must not run in pipeline unit tests: {args}")


@dataclass(slots=True)
class FakeChannel:
    """Fakes fetch/verify/stage at the pipeline seam and records every call."""

    tags: dict[str, tuple[ReleaseTag, ...]]
    tree: Path
    verify_errors: dict[str, ManagedVerifyError] = field(default_factory=dict)
    list_errors: dict[str, ManagedFetchError] = field(default_factory=dict)
    stage_errors: frozenset[str] = frozenset()
    listed: list[str] = field(default_factory=list)
    verified_tags: list[str] = field(default_factory=list)
    rollback_verified: list[str] = field(default_factory=list)
    staged: list[tuple[str, int, str]] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pipeline, "sync_remote", self._sync_remote)
        monkeypatch.setattr(pipeline, "list_release_tags", self._list_release_tags)
        monkeypatch.setattr(pipeline, "verify_release", self._verify_release)
        monkeypatch.setattr(pipeline, "stage_candidate", self._stage_candidate)

    def _sync_remote(self, config: pipeline.SyncConfig, runner: object) -> FetchResult:
        del runner
        return FetchResult(mirror_dir=config.mirror_dir, fetched=True, cloned=False)

    def _list_release_tags(
        self, mirror: Path, skill: str, runner: object
    ) -> tuple[ReleaseTag, ...]:
        del mirror, runner
        error = self.list_errors.get(skill)
        if error is not None:
            raise error
        self.listed.append(skill)
        return self.tags.get(skill, ())

    def _verify_release(
        self,
        mirror: Path,
        tag: str,
        config: pipeline.SyncConfig,
        state: SkillState,
        runner: object,
        *,
        allow_rollback: bool = False,
    ) -> VerifiedRelease:
        del mirror, config, state, runner
        error = self.verify_errors.get(tag)
        if error is not None:
            raise error
        self.verified_tags.append(tag)
        if allow_rollback:
            self.rollback_verified.append(tag)
        skill, _, version = tag.partition("/v")
        sequence = int(version)
        return VerifiedRelease(
            skill=skill,
            sequence=sequence,
            digest=_digest(sequence),
            manifest=_manifest(skill, sequence),
            tree_path=self.tree,
            tag=tag,
        )

    def _stage_candidate(self, verified: VerifiedRelease, quarantine_root: Path) -> Path:
        if verified.digest in self.stage_errors:
            raise quarantine.QuarantineError("simulated staging failure")
        self.staged.append((verified.skill, verified.sequence, verified.digest))
        return quarantine_root / verified.skill / verified.digest


def _config(tmp_path: Path, skills: Mapping[str, pipeline.SkillOptions]) -> pipeline.SyncConfig:
    return pipeline.SyncConfig(
        remote_url="ssh://feed.example/managed-skills.git",
        publisher="testlab",
        publisher_principal="publisher-testlab@autophagy",
        allowed_signers=tmp_path / "allowed_signers",
        mirror_dir=tmp_path / "mirror",
        ssh_key_path=tmp_path / "feed_key",
        quarantine_dir=tmp_path / "quarantine",
        state_path=tmp_path / "state.json",
        skills=skills,
    )


def test_sync_all_when_two_new_tags_then_stages_ascending_and_advances_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(
        tags={"managed-demo": (_tag("managed-demo", 1), _tag("managed-demo", 2))},
        tree=tmp_path,
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert channel.staged == [
        ("managed-demo", 1, _digest(1)),
        ("managed-demo", 2, _digest(2)),
    ]
    assert report.staged == (pipeline.StagedRelease("managed-demo", 2, _digest(2)),)
    assert report.failed == ()
    assert report.removal_requests == ()
    assert load_state(config.state_path).skill("managed-demo").highest_sequence == 2


def test_sync_all_when_one_skill_fails_verify_then_other_skills_still_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(
        tags={
            "managed-alpha": (_tag("managed-alpha", 1),),
            "managed-beta": (_tag("managed-beta", 1),),
        },
        tree=tmp_path,
        verify_errors={
            "managed-alpha/v1": ManagedVerifyError("BAD-SIGNATURE", "invalid signature")
        },
    )
    channel.install(monkeypatch)
    config = _config(
        tmp_path,
        {
            "managed-alpha": pipeline.SkillOptions(opt_in=True),
            "managed-beta": pipeline.SkillOptions(opt_in=True),
        },
    )

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert report.failed == (
        pipeline.FailedRelease("managed-alpha", "managed-alpha/v1", "BAD-SIGNATURE"),
    )
    assert channel.staged == [("managed-beta", 1, _digest(1))]
    saved = load_state(config.state_path)
    assert saved.skill("managed-beta").highest_sequence == 1
    assert saved.skill("managed-alpha").highest_sequence == 0


def test_sync_all_when_pin_caps_walk_then_sequences_above_pin_stay_unstaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(
        tags={
            "managed-demo": (
                _tag("managed-demo", 1),
                _tag("managed-demo", 2),
                _tag("managed-demo", 3),
            )
        },
        tree=tmp_path,
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True, pin=2)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert channel.staged == [
        ("managed-demo", 1, _digest(1)),
        ("managed-demo", 2, _digest(2)),
    ]
    assert report.staged == (pipeline.StagedRelease("managed-demo", 2, _digest(2)),)
    assert report.failed == ()
    assert load_state(config.state_path).skill("managed-demo").highest_sequence == 2


def test_sync_all_when_skill_is_not_opted_in_then_it_is_skipped_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(tags={"managed-demo": (_tag("managed-demo", 1),)}, tree=tmp_path)
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=False)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert channel.listed == []
    assert channel.staged == []
    assert report.skipped == (pipeline.SkippedSkill("managed-demo", "not-opted-in"),)
    assert not config.state_path.exists()


def test_sync_all_when_sequence_already_verified_then_only_newer_tags_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(
        tags={"managed-demo": (_tag("managed-demo", 1), _tag("managed-demo", 2))},
        tree=tmp_path,
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True)})
    state = ManagedSyncState(
        skills={"managed-demo": SkillState(highest_sequence=1, last_verified_digest=_digest(1))}
    )

    report = pipeline.sync_all(config, state, _unused_runner)

    assert channel.verified_tags == ["managed-demo/v2"]
    assert report.staged == (pipeline.StagedRelease("managed-demo", 2, _digest(2)),)


def test_sync_all_when_staging_fails_then_state_marks_only_prior_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(
        tags={"managed-demo": (_tag("managed-demo", 1), _tag("managed-demo", 2))},
        tree=tmp_path,
        stage_errors=frozenset({_digest(2)}),
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert report.failed == (
        pipeline.FailedRelease("managed-demo", "managed-demo/v2", "QUARANTINE"),
    )
    assert report.staged == (pipeline.StagedRelease("managed-demo", 1, _digest(1)),)
    assert load_state(config.state_path).skill("managed-demo").highest_sequence == 1


def test_managed_sync_modules_when_scanned_then_reference_no_live_store_paths() -> None:
    for module in (pipeline, quarantine):
        module_file = module.__file__
        assert module_file is not None
        assert "/srv/" not in Path(module_file).read_text(encoding="utf-8")


def test_sync_all_when_no_rollback_flag_then_lower_sequence_stays_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(tags={"managed-demo": (_tag("managed-demo", 1),)}, tree=tmp_path)
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True)})
    state = ManagedSyncState(
        skills={"managed-demo": SkillState(highest_sequence=2, last_verified_digest=_digest(2))}
    )

    report = pipeline.sync_all(config, state, _unused_runner)

    assert channel.verified_tags == []
    assert channel.staged == []
    assert report.staged == ()
    assert report.rolled_back == ()
    assert not config.state_path.exists()


def test_sync_all_when_allow_rollback_matches_then_restages_without_downgrading_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(
        tags={"managed-demo": (_tag("managed-demo", 1), _tag("managed-demo", 2))},
        tree=tmp_path,
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True)})
    state = ManagedSyncState(
        skills={"managed-demo": SkillState(highest_sequence=2, last_verified_digest=_digest(2))}
    )

    report = pipeline.sync_all(config, state, _unused_runner, allow_rollback=1)

    assert channel.rollback_verified == ["managed-demo/v1"]
    assert channel.staged == [("managed-demo", 1, _digest(1))]
    assert report.rolled_back == (pipeline.StagedRelease("managed-demo", 1, _digest(1)),)
    assert report.staged == ()
    assert load_state(config.state_path).skill("managed-demo").highest_sequence == 2


def test_sync_all_when_middle_release_fails_then_later_releases_still_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # v3 is independently valid: the fake verify models a manifest whose
    # previous_sha256 matches the state recorded by v1, so only v2 fails.
    channel = FakeChannel(
        tags={
            "managed-demo": (
                _tag("managed-demo", 1),
                _tag("managed-demo", 2),
                _tag("managed-demo", 3),
            )
        },
        tree=tmp_path,
        verify_errors={"managed-demo/v2": ManagedVerifyError("REVOKED", "self-revoked digest")},
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert channel.staged == [
        ("managed-demo", 1, _digest(1)),
        ("managed-demo", 3, _digest(3)),
    ]
    assert report.failed == (
        pipeline.FailedRelease("managed-demo", "managed-demo/v2", "REVOKED"),
    )
    assert report.staged == (pipeline.StagedRelease("managed-demo", 3, _digest(3)),)
    assert load_state(config.state_path).skill("managed-demo").highest_sequence == 3


def test_sync_all_when_multiple_releases_fail_then_every_failure_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(
        tags={
            "managed-demo": (
                _tag("managed-demo", 1),
                _tag("managed-demo", 2),
                _tag("managed-demo", 3),
                _tag("managed-demo", 4),
            )
        },
        tree=tmp_path,
        verify_errors={"managed-demo/v2": ManagedVerifyError("REVOKED", "self-revoked digest")},
        stage_errors=frozenset({_digest(3)}),
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert report.failed == (
        pipeline.FailedRelease("managed-demo", "managed-demo/v2", "REVOKED"),
        pipeline.FailedRelease("managed-demo", "managed-demo/v3", "QUARANTINE"),
    )
    assert report.staged == (pipeline.StagedRelease("managed-demo", 4, _digest(4)),)
    assert load_state(config.state_path).skill("managed-demo").highest_sequence == 4


def test_sync_all_when_chain_breaks_after_skipped_release_then_later_release_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # v3 genuinely depends on skipped v2: its previous_sha256 does NOT match
    # the recorded state, so verify's own chain check (6) rejects it.
    channel = FakeChannel(
        tags={
            "managed-demo": (
                _tag("managed-demo", 1),
                _tag("managed-demo", 2),
                _tag("managed-demo", 3),
            )
        },
        tree=tmp_path,
        verify_errors={
            "managed-demo/v2": ManagedVerifyError("REVOKED", "self-revoked digest"),
            "managed-demo/v3": ManagedVerifyError(
                "CHAIN-BREAK", "previous manifest digest does not match verified state"
            ),
        },
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, {"managed-demo": pipeline.SkillOptions(opt_in=True)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert channel.staged == [("managed-demo", 1, _digest(1))]
    assert report.failed == (
        pipeline.FailedRelease("managed-demo", "managed-demo/v2", "REVOKED"),
        pipeline.FailedRelease("managed-demo", "managed-demo/v3", "CHAIN-BREAK"),
    )
    assert report.staged == (pipeline.StagedRelease("managed-demo", 1, _digest(1)),)
    assert load_state(config.state_path).skill("managed-demo").highest_sequence == 1


def test_sync_all_when_tag_listing_fails_then_only_that_skill_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = FakeChannel(
        tags={
            "managed-alpha": (_tag("managed-alpha", 1),),
            "managed-beta": (_tag("managed-beta", 1),),
        },
        tree=tmp_path,
        list_errors={"managed-alpha": ManagedFetchError("git step returned 128")},
    )
    channel.install(monkeypatch)
    config = _config(
        tmp_path,
        {
            "managed-alpha": pipeline.SkillOptions(opt_in=True),
            "managed-beta": pipeline.SkillOptions(opt_in=True),
        },
    )

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert report.failed == (pipeline.FailedRelease("managed-alpha", "", "FETCH"),)
    assert channel.staged == [("managed-beta", 1, _digest(1))]
    assert load_state(config.state_path).skill("managed-beta").highest_sequence == 1
