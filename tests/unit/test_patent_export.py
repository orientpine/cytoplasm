import argparse
import dataclasses
import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "skills" / "patent-prep"))

from scripts import patent_cli, patent_export, patent_export_gate
from scripts import patent_export_manifest as manifest
from scripts.patent_storage import PatentPaths, private_directory, write_private

OWNER = "123456789"
APPROVALS_CHANNEL = "1528936606856122421"  # digit-only: bindings refuse a placeholder id
OWNER_DM_CHANNEL = "1526487935975952385"
AGENT_CHAT_CHANNEL = "1526487935975952390"
AGENT_CHAT_THREAD = "1526487935975952391"
REQUEST_THREAD = "1526487935975952392"
APPROVE = "\u2705"
CANCEL = "\u26d4"
MARKER = "SYNTH-BODY-MARKER-DO-NOT-LEAK"
State = manifest.State


class FakeDiscord:
    """In-process Discord transport (installed via monkeypatch, never an env var)."""

    def __init__(self):
        self.messages: dict[str, str] = {}
        self.reactions: dict[str, list] = {}
        self.posts: list = []
        self.threads: list = []
        self._n = 0

    def api(self, method, path, payload=None):
        self.posts.append((method, path, payload))
        if method == "POST" and path.endswith("/threads"):
            self.threads.append((payload or {}).get("name", ""))
            return {"id": REQUEST_THREAD}
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
        if channel_id in (AGENT_CHAT_THREAD, REQUEST_THREAD):
            return {
                "id": channel_id,
                "type": 11,
                "name": self.threads[-1] if self.threads else "승인-patent-export",
                "parent_id": AGENT_CHAT_CHANNEL,
            }
        return {"id": channel_id, "type": 0, "name": "approvals"}

    def approve(self, user_id=OWNER, bot=False):
        self.reactions = {APPROVE: [{"id": user_id, "bot": bot}]}

    def cancel(self):
        self.reactions = {CANCEL: [{"id": OWNER, "bot": False}]}

    def approve_and_cancel(self):
        self.reactions = {APPROVE: [{"id": OWNER, "bot": False}], CANCEL: [{"id": OWNER, "bot": False}]}

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
    perms = tmp_path / "perms.json"
    perms.write_text(json.dumps({"permissions": [{"id": "1", "type": "user", "role": "owner", "emailAddress": "cha@x"}]}), encoding="utf-8")
    _stub(
        tmp_path / "gws.sh", tmp_path / "gws.log",
        '#!/usr/bin/env bash\necho "$*" >> "LOG"\n'
        f'if [ "$1" = drive ] && [ "$2" = permissions ]; then cat "{perms}"\n'
        'elif [ "$1" = drive ] && [ "$2" = files ]; then echo \'{"id":"fileX","webViewLink":"https://drive.google.com/file/d/fileX/view"}\'\n'
        'else echo "{}"; fi\n',
    )
    _stub(
        tmp_path / "age.sh", tmp_path / "age.log",
        '#!/usr/bin/env bash\necho "$*" >> "LOG"\n'
        'o=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && o="$2"; shift; done\n'
        '[ -n "$o" ] && printf "CT\\n" > "$o"\n',
    )
    export_root = tmp_path / "export"
    monkeypatch.setenv("PATENT_EXPORT_ROOT", str(export_root))
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
    slug = "demo"
    private_directory(paths.workspace_root / slug)
    write_private(paths.workspace_root / slug / "draft.md", MARKER + "\n")
    return {"paths": paths, "slug": slug, "tmp": tmp_path, "fake": fake, "export_root": export_root, "perms": perms}


def _to_approved(slug: str) -> None:
    with manifest.lock(slug):
        manifest.transition(slug, allowed_from={State.PENDING}, to=State.APPROVED)


def _prepare_approved(env, *, mode="enc") -> None:
    patent_export.prepare_export(env["paths"], env["slug"], mode=mode)
    env["fake"].approve()
    _to_approved(env["slug"])


