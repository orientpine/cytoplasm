from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from automation.managed_skills.manifest import ManagedManifest
from automation.managed_sync import pipeline, revoke
from automation.managed_sync.fetch import FetchResult, ReleaseTag
from automation.managed_sync.state import ManagedSyncState, SkillState, load_state
from automation.managed_sync.verify import ManagedVerifyError, VerifiedRelease

_SKILL = "managed-demo"
_LIVE_DIGEST = "1" * 64
_OTHER_DIGEST = "2" * 64
_REMOVE_COMMAND = f"automation/deploy-skill.sh {_SKILL} --remove"


def _live_root(tmp_path: Path, *, target: Path | None = None, dangling: bool = False) -> Path:
    """Build a tmp live dir; optionally symlink `_SKILL` at `target`."""
    live = tmp_path / "live"
    live.mkdir(parents=True, exist_ok=True)
    if target is not None:
        if not dangling:
            target.mkdir(parents=True, exist_ok=True)
        (live / _SKILL).symlink_to(target)
    return live


def _release_dir(tmp_path: Path, digest: str) -> Path:
    return tmp_path / "managed-releases" / "cha" / _SKILL / digest


def _state(revoked: tuple[str, ...]) -> ManagedSyncState:
    return ManagedSyncState(skills={_SKILL: SkillState(revoked_digests=revoked)})


def test_check_live_when_live_digest_is_revoked_then_emits_one_owner_gated_request(
    tmp_path: Path,
) -> None:
    live = _live_root(tmp_path, target=_release_dir(tmp_path, _LIVE_DIGEST))

    requests = revoke.check_live(_state((_LIVE_DIGEST,)), (_SKILL,), live_root=live)

    assert len(requests) == 1
    request = requests[0]
    assert request.skill == _SKILL
    assert request.digest == _LIVE_DIGEST
    assert _REMOVE_COMMAND in request.reason
    assert _REMOVE_COMMAND in revoke.render_removal_instruction(request)


def test_check_live_when_live_digest_is_not_revoked_then_requests_nothing(
    tmp_path: Path,
) -> None:
    live = _live_root(tmp_path, target=_release_dir(tmp_path, _LIVE_DIGEST))

    assert revoke.check_live(_state((_OTHER_DIGEST,)), (_SKILL,), live_root=live) == ()


def test_check_live_when_symlink_is_missing_then_requests_nothing(tmp_path: Path) -> None:
    live = _live_root(tmp_path)

    assert revoke.check_live(_state((_LIVE_DIGEST,)), (_SKILL,), live_root=live) == ()
    absent_root = tmp_path / "no-such-live"
    assert revoke.check_live(_state((_LIVE_DIGEST,)), (_SKILL,), live_root=absent_root) == ()


def test_check_live_when_symlink_is_dangling_then_requests_nothing(tmp_path: Path) -> None:
    live = _live_root(tmp_path, target=_release_dir(tmp_path, _LIVE_DIGEST), dangling=True)

    assert revoke.check_live(_state((_LIVE_DIGEST,)), (_SKILL,), live_root=live) == ()


def test_check_live_when_live_entry_is_a_real_directory_then_requests_nothing(
    tmp_path: Path,
) -> None:
    live = _live_root(tmp_path)
    (live / _SKILL).mkdir()

    assert revoke.check_live(_state((_LIVE_DIGEST,)), (_SKILL,), live_root=live) == ()


