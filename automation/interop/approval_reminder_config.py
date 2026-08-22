"""Typed ``config.yaml`` model for approval-reminder scheduling.

The values are ordinary, non-secret Hermes settings.  They deliberately have
no environment-variable override: ``config.yaml`` is the single configuration
surface.  The loader accepts an already loaded mapping so watchers can reuse
their existing Hermes config load, while the default path calls Hermes'
canonical loader directly.
"""
from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final, Protocol, cast


SECTION: Final = "approval_reminders"
DEFAULT_ENABLED: Final = True
DEFAULT_INITIAL_DELAY: Final = timedelta(hours=3)
DEFAULT_REPEAT_INTERVAL: Final = timedelta(hours=1)
_ALLOWED_KEYS: Final = frozenset({"enabled", "initial_delay", "repeat_interval"})
_DURATION = re.compile(
    r"^(?P<value>\d+)\s*(?P<unit>m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$",
    re.IGNORECASE,
)
_DURATION_MULTIPLIERS: Final = {"m": 1, "h": 60, "d": 1440}


class ApprovalReminderConfigError(ValueError):
    """The approval-reminder section cannot be used safely at startup."""


@dataclass(frozen=True, slots=True)
class ApprovalReminderConfig:
    """Validated settings consumed by the shared reminder scheduler."""

    enabled: bool = DEFAULT_ENABLED
    initial_delay: timedelta = DEFAULT_INITIAL_DELAY
    repeat_interval: timedelta = DEFAULT_REPEAT_INTERVAL

    def __post_init__(self) -> None:
        _require_positive_interval("initial_delay", self.initial_delay)
        _require_positive_interval("repeat_interval", self.repeat_interval)


def _require_positive_interval(name: str, value: timedelta) -> None:
    if value <= timedelta(0):
        raise ApprovalReminderConfigError(
            f"{SECTION}.{name} must be greater than zero"
        )


def _duration_minutes(value: object, *, name: str) -> int:
    """Parse the integer minute/hour/day forms accepted by Hermes cron."""
    if not isinstance(value, str):
        raise ApprovalReminderConfigError(
            f"{SECTION}.{name} must be a duration string such as '3h'"
        )
    match = _DURATION.fullmatch(value.strip())
    if match is None:
        raise ApprovalReminderConfigError(
            f"{SECTION}.{name} has invalid duration {value!r}; use a value such as '30m', '3h', or '1d'"
        ) from None
    minutes = int(match.group("value")) * _DURATION_MULTIPLIERS[
        match.group("unit")[0].lower()
    ]
    if minutes <= 0:
        raise ApprovalReminderConfigError(
            f"{SECTION}.{name} must be greater than zero"
        )
    return minutes


def parse_approval_reminder_config(config: object) -> ApprovalReminderConfig:
    """Validate the optional section; omission preserves old deployments."""
    if not isinstance(config, Mapping):
        raise ApprovalReminderConfigError("config.yaml must contain a YAML mapping")
    mapping = cast(Mapping[object, object], config)
    if SECTION not in mapping:
        return ApprovalReminderConfig()

    section = mapping[SECTION]
    if not isinstance(section, Mapping):
        raise ApprovalReminderConfigError(f"{SECTION} must be a YAML mapping")
    section_mapping = cast(Mapping[object, object], section)
    unknown = sorted(str(key) for key in section_mapping if key not in _ALLOWED_KEYS)
    if unknown:
        raise ApprovalReminderConfigError(
            f"{SECTION} contains unknown setting(s): {', '.join(map(str, unknown))}"
        )

    enabled = section_mapping.get("enabled", DEFAULT_ENABLED)
    if not isinstance(enabled, bool):
        raise ApprovalReminderConfigError(f"{SECTION}.enabled must be true or false")
    initial = section_mapping.get("initial_delay", "3h")
    repeat = section_mapping.get("repeat_interval", "1h")
    return ApprovalReminderConfig(
        enabled=enabled,
        initial_delay=timedelta(
            minutes=_duration_minutes(initial, name="initial_delay")
        ),
        repeat_interval=timedelta(
            minutes=_duration_minutes(repeat, name="repeat_interval")
        ),
    )


def _load_yaml(path: Path) -> Mapping[str, object]:
    """Read only this feature's flat YAML section with the standard library.

    Production watchers use Hermes' canonical full-file loader. This path hook is for
    deterministic validation and intentionally recognizes only the three scalar keys
    owned by this module rather than introducing a second general YAML implementation.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise ApprovalReminderConfigError(f"config.yaml not found: {path}") from error
    except OSError as error:
        raise ApprovalReminderConfigError(f"cannot read config.yaml: {path}: {error}") from error

    section: dict[str, object] | None = None
    for number, raw in enumerate(lines, start=1):
        if "\t" in raw:
            raise ApprovalReminderConfigError(
                f"cannot parse config.yaml: {path}:{number}: tabs are not allowed"
            )
        content = raw.split("#", 1)[0].rstrip()
        if not content:
            continue
        indent = len(content) - len(content.lstrip(" "))
        text = content.strip()
        if indent == 0:
            if text == f"{SECTION}:":
                if section is not None:
                    raise ApprovalReminderConfigError(f"duplicate {SECTION} section")
                section = {}
            elif text.startswith(f"{SECTION}:"):
                raise ApprovalReminderConfigError(f"{SECTION} must be a YAML mapping")
            elif section is not None:
                break
            continue
        if section is None:
            continue
        if indent < 2 or ":" not in text:
            raise ApprovalReminderConfigError(
                f"cannot parse {SECTION} setting at {path}:{number}"
            )
        key, raw_value = (part.strip() for part in text.split(":", 1))
        if not key or not raw_value or key in section:
            raise ApprovalReminderConfigError(
                f"cannot parse {SECTION} setting at {path}:{number}"
            )
        value: object
        if raw_value in {"true", "false"}:
            value = raw_value == "true"
        else:
            value = raw_value.strip("'\"")
        section[key] = value
    return {} if section is None else {SECTION: section}


class _ConfigLoader(Protocol):
    def __call__(self) -> object: ...


def load_approval_reminder_config(
    *,
    config: Mapping[str, object] | None = None,
    path: Path | None = None,
) -> ApprovalReminderConfig:
    """Load and validate startup settings through the existing config system.

    ``config`` is intended for a watcher that already loaded Hermes settings.
    With neither argument, Hermes' canonical loader is reused.  ``path`` is a
    deterministic test/operator hook and still uses safe YAML parsing.
    """
    if config is not None and path is not None:
        raise ApprovalReminderConfigError("pass either config or path, not both")
    if path is not None:
        config = _load_yaml(path)
    elif config is None:
        try:
            module = importlib.import_module("hermes_cli.config")
            loader = cast(_ConfigLoader, getattr(module, "load_config"))
            loaded = loader()
            if not isinstance(loaded, Mapping):
                raise ApprovalReminderConfigError(
                    "config.yaml must contain a YAML mapping"
                )
            loaded_mapping = cast(Mapping[object, object], loaded)
            config = {str(key): value for key, value in loaded_mapping.items()}
        except (ImportError, ModuleNotFoundError):
            default_path = Path.home() / ".hermes" / "config.yaml"
            config = _load_yaml(default_path) if default_path.is_file() else {}
        except Exception as error:  # noqa: BLE001 - translate to startup-facing config error
            raise ApprovalReminderConfigError(
                f"cannot load config.yaml at watcher startup: {error}"
            ) from error
    return parse_approval_reminder_config(config)
