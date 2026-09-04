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
from collections.abc import Mapping
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


__all__ = (
    "MailPreflightError",
    "governed_copy_refusal",
    "repo_root",
    "_repo_module",
    "_contracts",
    "_gate",
)

RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
MIRROR_CHECKOUT: Final = Path("/srv/autophagy-agents")
#: 관리자 배포본이 마운트되는 유일한 루트 — ``automation.skill_mount.LIVE_ROOT`` 와 같은 값.
#: 스킬 스크립트는 import 시점에 automation 패키지를 쓸 수 없어 값을 여기서 다시 적는다
#: (``tests/unit/test_mail_governed_copy_guard.py`` 가 두 값의 동일성을 고정한다).
GOVERNED_LIVE_ROOT: Final = Path("/srv/autophagy-skills/live")
#: live 루트 주입 — ``automation.skill_mount.LIVE_ROOT_ENV`` 와 같은 이름. 샌드박스 scenario.sh 와
#: e2e actor 는 자기 사본의 루트(``<repo>/skills``)를 선언해 "지금 검사하는 사본이 곧 배포본"임을 밝힌다.
LIVE_ROOT_ENV: Final = "AUTOPHAGY_SKILL_LIVE_ROOT"
SKILL_NAME: Final = "mail"
STALE_COPY_MARKER: Final = "STALE-SKILL-COPY-BLOCK"


def governed_copy_refusal(script: Path, *, env: Mapping[str, str] | None = None) -> str | None:
    """변경 가능한 메일 작업 전 canonical governed 사본 판정을 지연 호출한다.

    스킬은 배포될 때 automation을 import할 수 없으므로, 런타임 체크아웃을 먼저 찾아
    canonical 판정을 쓴다. 그 import조차 불가능한 노드에서는 기존 inline 판정으로
    fail-closed 하여 낡은 사본이 발송하는 일을 막는다.
    """
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    bound = sys.modules.get("automation")
    repo_automation = str(root / "automation")
    if bound is not None and hasattr(bound, "__path__") and repo_automation not in bound.__path__:
        bound.__path__.append(repo_automation)
    try:
        from automation.skill_mount import governed_copy_refusal as canonical_governed_copy_refusal
    except ImportError:
        environment = os.environ if env is None else env
        override = environment.get(LIVE_ROOT_ENV, "").strip()
        live_root = Path(override).expanduser() if override else GOVERNED_LIVE_ROOT
        governed = live_root / SKILL_NAME / "scripts"
        try:
            if not governed.is_dir():
                return None
            same = governed.resolve() == script.resolve().parent
        except OSError as error:
            return (
                f"{STALE_COPY_MARKER}: 관리자 배포본 {governed} 를 판정할 수 없다"
                f"({error.__class__.__name__}) — 이 사본 {script} 을 실행하지 않는다"
            )
        if same:
            return None
        return (
            f"{STALE_COPY_MARKER}: mail 은 관리자 배포본 {governed / script.name} 에서만 실행한다"
            f" — 이 사본 {script} 은 마운트 판정(readlink {live_root / SKILL_NAME}) 밖이라 낡았을 수 있다"
        )
    return canonical_governed_copy_refusal(SKILL_NAME, script, env=env)


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
