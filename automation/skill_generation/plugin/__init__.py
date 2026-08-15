from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

_RUNTIME: Path = Path("~/.hermes/skill-generation/runtime").expanduser()


def _ensure_runtime_importable() -> None:
    """Make ``automation.skill_generation`` importable inside the gateway.

    The gateway injects ``PYTHONPATH=~/.hermes/interop_runtime`` whose
    ``automation`` is a regular package (has ``__init__.py``). Once the interop
    plugin imports it, that binding shadows our runtime: ``automation.__path__``
    never includes our dir, so ``automation.skill_generation`` is invisible and
    the hook fails with ``No module named 'automation.skill_generation'``. A
    plain ``sys.path.insert`` cannot fix an already-bound regular package, so we
    also merge our package dir into the bound ``automation.__path__``.
    """
    runtime = str(_RUNTIME)
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    bound = sys.modules.get("automation")
    pkg_path = getattr(bound, "__path__", None) if bound is not None else None
    if not isinstance(pkg_path, list):
        return
    merged = cast("list[str]", pkg_path)
    automation_dir = os.path.join(runtime, "automation")
    if automation_dir not in merged:
        merged.append(automation_dir)


_ensure_runtime_importable()

LOGGER = logging.getLogger("autophagy.skill_generation")
_CONFIG = Path("~/.hermes/interop/config.json").expanduser()
_OWNER_ID = re.compile(r'"owner_id"\s*:\s*"([^"]+)"')

if TYPE_CHECKING:
    from automation.skill_generation.service import AutoSkillService


class PluginContext(Protocol):
    def register_hook(self, name: str, callback: Callable[..., None]) -> None: ...


class InboundSource(Protocol):
    is_bot: bool
    user_id: str


class InboundEvent(Protocol):
    source: InboundSource
    text: str


def register(ctx: PluginContext) -> None:
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    LOGGER.warning("supervised skill-generation plugin registered")


def pre_gateway_dispatch(event: InboundEvent, gateway: None, session_store: None, **kwargs: str) -> None:
    del gateway, session_store, kwargs
    try:
        owner_id = _owner_id()
        source = event.source
        text = str(event.text).strip()
        if bool(source.is_bot) or str(source.user_id) != owner_id or not text:
            return None
        service = _service()
        rejected = service.audit_mounts()
        proposal = service.observe(text, datetime.now(UTC))
        if proposal is not None:
            LOGGER.warning("skill suggestion name=%s status=%s", proposal.name, proposal.status.value)
        if rejected:
            LOGGER.warning("generated skill bypass rejected count=%d", len(rejected))
    except (OSError, ValueError, AttributeError):
        LOGGER.exception("skill-generation observation failed")
    return None


def _owner_id() -> str:
    matched = _OWNER_ID.search(_CONFIG.read_text(encoding="utf-8"))
    if matched is None:
        raise ValueError("missing owner id")
    return matched.group(1)


def _service() -> "AutoSkillService":
    _ensure_runtime_importable()
    from automation.skill_generation.core import RepetitionDetector
    from automation.skill_generation.service import AutoSkillService, SkillGenerationPaths

    root = Path.home() / ".hermes" / "skill-generation"
    base = SkillGenerationPaths.from_root(root)
    paths = SkillGenerationPaths(base.root, base.observations, base.proposals, base.drafts, Path.home() / ".hermes" / "skills", base.registry)
    return AutoSkillService(paths, RepetitionDetector(), None)
