from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from typing import Protocol, TypeAlias, TypeGuard

from automation.managed_skills.manifest import (
    ManagedManifest,
    canonical_json,
    manifest_digest,
)
from automation.managed_skills.publish_core import (
    APPROVAL_EVIDENCE,
    PublishConfig,
    PublishError,
    PublishProcess,
    Runner,
    build_manifest,
    verify_deploy_approval,
    verify_publish_preconditions,
)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads


def _is_json_object(value: JsonValue) -> TypeGuard[dict[str, JsonValue]]:
    return isinstance(value, dict)


def _publish_approval(
    config: PublishConfig,
    manifest: ManagedManifest,
    process: PublishProcess,
) -> bool:
    tag = f"{config.skill}/v{manifest.release_sequence}"
    digest = manifest_digest(manifest)
    if config.publish_evidence is not None:
        evidence = APPROVAL_EVIDENCE.fullmatch(config.publish_evidence)
        if evidence is None:
            raise PublishError("publish evidence must be MESSAGE_ID:PUBLISH_NONCE")
        message_id, nonce = evidence.group("message_id"), evidence.group("nonce")
    else:
        request = process.require(
            (
                sys.executable,
                "-m",
                "automation.skill_gate",
                "publish-request",
                "--skill",
                config.skill,
                "--hash",
                manifest.skill_sha256,
                "--manifest-hash",
                digest,
                "--tag",
                tag,
                "--json",
            )
        )
        try:
            response_raw = _JSON_LOADS(request.stdout)
        except json.JSONDecodeError as error:
            raise PublishError("publish-request did not return JSON") from error
        if not _is_json_object(response_raw):
            raise PublishError("publish-request returned a non-object response")
        message_id = response_raw.get("message_id")
        nonce = response_raw.get("publish_nonce")
        if (
            not isinstance(message_id, str)
            or not message_id
            or not isinstance(nonce, str)
            or APPROVAL_EVIDENCE.fullmatch(f"x:{nonce}") is None
        ):
            raise PublishError("publish-request returned invalid approval binding")
    if config.stage_publish_request:
        print(
            "PUBLISH-STAGED "
            + f"message_id={message_id} publish_nonce={nonce} "
            + f"skill_sha256={manifest.skill_sha256} manifest_sha256={digest} tag={tag}"
        )
        return True
    check = (
        sys.executable,
        "-m",
        "automation.skill_gate",
        "publish-check",
        "--skill",
        config.skill,
        "--hash",
        manifest.skill_sha256,
        "--manifest-hash",
        digest,
        "--tag",
        tag,
        "--message-id",
        message_id,
        "--publish-nonce",
        nonce,
    )
    if config.injection_file is not None:
        check += ("--injection-file", str(config.injection_file))
    _ = process.require(check)
    return False


def publish(
    config: PublishConfig,
    runner: Runner = subprocess.run,
    environment: Mapping[str, str] | None = None,
) -> ManagedManifest:
    active_environment = dict(os.environ if environment is None else environment)
    verify_publish_preconditions(config, active_environment)
    subprocess_environment = dict(active_environment)
    if config.discord_token_file is not None:
        try:
            token = config.discord_token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise PublishError(f"discord token file unreadable: {config.discord_token_file}") from error
        if not token:
            raise PublishError("discord token file is empty")
        subprocess_environment["DISCORD_BOT_TOKEN"] = token
    process = PublishProcess(runner, subprocess_environment)
    verify_deploy_approval(config, process)
    status = process.require(("git", "-C", str(config.managed_repo), "status", "--porcelain"))
    if status.stdout:
        raise PublishError("managed repository worktree must be clean before publish approval")
    manifest = build_manifest(config, process)
    if _publish_approval(config, manifest, process):
        return manifest
    destination = config.managed_repo / "skills" / config.skill
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copytree(
        config.skills_src,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    manifest_path = config.managed_repo / "manifests" / f"{config.skill}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _ = manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
    tag = f"{config.skill}/v{manifest.release_sequence}"
    message = f"Publish {tag}\nmanifest_sha256:{manifest_digest(manifest)}"
    _ = process.require(
        (
            "git",
            "-C",
            str(config.managed_repo),
            "add",
            f"skills/{config.skill}",
            f"manifests/{config.skill}.json",
        )
    )
    _ = process.require(
        ("git", "-C", str(config.managed_repo), "commit", "-m", f"Publish {tag}")
    )
    _ = process.require(
        (
            "git",
            "-C",
            str(config.managed_repo),
            "-c",
            "gpg.format=ssh",
            "-c",
            f"user.signingkey={config.signing_key}",
            "-c",
            f"user.email={config.identity.publisher_principal}",
            "tag",
            "-s",
            tag,
            "-m",
            message,
        )
    )
    _ = process.require(("git", "-C", str(config.managed_repo), "push"))
    _ = process.require(("git", "-C", str(config.managed_repo), "push", "origin", tag))
    return manifest
