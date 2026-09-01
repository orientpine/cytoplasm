"""Runtime-root resolution for the mounted mail skill.

The skill may run from ``/srv/autophagy-skills/releases/<skill>/<hash>/scripts``,
where a parent-depth guess cannot locate the automation checkout. Resolve a candidate
that carries entity-preflight instead, and keep imports lazy so deploy loading works
without the repository on ``sys.path``.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Final, TypeVar

if TYPE_CHECKING:
    from typing import override
else:
    try:
        from typing import override
    except ImportError:
        # Hermes no-agent cron runs uv CPython 3.11 (no typing.override); this mirrors
        # automation/typing_compat.py because skill scripts import before the automation
        # package is reachable on sys.path (see module docstring).
        _MethodT = TypeVar("_MethodT")

        def override(method: _MethodT, /) -> _MethodT:
            return method


__all__ = ("MailPreflightError", "repo_root", "_repo_module", "_contracts", "_gate")

RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
MIRROR_CHECKOUT: Final = Path("/srv/autophagy-agents")


@dataclass(frozen=True, slots=True)
class MailPreflightError(RuntimeError):
    """Mail execution stopped before the existing approval-gated sender."""

    message: str
    exit_code: int
    should_render: bool = False

    @override
    def __str__(self) -> str:
        return self.message


def repo_root(
    here: Path | None = None,
    *,
    current: Path | None = None,
    mirror: Path | None = None,
) -> Path:
    """Return the checkout that actually carries ``automation``."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    release = current if current is not None else RELEASE_CURRENT
    resident = mirror if mirror is not None else MIRROR_CHECKOUT
    origin = (here or Path(__file__)).resolve()
    for candidate in (*origin.parents[2:6], release, resident):
        if (candidate / "automation" / "entity_preflight").is_dir():
            return candidate
    return release if (release / "automation").is_dir() else resident


def _repo_module(name: str) -> ModuleType:
    """Lazily import an entity-preflight module or fail closed."""
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    bound = sys.modules.get("automation")
    repo_automation = str(root / "automation")
    if bound is not None and hasattr(bound, "__path__") and repo_automation not in bound.__path__:
        bound.__path__.append(repo_automation)
    try:
        return importlib.import_module(f"automation.entity_preflight.{name}")
    except ImportError:
        raise MailPreflightError(
            f"개인 고유명사 preflight 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 발송 거부", 3
        ) from None


def _contracts() -> ModuleType:
    return _repo_module("contracts")


def _gate() -> ModuleType:
    return _repo_module("gate")
