#!/usr/bin/env python3
"""Offline actuator for the MS-E1 managed-skill scenario bank."""  # allow: SIZE_OK — one exact lifecycle state machine
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, TypeAlias

from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.managed_sync.cli import load_config
from automation.managed_sync.state import SkillState, load_state
from automation.managed_sync.verify import ManagedVerifyError, verify_release
from automation.skill_review import skill_digest

ROOT: Final = Path(__file__).resolve().parents[3]
SKILL: Final = "managed-hello-autophagy"
MESSAGE_ID: Final = "e2e-publish-message"
OWNER_ID: Final = "owner-e2e"
# AS-1.10: the publish gate now resolves a VERIFIED Discord channel, so the fixture
# must look like one — a snowflake the mock below can describe as #approvals.
CHANNEL_ID: Final = "900000000000000001"
EVIDENCE: Final = "offline-message:" + "a" * 32
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class Release:
    label: str
    signer: str
    revoked_digests: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Published:
    sequence: int
    digest: str
    output: str


@dataclass(frozen=True, slots=True)
class World:
    root: Path

    @classmethod
    def create(cls, root: Path) -> World:
        _ = root.mkdir(mode=0o700, parents=True)
        world = cls(root)
        _ = world.secret.write_text(secrets.token_hex(32), encoding="utf-8")
        for name in ("publisher", "unlisted"):
            _ = world.command(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(world.key(name))))
        _ = world.command(("git", "init", "--bare", str(world.remote)))
        seed = root / "seed"
        _ = seed.mkdir()
        _ = world.command(("git", "init", str(seed)))
        _ = (seed / "README.md").write_text("managed channel e2e seed\n", encoding="utf-8")
        _ = world.git(seed, "config", "user.name", "publisher-cha")
        _ = world.git(seed, "config", "user.email", "publisher-cha@autophagy")
        _ = world.git(seed, "add", "README.md")
        _ = world.git(seed, "commit", "-m", "seed")
        _ = world.git(seed, "branch", "-M", "main")
        _ = world.git(seed, "remote", "add", "origin", str(world.remote))
        _ = world.git(seed, "push", "-u", "origin", "main")
        _ = world.git(world.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        _ = world.command(("git", "clone", str(world.remote), str(world.checkout)))
        _ = world.git(world.checkout, "config", "user.name", "publisher-cha")
        _ = world.git(world.checkout, "config", "user.email", "publisher-cha@autophagy")
        _ = world.source.mkdir(parents=True)
        _ = world.home.mkdir(mode=0o700)
        _ = world.allowed.write_text(
            f"publisher-cha@autophagy {world.key('publisher').with_suffix('.pub').read_text(encoding='utf-8')}",
            encoding="utf-8",
        )
        _ = world.subscriber_key.write_text("offline only\n", encoding="utf-8")
        interop = world.home / ".hermes" / "interop"
        _ = interop.mkdir(mode=0o700, parents=True)
        _ = (interop / "config.json").write_text(
            json.dumps({"owner_id": OWNER_ID, "deploy_approvals_channel_id": CHANNEL_ID}), encoding="utf-8"
        )
        config_dir = world.home / ".hermes" / "managed-sync"
        _ = config_dir.mkdir(mode=0o700, parents=True)
        _ = world.config.write_text(
            json.dumps(
                {
                    "remote_url": str(world.remote),
                    "publisher": "cha",
                    "allowed_signers": str(world.allowed),
                    "mirror_dir": str(world.mirror),
                    "ssh_key_path": str(world.subscriber_key),
                    "quarantine_dir": str(world.quarantine),
                    "state_path": str(world.state),
                    "skills": {SKILL: {"opt_in": True, "pin": None}},
                }
            ),
            encoding="utf-8",
        )
        _ = world.mock.mkdir(mode=0o700)
        _ = (world.mock / "sitecustomize.py").write_text(_MOCK_SOURCE, encoding="utf-8")
        return world

    @property
    def remote(self) -> Path:
        return self.root / "managed-skills.git"

    @property
    def checkout(self) -> Path:
        return self.root / "publisher"

    @property
    def source(self) -> Path:
        return self.root / "source" / SKILL

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def allowed(self) -> Path:
        return self.root / "allowed_signers"

    @property
    def config(self) -> Path:
        return self.home / ".hermes" / "managed-sync" / "config.json"

    @property
    def key_dir(self) -> Path:
        return self.root / "keys"

    def key(self, name: str) -> Path:
        _ = self.key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return self.key_dir / name

    @property
    def subscriber_key(self) -> Path:
        return self.key_dir / "subscriber"

    @property
    def mirror(self) -> Path:
        return self.root / "subscriber" / "mirror"

    @property
    def quarantine(self) -> Path:
        return self.root / "subscriber" / "quarantine"

    @property
    def state(self) -> Path:
        return self.root / "subscriber" / "state.json"

    @property
    def live(self) -> Path:
        return self.root / "live"

    @property
    def secret(self) -> Path:
        return self.root / "e2e-secret"

    @property
    def mock(self) -> Path:
        return self.root / "mock"

    @property
    def store(self) -> Path:
        return self.root / "discord-store.json"

    def environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        _ = environment.pop("DISCORD_BOT_TOKEN", None)
        _ = environment.pop("MANAGED_ANNOUNCE_CHANNEL_ID", None)
        environment.update(
            {
                "APPROVAL_LOG_PATH": str(self.root / "approvals.jsonl"),
                "E2E_TEST_MODE": "1",
                "HOME": str(self.home),
                "INTEROP_E2E_SECRET": self.secret.read_text(encoding="utf-8"),
                "INTEROP_CONFIG": str(self.home / ".hermes" / "interop" / "config.json"),
                "MANAGED_SYNC_CONFIG": str(self.config),
                "MS_E2E_DISCORD_STORE": str(self.store),
                "MS_E2E_LIVE_ROOT": str(self.live),
                "MS_E2E_LOCAL_MOCK": "1",
                "PYTHONPATH": f"{self.mock}:{ROOT}:{environment.get('PYTHONPATH', '')}",
            }
        )
        return environment

    def command(self, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, check=False, cwd=ROOT, env=self.environment(), text=True)

    def require(self, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        result = self.command(args)
        if result.returncode != 0:
            raise RuntimeError(f"command failed: {' '.join(args)}: {result.stderr.strip()}")
        return result

    def git(self, directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return self.require(("git", "-C", str(directory), *args))

    def publish(self, release: Release) -> Published:
        _ = (self.source / "SKILL.md").write_text(f"# {SKILL}\n\nrelease: {release.label}\n", encoding="utf-8")
        digest = skill_digest(self.source)
        event = InboundEvent("e2e-event", OWNER_ID, CHANNEL_ID, f"PUBLISH skill:{SKILL} sha256:{digest} msg:{MESSAGE_ID}")
        injection = self.root / "publish-injection.json"
        _ = injection.write_text(
            json.dumps({"event": asdict(event), "signature": sign_event(event, self.secret.read_bytes())}),
            encoding="utf-8",
        )
        changelog = self.root / "changelog.json"
        _ = changelog.write_text(
            json.dumps({"changelog": release.label, "breaking": False, "compatibility": "e2e", "revoked_digests": list(release.revoked_digests)}),
            encoding="utf-8",
        )
        result = self.command(
            (
                sys.executable,
                "-m",
                "automation.managed_skills.publish_cli",
                "--skill",
                SKILL,
                "--managed-repo",
                str(self.checkout),
                "--skills-src",
                str(self.source),
                "--changelog-file",
                str(changelog),
                "--signing-key",
                str(self.key(release.signer)),
                "--approve-evidence",
                EVIDENCE,
                "--injection-file",
                str(injection),
            )
        )
        if result.returncode != 0:
            raise RuntimeError(f"publish failed: {result.stderr.strip()}")
        matched = re.search(r"tag=.*?/v(?P<sequence>\d+)", result.stdout)
        if matched is None:
            raise RuntimeError("publish output lacks release sequence")
        return Published(int(matched["sequence"]), digest, result.stdout)

    def sync(self) -> subprocess.CompletedProcess[str]:
        return self.command((sys.executable, "-m", "automation.managed_sync", "sync"))

    def require_sync(self) -> subprocess.CompletedProcess[str]:
        result = self.sync()
        if result.returncode != 0:
            raise RuntimeError(f"sync failed: {result.stderr.strip()}")
        return result

    def repoint(self, tag: str) -> None:
        message = self.git(self.checkout, "tag", "-l", "--format=%(contents)", tag).stdout
        _ = self.git(self.checkout, "add", "skills", "manifests")
        _ = self.git(self.checkout, "commit", "-m", "tamper tagged content")
        _ = self.git(self.checkout, "tag", "-d", tag)
        _ = self.git(self.checkout, "-c", "gpg.format=ssh", "-c", f"user.signingkey={self.key('publisher')}", "tag", "-s", tag, "-m", message)
        _ = self.git(self.checkout, "push", "--force", "origin", tag)


_MOCK_SOURCE: Final = """import json
import os
from pathlib import Path

if os.environ.get('MS_E2E_LOCAL_MOCK') == '1':
    from automation import skill_gate

    def api(method, path, payload=None):
        store = Path(os.environ['MS_E2E_DISCORD_STORE'])
        if method == 'POST':
            store.write_text(json.dumps({'content': payload['content']}), encoding='utf-8')
            return {'id': 'e2e-publish-message'}
        if path.startswith('/channels/') and path.count('/') == 2:
            return {'id': path.rsplit('/', 1)[1], 'type': 0, 'name': 'approvals'}
        return json.loads(store.read_text(encoding='utf-8'))

    skill_gate._token = lambda: 'offline'
    skill_gate._api = api
    from automation.managed_sync import revoke
    revoke.DEFAULT_LIVE_ROOT = Path(os.environ['MS_E2E_LIVE_ROOT'])
"""


def line(output: str, prefix: str) -> str:
    return next((item for item in output.splitlines() if item.startswith(prefix)), "")


def state_sequence(world: World) -> int:
    return load_state(world.state).skill(SKILL).highest_sequence


def verify_prefix(world: World, tag: str, *, allow_rollback: bool = False) -> str:
    config = load_config(world.config)
    try:
        _ = verify_release(config.mirror_dir, tag, config, load_state(config.state_path).skill(SKILL), allow_rollback=allow_rollback)
    except ManagedVerifyError as error:
        return error.prefix
    return "VERIFIED"


def case_publish_v1(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    published = world.publish(Release("v1", "publisher"))
    clone = root / "clean-clone"
    _ = world.require(("git", "clone", str(world.remote), str(clone)))
    verified = world.command(("git", "-C", str(clone), "-c", f"gpg.ssh.allowedSignersFile={world.allowed}", "verify-tag", f"{SKILL}/v1"))
    return {"publish_line": line(published.output, "PUBLISHED"), "signed_tag": published.sequence == 1, "clean_clone_verifies": verified.returncode == 0, "error": None}


def case_subscriber_sync(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    published = world.publish(Release("v1", "publisher"))
    synced = world.sync()
    return {"sync_staged": line(synced.stdout, "SYNC-STAGED"), "sync_summary": line(synced.stdout, "SYNC-SUMMARY"), "state_sequence": state_sequence(world), "quarantined": (world.quarantine / SKILL / published.digest).is_dir(), "error": None}


def case_bad_signature(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    _ = world.publish(Release("v1", "unlisted"))
    synced = world.sync()
    return {"sync_failed": line(synced.stdout, "SYNC-FAILED"), "sync_summary": line(synced.stdout, "SYNC-SUMMARY"), "quarantine_empty": not (world.quarantine / SKILL).exists(), "error": None}


def case_tampered_tag(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    _ = world.publish(Release("v1", "publisher"))
    manifest = world.checkout / "manifests" / f"{SKILL}.json"
    _ = manifest.write_text(manifest.read_text(encoding="utf-8").replace('"changelog":"v1"', '"changelog":"tampered"'), encoding="utf-8")
    _ = world.repoint(f"{SKILL}/v1")
    synced = world.sync()
    return {"sync_failed": line(synced.stdout, "SYNC-FAILED"), "quarantine_empty": not (world.quarantine / SKILL).exists(), "error": None}


def case_wrong_digest(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    _ = world.publish(Release("v1", "publisher"))
    source = world.checkout / "skills" / SKILL / "SKILL.md"
    _ = source.write_text(source.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    _ = world.repoint(f"{SKILL}/v1")
    synced = world.sync()
    return {"sync_failed": line(synced.stdout, "SYNC-FAILED"), "quarantine_empty": not (world.quarantine / SKILL).exists(), "error": None}


def case_replay(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    _ = world.publish(Release("v1", "publisher"))
    _ = world.require_sync()
    _ = world.publish(Release("v2", "publisher"))
    _ = world.require_sync()
    return {"verify_prefix": verify_prefix(world, f"{SKILL}/v1"), "error": None}


def case_offline_catch_up(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    _ = world.publish(Release("v1", "publisher"))
    _ = world.require_sync()
    _ = world.publish(Release("v2", "publisher"))
    v3 = world.publish(Release("v3", "publisher"))
    synced = world.sync()
    activated = world.command((sys.executable, "-m", "automation.managed_sync", "activate-instructions", SKILL, "--live-root", str(world.live)))
    return {"sync_staged": line(synced.stdout, "SYNC-STAGED"), "sync_summary": line(synced.stdout, "SYNC-SUMMARY"), "quarantined_releases": len(tuple((world.quarantine / SKILL).iterdir())) - 1, "activation_sequence": 3 if v3.digest in activated.stdout else 0, "error": None}


def case_revocation(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    _ = world.publish(Release("v1", "publisher"))
    _ = world.require_sync()
    v2 = world.publish(Release("v2", "publisher"))
    v3 = world.publish(Release("v3", "publisher"))
    _ = world.require_sync()
    _ = world.live.mkdir()
    (world.live / SKILL).symlink_to(world.quarantine / SKILL / v3.digest, target_is_directory=True)
    _ = world.publish(Release("v4", "publisher", (v3.digest,)))
    synced = world.sync()
    config = load_config(world.config)
    try:
        _ = verify_release(config.mirror_dir, f"{SKILL}/v3", config, SkillState(4, v2.digest, None, (v3.digest,)), allow_rollback=True)
    except ManagedVerifyError as error:
        v3_prefix = error.prefix
    else:
        v3_prefix = "VERIFIED"
    return {"sync_removal_request": line(synced.stdout, "SYNC-REMOVAL-REQUEST"), "sync_summary": line(synced.stdout, "SYNC-SUMMARY"), "v3_verify_prefix": v3_prefix, "live_symlink_present": (world.live / SKILL).is_symlink(), "error": None}


def case_collision(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    _ = world.publish(Release("v1", "publisher"))
    _ = world.require_sync()
    _ = world.live.mkdir()
    _ = (world.live / "hello-autophagy").mkdir()
    result = world.command((sys.executable, "-m", "automation.managed_sync", "activate-instructions", SKILL, "--live-root", str(world.live)))
    return {"activation_error": line(result.stderr, "COLLISION-BLOCK"), "error": None}


def case_prefix_forgery(root: Path) -> dict[str, JsonValue]:
    world = World.create(root)
    code = "from pathlib import Path; import sys; import automation.skill_store as store; store.STORE_ROOT=Path(sys.argv[1]); store._require_root=lambda:None; sys.argv=['skill_store.py','install','--skill','managed-hello-autophagy','--hash','0'*64]; raise SystemExit(store.main())"
    result = world.command((sys.executable, "-c", code, str(world.root / "store")))
    return {"store_error": line(result.stderr, "SKILL-STORE-BLOCK"), "error": None}


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--world":
        return 2
    world_root = Path(sys.argv[2])
    cases = (
        ("publish_v1", case_publish_v1),
        ("subscriber_sync", case_subscriber_sync),
        ("bad_signature", case_bad_signature),
        ("tampered_tag", case_tampered_tag),
        ("wrong_digest", case_wrong_digest),
        ("replay", case_replay),
        ("offline_catch_up", case_offline_catch_up),
        ("revocation", case_revocation),
        ("collision", case_collision),
        ("prefix_forgery", case_prefix_forgery),
    )
    observations: dict[str, dict[str, JsonValue]] = {}
    for case_id, case in cases:
        try:
            observations[case_id] = case(world_root / case_id)
        except Exception as error:
            observations[case_id] = {"error": f"{type(error).__name__}: {error}"[:300]}
    print("OBS-JSON: " + json.dumps(observations, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