class _RecordingHandler(logging.Handler):
    """Collects emitted records; caplog is unavailable in this environment."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_check_live_when_target_digest_is_unparseable_then_warns_and_requests_nothing(
    tmp_path: Path,
) -> None:
    target = tmp_path / "managed-releases" / "cha" / _SKILL / "not-a-digest"
    live = _live_root(tmp_path, target=target)
    logger = logging.getLogger("automation.managed_sync.revoke")
    handler = _RecordingHandler()
    logger.addHandler(handler)

    try:
        requests = revoke.check_live(_state((_LIVE_DIGEST,)), (_SKILL,), live_root=live)
    finally:
        logger.removeHandler(handler)

    assert requests == ()
    warnings = [record for record in handler.records if record.levelno == logging.WARNING]
    assert any("not-a-digest" in record.getMessage() for record in warnings)


def test_revoke_module_when_scanned_then_contains_no_live_mutation() -> None:
    module_file = revoke.__file__
    assert module_file is not None
    source = Path(module_file).read_text(encoding="utf-8")
    for forbidden in ("unlink", "rmtree", "shutil", "subprocess", "symlink_to", "os.remove"):
        assert forbidden not in source, f"revoke.py must stay read-only: found {forbidden!r}"


def test_revoke_module_when_scanned_then_remove_flag_appears_only_in_instruction_text() -> None:
    module_file = revoke.__file__
    assert module_file is not None
    source = Path(module_file).read_text(encoding="utf-8")
    assert source.count("--remove") == 1
    (command_line,) = [line for line in source.splitlines() if "--remove" in line]
    assert "deploy-skill.sh" in command_line


def _digest_for(sequence: int) -> str:
    return "abcdef"[sequence] * 64


def _manifest(skill: str, sequence: int, revoked: tuple[str, ...]) -> ManagedManifest:
    return ManagedManifest(
        schema_version=1,
        publisher="testlab",
        skill=skill,
        release_sequence=sequence,
        source_commit=None,
        skill_sha256=_digest_for(sequence),
        previous_sha256=None,
        compatibility="any",
        breaking=False,
        revoked_digests=revoked,
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
class RevocationChannel:
    """Fakes the fetch/verify/stage seam; verify honours S1's REVOKED check."""

    tags: dict[str, tuple[ReleaseTag, ...]]
    tree: Path
    manifest_revocations: dict[str, tuple[str, ...]] = field(default_factory=dict)
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
        del mirror, config, runner, allow_rollback
        skill, _, version = tag.partition("/v")
        sequence = int(version)
        digest = _digest_for(sequence)
        revoked = self.manifest_revocations.get(tag, ())
        if digest in set(state.revoked_digests).union(revoked):
            raise ManagedVerifyError("REVOKED", "source digest is revoked")
        return VerifiedRelease(
            skill=skill,
            sequence=sequence,
            digest=digest,
            manifest=_manifest(skill, sequence, revoked),
            tree_path=self.tree,
            tag=tag,
        )

    def _stage_candidate(self, verified: VerifiedRelease, quarantine_root: Path) -> Path:
        self.staged.append((verified.skill, verified.sequence, verified.digest))
        return quarantine_root / verified.skill / verified.digest


def _tag(sequence: int) -> ReleaseTag:
    return ReleaseTag(skill=_SKILL, sequence=sequence, tag_name=f"{_SKILL}/v{sequence}")


def _config(
    tmp_path: Path, live_root: Path, skills: Mapping[str, pipeline.SkillOptions]
) -> pipeline.SyncConfig:
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
        live_root=live_root,
    )


def test_sync_all_when_manifest_revokes_digests_then_state_accumulates_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = RevocationChannel(
        tags={_SKILL: (_tag(1),)},
        tree=tmp_path,
        manifest_revocations={f"{_SKILL}/v1": (_LIVE_DIGEST,)},
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, _live_root(tmp_path), {_SKILL: pipeline.SkillOptions(opt_in=True)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert report.staged == (pipeline.StagedRelease(_SKILL, 1, _digest_for(1)),)
    saved = load_state(config.state_path).skill(_SKILL)
    assert _LIVE_DIGEST in saved.revoked_digests
    assert saved.highest_sequence == 1


def test_sync_all_when_release_digest_was_revoked_earlier_then_it_is_never_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = RevocationChannel(
        tags={_SKILL: (_tag(1),)},
        tree=tmp_path,
        manifest_revocations={f"{_SKILL}/v1": (_digest_for(2),)},
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, _live_root(tmp_path), {_SKILL: pipeline.SkillOptions(opt_in=True)})
    _ = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)
    channel.tags[_SKILL] = (_tag(1), _tag(2))
    channel.staged.clear()

    report = pipeline.sync_all(config, load_state(config.state_path), _unused_runner)

    assert report.failed == (pipeline.FailedRelease(_SKILL, f"{_SKILL}/v2", "REVOKED"),)
    assert report.staged == ()
    assert channel.staged == []
    assert load_state(config.state_path).skill(_SKILL).highest_sequence == 1


def test_sync_all_when_live_digest_gets_revoked_this_run_then_report_carries_removal_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = _live_root(tmp_path, target=_release_dir(tmp_path, _LIVE_DIGEST))
    channel = RevocationChannel(
        tags={_SKILL: (_tag(1),)},
        tree=tmp_path,
        manifest_revocations={f"{_SKILL}/v1": (_LIVE_DIGEST,)},
    )
    channel.install(monkeypatch)
    config = _config(tmp_path, live, {_SKILL: pipeline.SkillOptions(opt_in=True)})

    report = pipeline.sync_all(config, ManagedSyncState(), _unused_runner)

    assert len(report.removal_requests) == 1
    request = report.removal_requests[0]
    assert (request.skill, request.digest) == (_SKILL, _LIVE_DIGEST)
    assert _REMOVE_COMMAND in request.reason
