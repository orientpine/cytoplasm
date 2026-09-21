"""Run the real deploy entrypoint against local transport, never SSH or Discord.

Separate regression file: historical deployment tests have frozen FS3 receipts.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Protocol, TypedDict

import pytest

REPO = Path(__file__).resolve().parents[2]
PLUGIN = ".hermes/plugins/00-meeting-gate/__init__.py"
SOURCE = "skills/meeting/plugin/__init__.py"


class _Location(TypedDict):
    search: list[str]


class _Message(TypedDict):
    subject_key: str
    fact: str
    owner: dict[str, str]
    location: _Location


class _Event(TypedDict, total=False):
    message: _Message
    channel: str
    token_loaded: bool
    dm_owner: str


class _Run(Protocol):
    def __call__(self, **overrides: str) -> tuple[subprocess.CompletedProcess[str], list[_Event]]: ...


Deployment = tuple[_Run, Path, Path, Path]


@pytest.fixture
def deployment(tmp_path: Path) -> Deployment:
    repo = tmp_path / "repo"
    home = tmp_path / "node-home"
    bin_dir = tmp_path / "bin"
    shim = tmp_path / "shim"
    for directory in (repo, home, bin_dir, shim):
        directory.mkdir()
    paths = [
        "skills/meeting/deploy.sh", SOURCE, "skills/meeting/plugin/plugin.yaml",
        "skills/meeting/scripts/meeting_pending_transcript_watch.py",
        "automation/deploy_push.sh", "automation/deploy_provenance.sh",
        "automation/node_config_sh.py", "automation/node_config.py",
        "configs/node.example.toml",
    ]
    adapter = "skills/meeting/scripts/meeting_deploy_notice.py"
    if (REPO / adapter).exists():
        paths.append(adapter)
    for relative in paths:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(REPO / relative, target)
    env = {
        "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DEPLOY_SSH_HOST": "fake-node", "DEPLOY_PROVENANCE_REF": "HEAD",
        "AUTOPHAGY_RUNTIME_ROOT": f"{shim}:{REPO}",
        "HEALTHCHECK_NODE_CONFIG_PATH": str(repo / "configs/node.example.toml"),
        "FAKE_LOG": str(tmp_path / "events.jsonl"),
        "PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C.UTF-8",
    }
    for args in (("init", "-q"), ("add", "."),
                 ("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                  "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")):
        _ = subprocess.run(["git", "-C", str(repo), *args], env=env, check=True,
                       capture_output=True, timeout=10)
    plugin = home / PLUGIN
    plugin.parent.mkdir(parents=True)
    _ = plugin.write_text("old plugin\n")
    config = home / ".hermes/interop/config.json"
    config.parent.mkdir(parents=True)
    _ = config.write_text(json.dumps({"owner_notice_channel_id": "123", "owner_id": "456"}))
    _ = (home / ".env.secrets").write_text("DISCORD_BOT_TOKEN=DUMMY-notice-token\n")
    _ = (shim / "sitecustomize.py").write_text('''
import dataclasses, json, os, socket
from pathlib import Path
from automation import owner_notice

def record(value):
    with Path(os.environ["FAKE_LOG"]).open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\\n")

def forbidden(*args, **kwargs):
    raise AssertionError("network forbidden")
socket.socket = forbidden
real_notify = owner_notice.notify_owner
def notify(*args, **kwargs):
    record({"message": dataclasses.asdict(kwargs["message"])})
    return real_notify(*args, **kwargs)
owner_notice.notify_owner = notify

def send(token, channel_id, body):
    record({"channel": channel_id, "body": body, "token_loaded": token == "DUMMY-notice-token"})
    if os.environ.get("FAIL_NOTICE"):
        raise OSError("synthetic delivery failure")
owner_notice.send_notice = send

def dm(token, owner_id):
    record({"dm_owner": owner_id})
    return "789"
owner_notice.owner_dm_channel = dm
''')
    _ = (bin_dir / "ssh").write_text('''#!/usr/bin/env bash
set -eu
[[ "$1" == fake-node ]]
printf '%s\\n' "$2" >> "$HOME/commands.log"
if [[ "${FAIL_SSH:-}" == 1 ]]; then echo FAKE-SSH-FAIL >&2; exit 42; fi
sudo() {
  [[ "$1 $2 $3 $4 $5 $6" == "-n -u agent -H bash -lc" ]]
  if [[ "${DROP_PLUGIN:-}" == 1 && "$7" == *'cat > "$HOME/.hermes/plugins/00-meeting-gate/__init__.py"'* ]]; then
    cat >/dev/null; return 0
  fi
  bash -c "$7"
}
export -f sudo
bash -c "$2"
''')
    _ = (bin_dir / "hermes").write_text('''#!/usr/bin/env bash
if [[ "${FAIL_CRON:-}" == 1 ]]; then echo FAKE-CRON-FAIL >&2; exit 43; fi
printf 'Name: meeting-pending-transcript-watch\\n'
''')
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)

    def run(**overrides: str) -> tuple[subprocess.CompletedProcess[str], list[_Event]]:
        result = subprocess.run(
            ["bash", str(repo / "skills/meeting/deploy.sh")], cwd=repo,
            env={**env, **overrides}, text=True, capture_output=True, timeout=20,
        )
        log = Path(env["FAKE_LOG"])
        events: list[_Event] = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        commands = home / "commands.log"
        if commands.exists():
            assert "systemctl" not in commands.read_text()
        return result, events

    return run, repo, home, config


@pytest.mark.parametrize("initial", ["old", "missing"])
def test_changed_plugin_reaches_owner_channel_once(deployment: Deployment, initial: str) -> None:
    run, repo, home, _ = deployment
    before = hashlib.sha256((home / PLUGIN).read_bytes()).hexdigest()
    if initial == "missing":
        (home / PLUGIN).unlink()
        before = ""
    result, events = run()
    assert result.returncode == 0, result.stderr
    assert "PLUGIN-CHANGED:" in result.stderr
    assert "[deploy-provenance] OK:" in result.stderr
    assert result.stdout == "Name: meeting-pending-transcript-watch\n"
    messages = [event["message"] for event in events if "message" in event]
    assert len(messages) == 1, result.stderr
    after = hashlib.sha256((repo / SOURCE).read_bytes()).hexdigest()
    assert messages[0]["subject_key"] == "meeting-plugin-restart"
    assert before[:12] in messages[0]["fact"] and after[:12] in messages[0]["fact"]
    assert messages[0]["owner"]["verb"] == "open"
    assert messages[0]["location"]["search"][1] == PLUGIN
    sent = [event for event in events if "channel" in event]
    assert len(sent) == 1 and sent[0].get("channel") == "123"
    assert sent[0].get("token_loaded")
    assert (home / PLUGIN).read_bytes() == (repo / SOURCE).read_bytes()
    # The next real invocation sees identical bytes: no duplicate owner notice.
    again, repeated = run()
    assert again.returncode == 0 and repeated == events
    assert "PLUGIN-CHANGED:" not in again.stderr


def test_unchanged_plugin_is_silent(deployment: Deployment) -> None:
    run, repo, home, _ = deployment
    _ = shutil.copyfile(repo / SOURCE, home / PLUGIN)
    result, events = run()
    assert result.returncode == 0 and not events
    assert "PLUGIN-CHANGED:" not in result.stderr


def test_unconfigured_channel_uses_facade_dm(deployment: Deployment) -> None:
    run, _, _, config = deployment
    _ = config.write_text(json.dumps({"owner_id": "456"}))
    result, events = run()
    assert result.returncode == 0
    assert {"dm_owner": "456"} in events
    assert [event["channel"] for event in events if "channel" in event] == ["789"]


@pytest.mark.parametrize("failure,marker", [
    ({"FAIL_NOTICE": "1"}, "NOTIFY-FAILED"),
    ({"AUTOPHAGY_RUNTIME_ROOT": "/nonexistent"}, "ModuleNotFoundError"),
])
def test_notice_failure_is_loud_without_changing_deploy_result(
    deployment: Deployment, failure: dict[str, str], marker: str,
) -> None:
    run, _, _, _ = deployment
    result, _ = run(**failure)
    assert result.returncode == 0, result.stderr
    assert marker in result.stderr
    assert "PLUGIN-NOTICE-FAIL:" in result.stderr
    assert result.stdout == "Name: meeting-pending-transcript-watch\n"


@pytest.mark.parametrize("failure,code,marker", [
    ({"DROP_PLUGIN": "1"}, 5, "DEPLOY-BLOCK:"),
    ({"FAIL_SSH": "1"}, 42, "FAKE-SSH-FAIL"),
])
def test_deploy_error_prevents_notice(
    deployment: Deployment, failure: dict[str, str], code: int, marker: str,
) -> None:
    run, _, _, _ = deployment
    result, events = run(**failure)
    assert result.returncode == code and marker in result.stderr
    assert not events


@pytest.mark.parametrize("source", [SOURCE, "skills/meeting/scripts/meeting_deploy_notice.py"])
def test_provenance_refusal_prevents_remote_effects(deployment: Deployment, source: str) -> None:
    run, repo, home, _ = deployment
    with (repo / source).open("a") as stream:
        _ = stream.write("# dirty\n")
    result, events = run()
    assert result.returncode == 4 and "DEPLOY-BLOCK:" in result.stderr
    assert not events and not (home / "commands.log").exists()


def test_missing_credentials_are_loud_without_sending(deployment: Deployment) -> None:
    run, _, home, _ = deployment
    (home / ".env.secrets").unlink()
    result, events = run()
    assert result.returncode == 0
    assert "NOTIFY-UNCONFIGURED:" in result.stderr and "PLUGIN-NOTICE-FAIL:" in result.stderr
    assert not any("channel" in event for event in events)


def test_later_cron_error_is_preserved_after_notice(deployment: Deployment) -> None:
    run, _, _, _ = deployment
    result, events = run(FAIL_CRON="1")
    assert result.returncode == 43 and "FAKE-CRON-FAIL" in result.stderr
    assert len([event for event in events if "channel" in event]) == 1
