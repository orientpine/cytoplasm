"""no-agent cron wrappers must resolve their repo root through the runtime-root
order (release `current` first, then the resident mirror) — never a bare
/srv/autophagy-agents literal that pins them to the mutable checkout (DG-4 W3.B).

The wrappers insert their root onto sys.path BEFORE importing automation.*, so
they cannot import runtime_root.py first — they inline the same fallback list
by value. This inventory-style conformance test globs every cron wrapper and
asserts each one prefers `current` and none hardcodes only the mirror.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CRON_GLOBS = ("automation/*/cron/*.py",)
_RELEASE_CURRENT = "/srv/autophagy-agent-current"
_MIRROR = "/srv/autophagy-agents"


def _cron_wrappers() -> list[Path]:
    found: list[Path] = []
    for pattern in _CRON_GLOBS:
        found.extend(sorted(_REPO.glob(pattern)))
    return [p for p in found if p.name != "__init__.py"]


def test_there_are_cron_wrappers_to_check() -> None:
    # Guard against the glob silently matching nothing.
    assert _cron_wrappers(), "no cron wrappers found to check"


def test_every_mirror_using_cron_wrapper_prefers_release_current(tmp_path: Path) -> None:  # noqa: ARG001
    # A wrapper is a runtime-root consumer only if it resolves the repo root from
    # the resident mirror. Wrappers that import from their own ~/.hermes/*_runtime
    # package (e.g. rag_ingest) never touch the checkout and are out of scope.
    offenders: list[str] = []
    for wrapper in _cron_wrappers():
        text = wrapper.read_text(encoding="utf-8")
        if _MIRROR in text and _RELEASE_CURRENT not in text:
            offenders.append(f"{wrapper.relative_to(_REPO)}: resolves the mirror but not {_RELEASE_CURRENT}")
    assert not offenders, "mirror-using cron wrappers not migrated to release-current:\n" + "\n".join(offenders)


def test_no_cron_wrapper_pins_only_the_mirror(tmp_path: Path) -> None:  # noqa: ARG001
    # The mirror may still appear (as the fallback), but only alongside current.
    offenders: list[str] = []
    for wrapper in _cron_wrappers():
        text = wrapper.read_text(encoding="utf-8")
        if _MIRROR in text and _RELEASE_CURRENT not in text:
            offenders.append(str(wrapper.relative_to(_REPO)))
    assert not offenders, "cron wrappers still pin only the mirror:\n" + "\n".join(offenders)


def test_memory_curator_bootstrap_prefers_release_current() -> None:
    text = (_REPO / "automation" / "memory_curator" / "_bootstrap.py").read_text(encoding="utf-8")
    # current must come BEFORE the mirror in the candidate order.
    assert _RELEASE_CURRENT in text
    assert text.index(_RELEASE_CURRENT) < text.index(_MIRROR)
