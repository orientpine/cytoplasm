from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError, URLError

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
os.environ["CALENDAR_SCRIPTS"] = str(_SCRIPTS)
sys.path.insert(0, str(_SCRIPTS))


def _load_watch_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "calendar_confirm_reaction_watch_subprocess", _SCRIPTS / "confirm_reaction_watch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watch = _load_watch_module()

def _entry() -> object:
    return watch.PendingConfirm(
        draft_id="draft123",
        sha256="hash123",
        dm_channel_id="dm123",
        dm_message_id="msg123",
        created=datetime(2026, 7, 19, tzinfo=UTC),
    )


class _ApprovedDiscord:
    def message_content(self, _entry: object) -> str:
        return "sha256:hash123"

    def reaction_users(self, _entry: object, emoji: str) -> tuple[dict[str, str | bool], ...]:
        return ({"id": "owner", "bot": False},) if emoji == watch.APPROVE_EMOJI else ()

    def send_owner_dm(self, _content: str) -> None:
        raise AssertionError("approval does not send a result DM")

@pytest.mark.parametrize("return_code", [1, 6])
def test_child_nonzero_is_retained_logged_and_propagated_to_watcher(
    return_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given a confirmed entry whose child returns a distinct gate failure.
    child = tmp_path / "calendar_cli.py"
    _ = child.write_text(
        "import sys\n"
        "print('diagnostic stdout token=calendar-fixture-secret')\n"
        "print('HTTP Error 429 Retry-After: 7', file=sys.stderr)\n"
        f"raise SystemExit({return_code})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(watch.calendar_confirm, "bot_token", lambda: "fixture-credential")
    store = watch.PendingConfirmStore(tmp_path / "pending.jsonl")
    store.append(_entry())

    # When
    with pytest.raises(watch.ConfirmBatchError) as caught:
        watch.run_once(
            store=store,
            owner_id="owner",
            discord=_ApprovedDiscord(),
            commands=watch.CliCommands(child),
            draft_sha256=lambda _draft: "hash123",
            now=datetime(2026, 7, 19, 1, tzinfo=UTC),
        )

    # Then the original rc and safe bounded diagnostics survive, while the token does not.
    assert caught.value.exit_code == return_code
    assert len(store.load()) == 1
    record = json.loads(capsys.readouterr().err)
    assert record["event"] == "calendar_confirm_watch_failure"
    assert record["stage"] == "subprocess.confirm"
    assert record["child_returncode"] == return_code
    assert record["http_status"] == 429
    assert record["retry_after"] == "7"
    assert record["retryable"] is True
    assert "diagnostic stdout" in record["stdout_tail"]
    assert "calendar-fixture-secret" not in record["stdout_tail"]
    assert "[REDACTED]" in record["stdout_tail"]


def test_discord_network_error_is_retryable_and_fails_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    def fail_message(_entry: object) -> str:
        raise URLError("temporary resolver failure")

    monkeypatch.setattr(watch.calendar_confirm, "confirmation_message_content", fail_message)
    store = watch.PendingConfirmStore(tmp_path / "pending.jsonl")
    store.append(_entry())

    class Commands:
        def confirm(self, _pending: object, _owner: str) -> None:
            raise AssertionError("network failure must not execute confirm")

        def discard(self, _draft: str) -> None:
            raise AssertionError("network failure must not discard")

    # When
    with pytest.raises(watch.ConfirmBatchError):
        watch.run_once(
            store=store,
            owner_id="owner",
            discord=watch.DiscordApi("owner"),
            commands=Commands(),
            draft_sha256=lambda _draft: "hash123",
            now=datetime(2026, 7, 19, 1, tzinfo=UTC),
        )

    # Then
    assert len(store.load()) == 1
    record = json.loads(capsys.readouterr().err)
    assert record["cause_type"] == "URLError"
    assert record["retryable"] is True


@pytest.mark.parametrize("status", [429, 503])
def test_discord_rate_limit_and_transient_http_error_fail_tick_without_execution(
    status: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    headers = Message()
    headers["Retry-After"] = "9"
    http_error = HTTPError("https://discord.invalid/private", status, "temporary", headers, None)

    def fail_message(_entry: object) -> str:
        raise http_error

    monkeypatch.setattr(watch.calendar_confirm, "confirmation_message_content", fail_message)
    store = watch.PendingConfirmStore(tmp_path / "pending.jsonl")
    store.append(_entry())

    class Commands:
        def confirm(self, _pending: object, _owner: str) -> None:
            raise AssertionError("HTTP failure must not execute confirm")

        def discard(self, _draft: str) -> None:
            raise AssertionError("HTTP failure must not discard")

    # When
    with pytest.raises(watch.ConfirmBatchError) as caught:
        watch.run_once(
            store=store,
            owner_id="owner",
            discord=watch.DiscordApi("owner"),
            commands=Commands(),
            draft_sha256=lambda _draft: "hash123",
            now=datetime(2026, 7, 19, 1, tzinfo=UTC),
        )

    # Then
    assert caught.value.exit_code == 1
    assert len(store.load()) == 1
    record = json.loads(capsys.readouterr().err)
    assert record["http_status"] == status
    assert record["retry_after"] == "9"
    assert record["retryable"] is True
    assert "private" not in json.dumps(record)