def _tamper(slug: str, **fields) -> None:
    path = manifest.manifest_path(slug)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(fields)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_execute_encrypts_and_uploads_on_owner_approval(env):
    _prepare_approved(env)
    out = patent_export.execute_export(env["paths"], env["slug"])
    assert "PATENT-EXPORTED" in out
    gws = (env["tmp"] / "gws.log").read_text().splitlines()
    assert "permissions list" in gws[0]
    assert "files create" in gws[1]
    assert "--upload draft.md.age" in gws[1]  # relative basename; gws rejects --upload paths outside cwd
    assert "-R" in (env["tmp"] / "age.log").read_text()
    assert manifest.load_manifest(env["slug"]).state == State.CONSUMED
    audit = (env["export_root"] / "audit.jsonl").read_text().splitlines()
    assert len(audit) == 1
    rec = json.loads(audit[0])
    assert rec["plaintext_sha256"] and rec["ciphertext_sha256"]
    assert rec["approval"]["method"] == "manual_reaction"


def test_execute_aborts_on_non_owner_folder_permission(env):
    env["perms"].write_text(json.dumps({"permissions": [{"id": "1", "type": "user", "role": "owner"}, {"id": "2", "type": "anyone", "role": "reader"}]}), encoding="utf-8")
    _prepare_approved(env)
    with pytest.raises(patent_export.PatentExportError):
        patent_export.execute_export(env["paths"], env["slug"])
    gws = (env["tmp"] / "gws.log").read_text().splitlines()
    assert "permissions list" in gws[0]
    assert not any("files create" in line for line in gws)
    assert manifest.load_manifest(env["slug"]).state == State.APPROVED


@pytest.mark.parametrize("cond", ["pending", "expired", "cancelled", "consumed", "changed"])
def test_execute_fail_closed(env, cond):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    env["fake"].approve()
    slug = env["slug"]
    if cond == "expired":
        with manifest.lock(slug):
            manifest.transition(slug, allowed_from={State.PENDING}, to=State.APPROVED, expiry_ts=1)
    elif cond == "cancelled":
        with manifest.lock(slug):
            manifest.transition(slug, allowed_from={State.PENDING}, to=State.CANCELLED)
    elif cond == "consumed":
        with manifest.lock(slug):
            manifest.transition(slug, allowed_from={State.PENDING}, to=State.CONSUMED)
    elif cond == "changed":
        _to_approved(slug)
        write_private(env["paths"].workspace_root / slug / "draft.md", "CHANGED\n")
    with pytest.raises(patent_export.PatentExportError):
        patent_export.execute_export(env["paths"], slug)
    assert not (env["tmp"] / "gws.log").exists()


def test_execute_refuses_without_owner_reaction(env):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    env["fake"].clear()
    _to_approved(env["slug"])
    with pytest.raises(patent_export.PatentExportError):
        patent_export.execute_export(env["paths"], env["slug"])
    assert not (env["tmp"] / "gws.log").exists()


def test_execute_refuses_on_non_owner_reaction(env):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    env["fake"].approve(user_id="999999")
    _to_approved(env["slug"])
    with pytest.raises(patent_export.PatentExportError):
        patent_export.execute_export(env["paths"], env["slug"])
    assert not (env["tmp"] / "gws.log").exists()


def test_execute_refuses_on_bot_reaction(env):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    env["fake"].approve(bot=True)
    _to_approved(env["slug"])
    with pytest.raises(patent_export.PatentExportError):
        patent_export.execute_export(env["paths"], env["slug"])
    assert not (env["tmp"] / "gws.log").exists()


def test_execute_cancels_on_live_cancel_reaction(env):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    env["fake"].approve_and_cancel()  # ⛔ precedence
    _to_approved(env["slug"])
    with pytest.raises(patent_export.PatentExportError):
        patent_export.execute_export(env["paths"], env["slug"])
    assert manifest.load_manifest(env["slug"]).state == State.CANCELLED
    assert not (env["tmp"] / "gws.log").exists()


def test_execute_refuses_on_mode_downgrade_tamper(env):
    _prepare_approved(env, mode="enc")
    _tamper(env["slug"], mode="plaintext")
    with pytest.raises((patent_export.PatentExportError, patent_export_gate.ExportGateError)):
        patent_export.execute_export(env["paths"], env["slug"])
    assert not (env["tmp"] / "gws.log").exists()


def test_execute_refuses_on_destination_tamper(env):
    _prepare_approved(env, mode="enc")
    _tamper(env["slug"], dest_folder_id="attacker-folder")
    with pytest.raises(patent_export.PatentExportError):
        patent_export.execute_export(env["paths"], env["slug"])
    assert not (env["tmp"] / "gws.log").exists()


