from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Final

DEFAULT_PEERS_CONFIG: Final = Path("~/.hermes/interop/peers.yaml").expanduser()
DEFAULT_REPO_ROOT: Final = Path("/srv/autophagy-agent-current")
DEFAULT_OWNER_AGENT_ID: Final = "agent-cha"

# Explicit negotiate-intent cues. When present, a peer request is coordination
# even if it also carries an exact single time (the owner is asking whether the
# peer is free, not fixing their own solo slot).
COORDINATION_CUES: Final = (
    "조율",
    "가능한 시간",
    "가능한지",
    "가능 여부",
    "가능해",
    "물어봐",
    "협의",
    "조율해",
    "맞춰",
    "negotiate",
    "availability",
    "coordinate",
)

class PeerRegistryError(RuntimeError):
    pass


def named_peer_ids(request: str) -> tuple[str, ...]:
    path = _peers_config_path()
    return tuple(
        agent_id
        for agent_id in _registered_agent_ids(path)
        if agent_id != _owner_agent_id() and _contains_agent_id(request, agent_id)
    )


def _peers_config_path() -> Path:
    return Path(os.environ.get("CALENDAR_PEERS_CONFIG", str(DEFAULT_PEERS_CONFIG))).expanduser()


def _owner_agent_id() -> str:
    return os.environ.get("CALENDAR_OWNER_AGENT_ID", DEFAULT_OWNER_AGENT_ID)


def _registered_agent_ids(path: Path) -> tuple[str, ...]:
    repo_root = Path(os.environ.get("AUTOPHAGY_REPO_ROOT", str(DEFAULT_REPO_ROOT))).expanduser()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from automation.report_hub.registry import RegistryError, load_registry
    except ModuleNotFoundError as error:
        raise PeerRegistryError(f"피어 레지스트리 로더를 찾을 수 없습니다: {repo_root}") from error
    try:
        registry = load_registry(path)
    except RegistryError as error:
        raise PeerRegistryError(str(error)) from error
    return tuple(peer.agent_id for peer in registry.peers)


def _contains_agent_id(request: str, agent_id: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(agent_id)}(?![A-Za-z0-9_-])"
    return re.search(pattern, request) is not None


def has_coordination_cue(request: str) -> bool:
    """True when the free text explicitly asks to negotiate with a peer."""
    return any(cue in request for cue in COORDINATION_CUES)


def resolves_to_exact_time(request: str, now: datetime) -> bool:
    """True when ``request`` deterministically fixes a single start time.

    Delegates to the calendar parser: it returns a concrete event only when a
    day AND a clock time are both present and unambiguous. Any AmbiguousTime /
    ParseRejected (vague window, no time, past time) means NOT an exact slot.
    """
    calendar_core = import_module("calendar_core")
    try:
        calendar_core.parse_request(request, now)
    except (calendar_core.AmbiguousTime, calendar_core.ParseRejected):
        return False
    return True


def classify_meeting_request(
    request: str,
    now: datetime,
    *,
    peer_flag: str | None = None,
    explicit_exact_slot: bool = False,
) -> str:
    """Deterministically route one meeting request to exactly one skill.

    Returns ``"calendar"`` (solo event, gate via calendar draft),
    ``"coordination"`` (agent-to-agent negotiation) or ``"clarify"`` (ambiguous
    — fail-closed: ask the owner, execute no external effect).

    Decision rule (peer name in free text OR an explicit ``--peer`` flag both
    count as "a peer is involved"):

    * no peer + exact time            -> calendar
    * no peer + not exact              -> clarify
    * peer + exact time + no cue       -> calendar (peer name is a title token;
                                          the owner fixed their own slot)
    * peer + exact time + coord cue    -> clarify (conflicting signals; the
                                          negotiator cannot honour a fixed slot
                                          without exact-slot-confirm semantics)
    * peer + not exact + coord cue     -> coordination
    * peer + not exact + explicit flag -> coordination
    * peer + not exact + no cue/flag   -> clarify (a bare peer name is not
                                          enough to start a negotiation — the
                                          incident that motivated this guard)
    """
    has_peer = bool(peer_flag) or bool(named_peer_ids(request))
    exact = explicit_exact_slot or resolves_to_exact_time(request, now)
    cue = has_coordination_cue(request)
    if not has_peer:
        return "calendar" if exact else "clarify"
    if exact:
        return "clarify" if cue else "calendar"
    if cue or peer_flag:
        return "coordination"
    return "clarify"
