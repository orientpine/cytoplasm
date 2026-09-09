"""The converger's installed package must be import-closed, not merely list-matched.

``tests/unit/test_install_converge_parity.py`` guards the same directory twice: it pins
the two installers against each other, and it asserts a hand-written set of modules.
Both defences are blind to the SAME failure — a module already in the set growing a NEW
import.  Neither installer places the newly imported module, so the two sets still agree
and the hand-written set still passes.

That is exactly what happened.  ``automation/git_tag_signature.py`` gained
``from .typing_compat import override`` on 2026-08-23 (4358e8a2) and nothing placed
``automation/typing_compat.py`` beside it.  From the moment a node's helper tree was
refreshed with that file, ``python3 -I <libdir>/automation/update_trust.py resolve-signed``
died at import with ``ModuleNotFoundError: No module named 'automation.typing_compat'``,
``converge_origin_main.sh`` exited ``SYNC-BLOCK: update trust rejected the remote target``,
and the reconciler swallowed that stderr and logged only ``mirror left-behind`` every two
minutes.  Measured 2026-09-08 on the primary node: production frozen at v1.6.1 while
v1.6.2 and v1.6.3 were signed, pushed, and unreachable.

This walks the actual import graph instead of comparing lists, so a future import added to
any installed module is caught by the same assertion rather than by a third hand-written
set that would drift the same way.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_PROVISIONER: Final = _REPO / "automation" / "provision-deploy-converge.sh"

#: ``install -m MODE -o root -g root "$SRC" "$HELPER_LIBDIR/DEST"`` — the same line shape
#: the parity test parses, so both tests read one source of truth on disk.
_INSTALL_LINE: Final = re.compile(
    r'install -m 0[0-7]{3} -o root -g root "[^"]+" "\$HELPER_LIBDIR/([^"]+)"'
)

#: ``from .name import``, ``from automation.name import``, ``import automation.name``.
#: Only single-segment names matter here: the converger's package is flat, and a dotted
#: sub-package (``automation.install.plan``) resolves to a directory that no converge.d
#: file may reach anyway.
_IMPORT: Final = re.compile(
    r"^\s*(?:from\s+\.(\w+)\s+import|from\s+automation\.(\w+)\s+import|import\s+automation\.(\w+))",
    re.MULTILINE,
)


def _placed_package_modules() -> frozenset[str]:
    text = _PROVISIONER.read_text(encoding="utf-8")
    return frozenset(
        destination
        for destination in _INSTALL_LINE.findall(text)
        if destination.startswith("automation/") and destination.endswith(".py")
    )


def _imported_package_modules(destination: str) -> frozenset[str]:
    source = (_REPO / destination).read_text(encoding="utf-8")
    names = (next(name for name in match.groups() if name) for match in _IMPORT.finditer(source))
    return frozenset(
        f"automation/{name}.py"
        for name in names
        if (_REPO / "automation" / f"{name}.py").is_file()
    )


def test_the_installed_converger_package_reaches_every_module_it_imports() -> None:
    # Given: the package files the provisioner installs beside the privileged converger.
    placed = _placed_package_modules()
    assert placed, "provisioner install lines no longer parse"

    # When: their import graph is walked transitively.
    missing: list[str] = []
    seen: set[str] = set()
    pending = list(placed)
    while pending:
        destination = pending.pop()
        if destination in seen:
            continue
        seen.add(destination)
        for needed in sorted(_imported_package_modules(destination)):
            if needed not in placed:
                missing.append(f"{destination} imports {needed}")
            elif needed not in seen:
                pending.append(needed)

    # Then: nothing it imports is left behind — a partial package is not a degraded
    # install but a node that can never install another release.
    assert not missing, (
        "the privileged converger imports modules the installers do not place; "
        "add each to automation/provision-deploy-converge.sh AND "
        "automation/install/libexec_assets.py::CONVERGE_HELPERS:\n" + "\n".join(sorted(missing))
    )
