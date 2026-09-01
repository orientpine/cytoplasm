from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from automation.interop import approval_reminder_config as reminder_config
from automation.interop.approval_reminder_config import (
    ApprovalReminderConfig,
    ApprovalReminderConfigError,
    load_approval_reminder_config,
    parse_approval_reminder_config,
)


def test_omitted_section_preserves_backward_compatible_defaults() -> None:
    config = parse_approval_reminder_config({"model": {"default": "example"}})
    assert config == ApprovalReminderConfig(
        enabled=True,
        initial_delay=timedelta(hours=3),
        repeat_interval=timedelta(hours=1),
    )


def test_explicit_values_use_existing_hermes_duration_forms() -> None:
    config = parse_approval_reminder_config(
        {
            "approval_reminders": {
                "enabled": False,
                "initial_delay": "90m",
                "repeat_interval": "2h",
            }
        }
    )
    assert config.enabled is False
    assert config.initial_delay == timedelta(minutes=90)
    assert config.repeat_interval == timedelta(hours=2)


def test_individual_values_can_be_omitted() -> None:
    config = parse_approval_reminder_config(
        {"approval_reminders": {"enabled": False}}
    )
    assert config == ApprovalReminderConfig(enabled=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("initial_delay", "0m"),
        ("initial_delay", "-1h"),
        ("initial_delay", "soon"),
        ("repeat_interval", "0h"),
        ("repeat_interval", "1.5h"),
        ("repeat_interval", 60),
    ],
)
def test_invalid_or_non_positive_intervals_fail_at_load_with_key_name(
    field: str, value: object
) -> None:
    with pytest.raises(ApprovalReminderConfigError, match=field):
        parse_approval_reminder_config(
            {"approval_reminders": {field: value}}
        )


@pytest.mark.parametrize("value", ["false", 0, None, [], {}])
def test_enabled_requires_a_real_yaml_boolean(value: object) -> None:
    with pytest.raises(ApprovalReminderConfigError, match=r"approval_reminders\.enabled"):
        parse_approval_reminder_config(
            {"approval_reminders": {"enabled": value}}
        )


def test_malformed_section_and_unknown_keys_are_rejected() -> None:
    with pytest.raises(ApprovalReminderConfigError, match="must be a YAML mapping"):
        parse_approval_reminder_config({"approval_reminders": True})
    with pytest.raises(ApprovalReminderConfigError, match="unknown setting.*typo"):
        parse_approval_reminder_config(
            {"approval_reminders": {"typo": "3h"}}
        )


def test_path_loader_reuses_yaml_and_reports_startup_errors(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "model:\n  default: example\napproval_reminders:\n"
        "  enabled: true\n  initial_delay: 3h\n  repeat_interval: 1h\n",
        encoding="utf-8",
    )
    assert load_approval_reminder_config(path=path) == ApprovalReminderConfig()

    path.write_text(
        "approval_reminders:\n  initial_delay: never\n",
        encoding="utf-8",
    )
    with pytest.raises(ApprovalReminderConfigError, match="initial_delay"):
        load_approval_reminder_config(path=path)


def test_loader_rejects_ambiguous_sources(tmp_path: Path) -> None:
    with pytest.raises(ApprovalReminderConfigError, match="either config or path"):
        load_approval_reminder_config(config={}, path=tmp_path / "config.yaml")


def test_disabled_config_is_an_explicit_consumer_model() -> None:
    config = load_approval_reminder_config(
        config={"approval_reminders": {"enabled": False}}
    )
    assert config.enabled is False


def test_a_home_directory_the_sandbox_hides_does_not_kill_the_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback home probe must answer, not raise, under ``ProtectHome=yes``.

    `autophagy-repair-approval-watch.service` runs `User=ops` with `ProtectHome=yes`,
    which presents /home to the service as an empty directory. `Path.is_file()` only
    swallows ENOENT/ENOTDIR/EBADF/ELOOP (CPython `_IGNORED_ERRNOS`), so probing
    ``~/.hermes/config.yaml`` raises EACCES instead of answering False — for a file that
    does not exist at all. And because that raise happens *inside* the
    ``except (ImportError, ModuleNotFoundError)`` clause, the sibling ``except Exception``
    cannot translate it: it escapes raw and the unit dies at startup.

    Measured on the primary node: every minute from 2026-08-21 07:58 UTC, 5,329
    PermissionErrors, with repair owner-approval reactions unconsumed the whole time.
    """
    module = reminder_config

    def no_hermes_config(name: str) -> object:
        raise ImportError(f"no module named {name}")

    def hidden_home(_self: Path) -> bool:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(module.importlib, "import_module", no_hermes_config)
    monkeypatch.setattr(Path, "is_file", hidden_home)

    config = module.load_approval_reminder_config()

    assert config == module.parse_approval_reminder_config({})
