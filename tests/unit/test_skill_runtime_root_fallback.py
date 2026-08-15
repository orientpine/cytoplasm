"""Mounted-skill code-root fallbacks must prefer the release `current` before the
resident mirror (DG-4 W3.C).

A mounted skill resolves where the shared ``automation.*`` / repo code lives in
order to import it. Those resolvers hardcoded ``/srv/autophagy-agents`` (the
mutable mirror). They cannot import automation.runtime_root (they run BEFORE the
repo root is on sys.path), so the ``/srv/autophagy-agent-current`` preference is
inlined by value. This inventory test asserts every skill code-root resolver
references release-current, and a drift guard pins the by-value literal to the
canonical one in runtime_root.py.

NOT in scope: approval-log / config-seed paths under the checkout
(``*_gate.py``, ``triage_mode.py``, ``calendar_routing.py``) — those are runtime
state locations, not code roots, and are a separate concern.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_RELEASE_CURRENT = "/srv/autophagy-agent-current"
_MIRROR = "/srv/autophagy-agents"

# The code-root resolvers migrated by W3.C (path, the function that resolves the root).
_CODE_ROOT_RESOLVERS = (
    "skills/mail/scripts/mail_preflight.py",
    "skills/calendar/scripts/calendar_preflight.py",
    "skills/todo/scripts/todo_preflight.py",
    "skills/doctype/scripts/doctype_store.py",
    "skills/prompt/scripts/prompt_store.py",
    "skills/wiki/scripts/wiki_confirm_reaction_watch.py",
)


def test_all_code_root_resolvers_exist() -> None:
    for rel in _CODE_ROOT_RESOLVERS:
        assert (_REPO / rel).is_file(), rel


def test_every_skill_code_root_resolver_prefers_release_current() -> None:
    offenders: list[str] = []
    for rel in _CODE_ROOT_RESOLVERS:
        text = (_REPO / rel).read_text(encoding="utf-8")
        if _RELEASE_CURRENT not in text:
            offenders.append(f"{rel}: does not reference {_RELEASE_CURRENT}")
    assert not offenders, "skill code-root resolvers not migrated:\n" + "\n".join(offenders)


def test_release_current_precedes_the_mirror_in_each_resolver() -> None:
    offenders: list[str] = []
    for rel in _CODE_ROOT_RESOLVERS:
        text = (_REPO / rel).read_text(encoding="utf-8")
        if _RELEASE_CURRENT in text and _MIRROR in text:
            if text.index(_RELEASE_CURRENT) > text.index(_MIRROR):
                offenders.append(f"{rel}: mirror appears before current")
    assert not offenders, "current must be tried before the mirror:\n" + "\n".join(offenders)


def test_by_value_literal_matches_runtime_root_module() -> None:
    # Drift guard: the inlined literal must equal runtime_root.py's canonical const.
    rr = (_REPO / "automation" / "runtime_root.py").read_text(encoding="utf-8")
    assert f'"{_RELEASE_CURRENT}"' in rr