def test_binding_rejects_substring_folder(env):
    # A folder id that is a SUBSTRING of the approved one must NOT satisfy the binding.
    # Exercise reaction_state directly so the execute-level allowlist check (which also
    # rejects a changed dest) does not short-circuit the binding logic under test.
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    m = manifest.load_manifest(env["slug"])
    env["fake"].approve()
    tampered = dataclasses.replace(m, dest_folder_id="folder12")  # prefix of approved "folder123"
    with pytest.raises(patent_export_gate.ExportGateError):
        patent_export_gate.reaction_state(tampered)


def test_load_manifest_rejects_invalid_mode(env):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    _tamper(env["slug"], mode="raw")
    with pytest.raises(manifest.ManifestError):
        manifest.load_manifest(env["slug"])


def test_execute_second_invocation_refused_under_lock(env):
    _prepare_approved(env)
    with manifest.lock(env["slug"]):
        with pytest.raises(manifest.ManifestError):
            patent_export.execute_export(env["paths"], env["slug"])


def test_no_draft_body_in_any_artifact(env, capsys):
    _prepare_approved(env)
    patent_export.execute_export(env["paths"], env["slug"])
    captured = capsys.readouterr()
    for _method, _path, payload in env["fake"].posts:
        assert MARKER not in json.dumps(payload or {})
    for content in env["fake"].messages.values():
        assert MARKER not in content
    assert MARKER not in manifest.manifest_path(env["slug"]).read_text(encoding="utf-8")
    assert MARKER not in (env["export_root"] / "audit.jsonl").read_text(encoding="utf-8")
    assert MARKER not in captured.out and MARKER not in captured.err


def test_default_mode_is_enc(env):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    assert manifest.load_manifest(env["slug"]).mode == "enc"


def test_cli_allow_plaintext_sets_labeled_plaintext_approval(env):
    patent_cli._export_prepare(argparse.Namespace(slug=env["slug"], allow_plaintext=True))
    assert manifest.load_manifest(env["slug"]).mode == "plaintext"
    posted = [p for _m, path, p in env["fake"].posts if str(path).endswith("/messages") and "mode=plaintext" in (p or {}).get("content", "")]
    assert posted


def test_cli_default_is_enc(env):
    patent_cli._export_prepare(argparse.Namespace(slug=env["slug"], allow_plaintext=False))
    assert manifest.load_manifest(env["slug"]).mode == "enc"


def test_reaction_state_owner_only_and_cancel_precedence(env):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    m = manifest.load_manifest(env["slug"])
    env["fake"].clear()
    assert patent_export_gate.reaction_state(m) is None
    env["fake"].approve(user_id="999999")
    assert patent_export_gate.reaction_state(m) is None
    env["fake"].approve(bot=True)
    assert patent_export_gate.reaction_state(m) is None
    env["fake"].approve()
    assert patent_export_gate.reaction_state(m) == APPROVE
    env["fake"].approve_and_cancel()
    assert patent_export_gate.reaction_state(m) == CANCEL


def test_reaction_state_fails_closed_on_binding_mismatch(env):
    patent_export.prepare_export(env["paths"], env["slug"], mode="enc")
    m = manifest.load_manifest(env["slug"])
    env["fake"].messages = {k: "tampered message without the bound hash" for k in env["fake"].messages}
    env["fake"].approve()
    with pytest.raises(patent_export_gate.ExportGateError):
        patent_export_gate.reaction_state(m)


def test_gate_module_has_no_env_stub_backdoor():
    # Inspect the real module SOURCE (not a monkeypatched attribute): the production
    # gate must contain no env-triggered stub that could forge an owner reaction.
    import inspect

    src = inspect.getsource(patent_export_gate)
    assert "PATENT_DISCORD_STUB" not in src
    assert "PATENT_STUB_REACTION" not in src
    assert not hasattr(patent_export_gate, "_stub_api")


def test_export_does_not_alter_model_routing():
    from scripts.patent_routing import plan_patent_call

    call = plan_patent_call(("review",))
    assert call.provider == "openai-codex"
    assert call.model == "gpt-5.4"
    assert call.tag_auto_attached is True
