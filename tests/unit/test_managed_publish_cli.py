from __future__ import annotations
# allow: SIZE_OK — one cohesive publisher protocol matrix shares its fake runner.

import json
import hashlib
import importlib
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from automation.managed_skills.manifest import ManifestError, canonical_json, parse_manifest
from automation.managed_skills.publisher_config import PublisherIdentity

publish_cli = importlib.import_module("automation.managed_skills.publish_cli")
PublishConfig = publish_cli.PublishConfig
PublishError = publish_cli.PublishError
publish = publish_cli.publish


_SKILL = "managed-x"
_NONCE = "a" * 32
_PREVIOUS_DIGEST = "b" * 64
_SOURCE_COMMIT = "c" * 40
_IDENTITY = PublisherIdentity(
    publisher="testlab", publisher_principal="publisher-testlab@autophagy"
)


@dataclass
class FakeRunner:
    tags: tuple[str, ...] = ()
    tag_manifests: dict[str, str] = field(default_factory=dict)
    publish_check_code: int = 0
    deploy_evidence: str | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)
    environments: list[dict[str, str]] = field(default_factory=list)

    def __call__(
        self,
        args: list[str],
        /,
        *,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert timeout > 0
        self.calls.append(tuple(args))
        self.environments.append(env)
        command = tuple(args)
        if command[-3:-1] == ("tag", "--list"):
            return _completed(command, stdout="\n".join(self.tags))
        if "status" in command:
            return _completed(command)
        if "rev-parse" in command:
            return _completed(command, stdout=f"{_SOURCE_COMMIT}\n")
        if "show" in command:
            tag = command[-1].split(":", maxsplit=1)[0]
            return _completed(command, stdout=self.tag_manifests[tag])
        if "publish-request" in command:
            return _completed(
                command,
                stdout=json.dumps({"message_id": "publish-message", "publish_nonce": _NONCE}),
            )
        if "publish-check" in command:
            return _completed(command, returncode=self.publish_check_code)
        if command[0].endswith("deploy-skill.sh"):
            return _completed(command, stdout=f"deploy-message:{_NONCE}\n")
        return _completed(command)


def _completed(
    command: tuple[str, ...], *, returncode: int = 0, stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(command), returncode, stdout, "")


def _write_source(tmp_path: Path) -> Path:
    source = tmp_path / "source" / "skills" / _SKILL
    source.mkdir(parents=True)
    _ = (source / "SKILL.md").write_text("---\nname: managed-x\n---\n", encoding="utf-8")
    _ = (source / "script.py").write_text("print('managed')\n", encoding="utf-8")
    return source


def _write_changelog(tmp_path: Path, payload: str | None = None) -> Path:
    changelog = tmp_path / "release.json"
    _ = changelog.write_text(
        payload
        or json.dumps(
            {
                "changelog": "Initial managed release.",
                "breaking": False,
                "compatibility": "any",
                "revoked_digests": [],
            }
        ),
        encoding="utf-8",
    )
    return changelog


def _config(tmp_path: Path, *, approve_evidence: str | None = f"deploy-message:{_NONCE}"):
    source = _write_source(tmp_path)
    managed_repo = tmp_path / "managed-repo"
    managed_repo.mkdir()
    signing_key = tmp_path / "fake-signing-key"
    _ = signing_key.write_text("test-only-key-path\n", encoding="utf-8")
    return PublishConfig(
        skill=_SKILL,
        managed_repo=managed_repo,
        skills_src=source,
        changelog_file=_write_changelog(tmp_path),
        signing_key=signing_key,
        identity=_IDENTITY,
        approve_evidence=approve_evidence,
        injection_file=None,
    )


def _mutating_commands(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [
        command
        for command in calls
        if "add" in command or "commit" in command or "push" in command or "-s" in command
    ]


def test_publish_when_approved_then_writes_v1_manifest_signs_tag_and_pushes(tmp_path: Path) -> None:
    # Given: a fresh managed checkout, valid deploy evidence, and an approved publish gate.
    config = _config(tmp_path)
    runner = FakeRunner()

    # When: the publisher runs the release pipeline.
    manifest = publish(config, runner, {})

    # Then: v1 has no predecessor and its tag message binds the canonical manifest digest.
    assert manifest.release_sequence == 1
    assert manifest.previous_sha256 is None
    written = parse_manifest((config.managed_repo / "manifests" / f"{_SKILL}.json").read_text(encoding="utf-8"))
    assert written == manifest
    tag_call = next(command for command in runner.calls if "-s" in command)
    assert ("-c", "gpg.format=ssh") in tuple(zip(tag_call, tag_call[1:]))
    assert manifest.publisher == _IDENTITY.publisher
    assert f"user.email={_IDENTITY.publisher_principal}" in tag_call
    assert ("tag", "-s", f"{_SKILL}/v1") == tag_call[tag_call.index("tag") : tag_call.index("tag") + 3]
    assert f"manifest_sha256:{hashlib.sha256(canonical_json(manifest).encode()).hexdigest()}" in tag_call[-1]
    assert runner.calls.index(tag_call) < next(index for index, command in enumerate(runner.calls) if "push" in command)


def test_publish_when_gate_check_is_pending_then_no_managed_repo_mutation_occurs(tmp_path: Path) -> None:
    # Given: a publish-check rejection after the request was posted.
    config = _config(tmp_path)
    runner = FakeRunner(publish_check_code=1)

    # When: publishing stops at the gate.
    with pytest.raises(PublishError):
        publish(config, runner, {})

    # Then: the checkout remains clean: no copy, stage, commit, tag, or push occurred.
    assert not (config.managed_repo / "skills").exists()
    assert _mutating_commands(runner.calls) == []


def test_publish_when_gate_check_precedes_approval_then_mutations_follow_it(tmp_path: Path) -> None:
    # Given: an approved publish request.
    config = _config(tmp_path)
    runner = FakeRunner()

    # When: a release is published.
    _ = publish(config, runner, {})

    # Then: no managed-repo mutation argv appears before publish-check succeeds.
    check_index = next(index for index, command in enumerate(runner.calls) if "publish-check" in command)
    assert all(runner.calls.index(command) > check_index for command in _mutating_commands(runner.calls))


def test_publish_when_agent_runtime_environment_present_then_refuses_before_subprocess(tmp_path: Path) -> None:
    # Given: the agent-runtime marker is present in the process environment.
    config = _config(tmp_path)
    runner = FakeRunner()

    # When: publishing is attempted from that environment.
    with pytest.raises(PublishError):
        publish(config, runner, {"DISCORD_BOT_TOKEN": "present"})

    # Then: no gate or git subprocess is invoked and the checkout remains untouched.
    assert runner.calls == []
    assert not (config.managed_repo / "skills").exists()


def test_publish_when_existing_tags_then_bumps_sequence_and_carries_previous_digest(tmp_path: Path) -> None:
    # Given: v1 and v2 tags, whose v2 manifest names the last released digest.
    config = _config(tmp_path)
    previous = parse_manifest(
        json.dumps(
            {
                "schema_version": 1,
                "publisher": _IDENTITY.publisher,
                "skill": _SKILL,
                "release_sequence": 2,
                "source_commit": None,
                "skill_sha256": _PREVIOUS_DIGEST,
                "previous_sha256": "d" * 64,
                "compatibility": "any",
                "breaking": False,
                "revoked_digests": [],
                "changelog": "Previous release.",
            }
        )
    )
    runner = FakeRunner(
        tags=(f"{_SKILL}/v1", f"{_SKILL}/v2"),
        tag_manifests={f"{_SKILL}/v2": canonical_json(previous)},
    )

    # When: the next release is published.
    manifest = publish(config, runner, {})

    # Then: it is v3 and chains to v2's source digest.
    assert manifest.release_sequence == 3
    assert manifest.previous_sha256 == _PREVIOUS_DIGEST


@pytest.mark.parametrize("skill", ("calendar", "managed-" + "x" * 34))
def test_publish_when_skill_is_outside_managed_namespace_then_refuses_before_gate(tmp_path: Path, skill: str) -> None:
    # Given: a non-managed or overlong requested skill name.
    config = _config(tmp_path)
    invalid = PublishConfig(
        skill=skill,
        managed_repo=config.managed_repo,
        skills_src=config.skills_src,
        changelog_file=config.changelog_file,
        signing_key=config.signing_key,
        identity=_IDENTITY,
        approve_evidence=config.approve_evidence,
        injection_file=None,
    )
    runner = FakeRunner()

    # When: validation runs.
    with pytest.raises(PublishError):
        publish(invalid, runner, {})

    # Then: it fails closed before any gate call or managed checkout mutation.
    assert runner.calls == []
    assert not (config.managed_repo / "skills").exists()


def test_publish_when_deploy_evidence_is_not_supplied_then_runs_approve_only_with_explicit_source(tmp_path: Path) -> None:
    # Given: no reusable evidence but a fake approve-only deploy result.
    config = _config(tmp_path, approve_evidence=None)
    runner = FakeRunner()

    # When: publishing acquires deploy evidence.
    _ = publish(config, runner, {})

    # Then: the isolated deploy command runs before the publish gate with its source explicit in env.
    deploy_index = next(index for index, command in enumerate(runner.calls) if command[0].endswith("deploy-skill.sh"))
    request_index = next(index for index, command in enumerate(runner.calls) if "publish-request" in command)
    assert deploy_index < request_index
    assert runner.environments[deploy_index]["SKILL_SRC_DIR"] == str(config.skills_src)


def test_publish_when_approve_evidence_is_malformed_then_refuses_before_gate(tmp_path: Path) -> None:
    # Given: malformed deployment evidence.
    config = _config(tmp_path, approve_evidence="message-without-nonce")
    runner = FakeRunner()

    # When: publishing validates the evidence boundary.
    with pytest.raises(PublishError):
        publish(config, runner, {})

    # Then: no subprocess or mutation is allowed.
    assert runner.calls == []


def test_publish_when_signing_key_path_is_missing_then_refuses_before_gate(tmp_path: Path) -> None:
    # Given: a release config whose signing key path does not exist.
    config = _config(tmp_path)
    missing_key = PublishConfig(
        skill=config.skill,
        managed_repo=config.managed_repo,
        skills_src=config.skills_src,
        changelog_file=config.changelog_file,
        signing_key=tmp_path / "missing-signing-key",
        identity=_IDENTITY,
        approve_evidence=config.approve_evidence,
        injection_file=None,
    )
    runner = FakeRunner()

    # When: the precondition is checked.
    with pytest.raises(PublishError):
        publish(missing_key, runner, {})

    # Then: it refuses without creating a publish request.
    assert runner.calls == []


def test_publish_when_breaking_changelog_omits_migration_then_manifest_validation_blocks_gate(tmp_path: Path) -> None:
    # Given: a breaking release changelog lacking the migration note MS-P1 requires.
    config = _config(tmp_path)
    _ = config.changelog_file.write_text(
        json.dumps({"changelog": "Breaking release.", "breaking": True, "compatibility": "any"}),
        encoding="utf-8",
    )
    runner = FakeRunner()

    # When: the publisher assembles the manifest.
    with pytest.raises(ManifestError, match="migration"):
        publish(config, runner, {})

    # Then: validation happens before the publish gate and no checkout mutation occurs.
    assert not any("publish-request" in command for command in runner.calls)
    assert _mutating_commands(runner.calls) == []


def test_publish_when_release_reclaims_own_digest_then_names_specific_error_before_gate(
    tmp_path: Path,
) -> None:
    # Given: a changelog that mistakenly reclaims the exact source digest being published.
    config = _config(tmp_path)
    digest = publish_cli.skill_digest(config.skills_src)
    _ = config.changelog_file.write_text(
        json.dumps(
            {
                "changelog": "Mistaken self-reclaim.",
                "breaking": False,
                "compatibility": "any",
                "revoked_digests": [digest],
            }
        ),
        encoding="utf-8",
    )
    runner = FakeRunner()

    # When: publish-time validation assembles the candidate manifest.
    with pytest.raises(PublishError, match="SELF-DIGEST-RECLAIM"):
        publish(config, runner, {})

    # Then: the operator gets the named cause before approval or repository mutation.
    assert not any("publish-request" in command for command in runner.calls)
    assert _mutating_commands(runner.calls) == []


def test_publish_when_stage_request_mode_then_prints_bound_evidence_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a fully approved deployment and a fresh managed checkout.
    config = replace(_config(tmp_path), stage_publish_request=True)
    runner = FakeRunner()

    # When: the publish approval request is staged for a real owner reaction.
    _ = publish(config, runner, {})

    # Then: its stable evidence line binds the request without creating any release mutation.
    line = capsys.readouterr().out.strip()
    assert line.startswith(f"PUBLISH-STAGED message_id=publish-message publish_nonce={_NONCE} skill_sha256=")
    assert "manifest_sha256=" in line and "tag=managed-x/v1" in line
    assert any("publish-request" in command for command in runner.calls)
    assert _mutating_commands(runner.calls) == []


def test_publish_when_publish_evidence_is_approved_then_checks_existing_request_before_tagging(tmp_path: Path) -> None:
    # Given: an existing publish-request binding supplied after the owner reacts.
    config = replace(_config(tmp_path), publish_evidence=f"publish-message:{_NONCE}")
    runner = FakeRunner()

    # When: the finalization mode consumes that binding.
    _ = publish(config, runner, {})

    # Then: it never posts again and tags only after the bound publish-check.
    check = next(command for command in runner.calls if "publish-check" in command)
    assert not any("publish-request" in command for command in runner.calls)
    assert check[check.index("--message-id") + 1] == "publish-message"
    assert check[check.index("--publish-nonce") + 1] == _NONCE
    assert _mutating_commands(runner.calls)


def test_publish_when_publish_evidence_check_rejects_then_leaves_checkout_unmodified(tmp_path: Path) -> None:
    # Given: a publish evidence binding whose owner approval is absent.
    config = replace(_config(tmp_path), publish_evidence=f"publish-message:{_NONCE}")
    runner = FakeRunner(publish_check_code=1)

    # When: finalization checks the existing request.
    with pytest.raises(PublishError):
        publish(config, runner, {})

    # Then: it never posts, tags, commits, or pushes.
    assert not any("publish-request" in command for command in runner.calls)
    assert _mutating_commands(runner.calls) == []


def test_publish_when_staged_then_finalized_with_same_inputs_then_manifest_hash_is_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the same source, changelog, and release checkout across two approval phases.
    staged = replace(_config(tmp_path), stage_publish_request=True)
    _ = publish(staged, FakeRunner(), {})
    staged_hash = next(field.removeprefix("manifest_sha256=") for field in capsys.readouterr().out.split() if field.startswith("manifest_sha256="))
    finalized = replace(staged, stage_publish_request=False, publish_evidence=f"publish-message:{_NONCE}")
    runner = FakeRunner()

    # When: the approved evidence finalizes the same release.
    _ = publish(finalized, runner, {})

    # Then: the signed tag carries the exact manifest hash staged for the owner.
    tag = next(command for command in runner.calls if "-s" in command)
    assert f"manifest_sha256:{staged_hash}" in tag[-1]


def test_publish_when_two_phase_flags_are_combined_then_refuses_before_request(tmp_path: Path) -> None:
    # Given: incompatible stage and finalization modes.
    config = replace(_config(tmp_path), stage_publish_request=True, publish_evidence=f"publish-message:{_NONCE}")
    runner = FakeRunner()

    # When: publishing validates its mode boundary.
    with pytest.raises(PublishError):
        publish(config, runner, {})

    # Then: it fails closed without creating a gate request or release mutation.
    assert runner.calls == []


def test_publish_when_token_file_is_provided_then_only_gate_subprocess_environment_receives_it(tmp_path: Path) -> None:
    # Given: a publisher ambient environment without the agent token and a local token file.
    token_file = tmp_path / "publish-token"
    _ = token_file.write_text("fake-gate-token\n", encoding="utf-8")
    config = replace(_config(tmp_path), discord_token_file=token_file)
    runner = FakeRunner()

    # When: the synchronous publish gate runs.
    _ = publish(config, runner, {"PUBLISHER_CONTEXT": "workstation"})

    # Then: request and check receive the scoped token while the ambient precondition remains token-free.
    gate_environments = [runner.environments[index] for index, call in enumerate(runner.calls) if "publish-" in " ".join(call)]
    assert gate_environments == [
        {"PUBLISHER_CONTEXT": "workstation", "DISCORD_BOT_TOKEN": "fake-gate-token"},
        {"PUBLISHER_CONTEXT": "workstation", "DISCORD_BOT_TOKEN": "fake-gate-token"},
    ]


def test_publish_when_token_file_is_blank_then_refuses_before_any_subprocess(tmp_path: Path) -> None:
    # Given: a blank scoped token file.
    token_file = tmp_path / "publish-token"
    _ = token_file.write_text(" \n", encoding="utf-8")
    config = replace(_config(tmp_path), discord_token_file=token_file)
    runner = FakeRunner()

    # When: publishing prepares its subprocess environment.
    with pytest.raises(PublishError, match="discord token file is empty"):
        publish(config, runner, {})

    # Then: no deployment, gate, or git subprocess has run.
    assert runner.calls == []


def test_publish_when_token_file_and_ambient_token_are_both_present_then_ambient_refusal_wins(tmp_path: Path) -> None:
    # Given: a scoped token source but an environment identifying a real agent runtime.
    token_file = tmp_path / "publish-token"
    _ = token_file.write_text("fake-gate-token\n", encoding="utf-8")
    config = replace(_config(tmp_path), discord_token_file=token_file)
    runner = FakeRunner()

    # When: publishing starts in the ambient agent environment.
    with pytest.raises(PublishError):
        publish(config, runner, {"DISCORD_BOT_TOKEN": "ambient-agent-token"})

    # Then: scoped injection never bypasses the SI-5 ambient-environment refusal.
    assert runner.calls == []



def test_publish_when_identity_changes_then_manifest_and_signing_email_follow_config(
    tmp_path: Path,
) -> None:
    # Given: a second group whose admin publishes under a different principal.
    other = PublisherIdentity(
        publisher="otherlab", publisher_principal="publisher-otherlab@autophagy"
    )
    config = replace(_config(tmp_path), identity=other)
    runner = FakeRunner()

    # When: that admin publishes a release.
    manifest = publish(config, runner, {})

    # Then: nothing about the release names any other publisher.
    tag_call = next(command for command in runner.calls if "-s" in command)
    assert manifest.publisher == "otherlab"
    assert "user.email=publisher-otherlab@autophagy" in tag_call
    assert not any("otherlab" not in field and "user.email=" in field for field in tag_call)