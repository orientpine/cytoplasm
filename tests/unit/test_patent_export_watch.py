import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "skills" / "patent-prep"))

from scripts import patent_export, patent_export_gate
from scripts import patent_export_confirm_reaction_watch as watch
from scripts import patent_export_manifest as manifest
from scripts.patent_storage import PatentPaths, private_directory, write_private

OWNER = "123456789"
APPROVALS_CHANNEL = "1528936606856122421"  # digit-only: bindings refuse a placeholder id
OWNER_DM_CHANNEL = "1526487935975952385"
AGENT_CHAT_CHANNEL = "1526487935975952390"
AGENT_CHAT_THREAD = "1526487935975952391"
APPROVE = "\u2705"
CANCEL = "\u26d4"
MARKER = "SYNTH-BODY-MARKER-DO-NOT-LEAK"
State = manifest.State


class FakeDiscord:
    def __init__(self):
        self.messages: dict[str, str] = {}
        self.reactions: dict[str, list] = {}
        self._n = 0

    def api(self, method, path, payload=None):
        if method == "POST" and path.endswith("/messages"):
            self._n += 1
            mid = f"m{self._n}"
            self.messages[mid] = (payload or {}).get("content", "")
            return {"id": mid}
        if method == "GET" and "/reactions/" in path:
            from urllib.parse import unquote
            emoji = unquote(path.split("/reactions/", 1)[1].split("?", 1)[0])
            return self.reactions.get(emoji, [])
        if method == "GET" and "/messages/" in path:
            mid = path.rsplit("/", 1)[-1]
            return {"id": mid, "content": self.messages.get(mid, "")}
        if method == "POST" and path == "/users/@me/channels":
            return {"id": OWNER_DM_CHANNEL}
        if method == "GET" and path.startswith("/channels/") and path.count("/") == 2:
            return self.describe(path.rsplit("/", 1)[-1])
        if method == "GET" and path == "/guilds/guild/threads/active":
            return {"threads": [{
                "id": AGENT_CHAT_THREAD,
                "type": 11,
                "name": "승인-patent-export",
                "parent_id": AGENT_CHAT_CHANNEL,
            }]}
        return {"id": "x"}

    def describe(self, channel_id):
        if channel_id == OWNER_DM_CHANNEL:
            return {"id": channel_id, "type": 1, "recipients": [{"id": OWNER}]}
        if channel_id == AGENT_CHAT_CHANNEL:
            return {"id": channel_id, "type": 0, "name": "agent-chat", "guild_id": "guild"}
        if channel_id == AGENT_CHAT_THREAD:
            return {
                "id": channel_id,
                "type": 11,
                "name": "승인-patent-export",
                "parent_id": AGENT_CHAT_CHANNEL,
            }
        return {"id": channel_id, "type": 0, "name": "approvals"}

    def approve(self, user_id=OWNER, bot=False):
        self.reactions = {APPROVE: [{"id": user_id, "bot": bot}]}

    def cancel(self):
        self.reactions = {CANCEL: [{"id": OWNER, "bot": False}]}

    def clear(self):
        self.reactions = {}


def _stub(path: Path, log: Path, body: str) -> None:
    path.write_text(body.replace("LOG", str(log)), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path, monkeypatch):
    ssh = tmp_path / "id.pub"
    ssh.write_text("ssh-ed25519 AAAAtest test\n", encoding="utf-8")
    interop = tmp_path / "interop.json"
    interop.write_text(
        json.dumps({
            "owner_id": OWNER,
            "personal_approvals_channel_id": APPROVALS_CHANNEL,
            "agent_chat_channel_id": AGENT_CHAT_CHANNEL,
        }),
        encoding="utf-8",
    )
    _stub(tmp_path / "gws.sh", tmp_path / "gws.log", '#!/usr/bin/env bash\necho "$*" >> "LOG"\necho "{}"\n')
    _stub(tmp_path / "age.sh", tmp_path / "age.log", '#!/usr/bin/env bash\necho "$*" >> "LOG"\n')
    monkeypatch.setenv("PATENT_EXPORT_ROOT", str(tmp_path / "export"))
    monkeypatch.setenv("PATENT_ARCHIVE_FOLDER_ID", "folder123")
    monkeypatch.setenv("PATENT_DRAFT_ROOT", str(tmp_path / "drafts"))
    monkeypatch.setenv("PATENT_STATUS_ROOT", str(tmp_path / "status"))
    monkeypatch.setenv("PATENT_SSH_PUBKEY", str(ssh))
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-envtoken")
    monkeypatch.setenv("PATENT_GWS_BIN", str(tmp_path / "gws.sh"))
    monkeypatch.setenv("PATENT_AGE_BIN", str(tmp_path / "age.sh"))

    fake = FakeDiscord()
    monkeypatch.setattr(patent_export_gate, "_api", fake.api)

    paths = PatentPaths.from_environment()
    slug = "watch-slug"
    private_directory(paths.workspace_root / slug)
    write_private(paths.workspace_root / slug / "draft.md", MARKER + "\n")
    patent_export.prepare_export(paths, slug, mode="enc")  # PENDING with message_id
    return {"paths": paths, "slug": slug, "tmp": tmp_path, "fake": fake}


def test_watch_flips_pending_to_approved_on_owner_approve(env):
    env["fake"].approve()
    watch.run_once(manifest.now_ts())
    assert manifest.load_manifest(env["slug"]).state == State.APPROVED


def test_watch_flips_pending_to_cancelled_on_owner_cancel(env):
    env["fake"].cancel()
    watch.run_once(manifest.now_ts())
    assert manifest.load_manifest(env["slug"]).state == State.CANCELLED


def test_watch_ignores_non_owner_reaction(env):
    env["fake"].approve(user_id="999999")
    watch.run_once(manifest.now_ts())
    assert manifest.load_manifest(env["slug"]).state == State.PENDING


def test_watch_cancel_revokes_prior_approval(env):
    slug = env["slug"]
    with manifest.lock(slug):
        manifest.transition(slug, allowed_from={State.PENDING}, to=State.APPROVED)
    env["fake"].cancel()
    watch.run_once(manifest.now_ts())
    assert manifest.load_manifest(slug).state == State.CANCELLED


def test_watch_retains_pending_without_reaction(env):
    env["fake"].clear()
    watch.run_once(manifest.now_ts())
    assert manifest.load_manifest(env["slug"]).state == State.PENDING


def test_watch_never_uploads_or_encrypts(env):
    env["fake"].approve()
    watch.run_once(manifest.now_ts())
    assert not (env["tmp"] / "gws.log").exists()
    assert not (env["tmp"] / "age.log").exists()
    assert MARKER not in manifest.manifest_path(env["slug"]).read_text(encoding="utf-8")


def test_watch_ignores_consumed_manifest(env):
    slug = env["slug"]
    with manifest.lock(slug):
        manifest.transition(slug, allowed_from={State.PENDING}, to=State.APPROVED)
    with manifest.lock(slug):
        manifest.transition(slug, allowed_from={State.APPROVED}, to=State.CONSUMED)
    env["fake"].cancel()
    watch.run_once(manifest.now_ts())
    assert manifest.load_manifest(slug).state == State.CONSUMED
