"""gateway-preflight: import the new interop shim the way the gateway will, before deploying it.

`automation/interop/deploy.sh` runs this text with the gateway interpreter
(`~/.hermes/hermes-agent/venv/bin/python -B -c <this file>`) on each account, with the new
shim on stdin. It builds a throwaway import farm by the same rule as
`pin_import_root.sh` (entry symlinks of `<generation>/automation/*` minus `hermes_compat` and
`__pycache__`), then a child interpreter with `PYTHONPATH=<farm>` replays the gateway load order:
the compat carrier boot (when present), the skill-generation plugin (when present), the shim,
and the modules the shim and plugin body import. The import must resolve `automation` to
`<generation>/automation` - resolving the file first, because the farm directory itself is never
the generation. Exit 0 only when every step succeeds; nothing outside a temporary directory is
written. Python 3.11, stdlib only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

REQUIRED: Final = (
    "automation/__init__.py",
    "automation/interop/external_effect_gate.py",
    "automation/interop/hermes_plugin/__init__.py",
    "automation/gateway_generation.py",
    "automation/interop/policy_paths.py",
    "automation/interop/plugin_shim/__init__.py",
)
EXCLUDED: Final = frozenset({"hermes_compat", "__pycache__"})

_CHILD: Final = r"""
import importlib.util, os, sys
generation, shim = sys.argv[1], sys.argv[2]
home = os.path.expanduser("~")

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

boot = os.path.join(home, ".hermes", "hermes-compat", "hermes_compat_boot.py")
if os.path.isfile(boot):
    load("hermes_compat_boot", boot)
skill_generation = os.path.join(home, ".hermes", "plugins", "05-skill-generation", "__init__.py")
if os.path.isfile(skill_generation):
    load("preflight_skill_generation_plugin", skill_generation)
load("preflight_interop_protocol_plugin", shim)
import automation
import automation.gateway_generation
import automation.group_roster
import automation.interop.hermes_plugin
import automation.owner_notice
want = os.path.join(generation, "automation")
have = os.path.dirname(os.path.realpath(automation.__file__))
if have != want:
    sys.exit(f"PREFLIGHT-FAIL automation resolves to {have}, not {want}")
for module in (automation.interop.hermes_plugin, automation.gateway_generation):
    if not os.path.realpath(module.__file__).startswith(want + os.sep):
        sys.exit(f"PREFLIGHT-FAIL {module.__name__} resolves outside {want}")
print("PREFLIGHT-OK")
"""


def resolve_root(env: Mapping[str, str]) -> Path:
    """Same order as pin_import_root.sh: override, release pointer if present, else mirror."""
    override = env.get("AUTOPHAGY_RUNTIME_ROOT")
    if override:
        return Path(override)
    pointer = Path(env.get("PIN_RELEASE_POINTER") or "/srv/autophagy-agent-current")
    if os.path.lexists(pointer) and pointer.exists():
        return pointer
    return Path(env.get("PIN_MIRROR_ROOT") or "/srv/autophagy-agents")


def missing_closure(generation: Path) -> list[str]:
    return [relative for relative in REQUIRED if not (generation / relative).is_file()]


def build_farm(generation: Path, farm: Path) -> None:
    package = farm / "automation"
    package.mkdir(parents=True)
    for entry in sorted((generation / "automation").iterdir()):
        if entry.name in EXCLUDED or entry.name.startswith("."):
            continue
        (package / entry.name).symlink_to(generation / "automation" / entry.name)


def main() -> int:
    generation = Path(os.path.realpath(resolve_root(os.environ)))
    missing = missing_closure(generation)
    if missing:
        print(f"PREFLIGHT-FAIL missing {generation / missing[0]}", file=sys.stderr)
        return 1
    shim = sys.stdin.buffer.read()
    if not shim:
        print("PREFLIGHT-FAIL no shim on stdin", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="interop-preflight.") as temporary:
        farm = Path(temporary) / "farm"
        build_farm(generation, farm)
        shim_path = Path(temporary) / "shim" / "__init__.py"
        shim_path.parent.mkdir()
        _ = shim_path.write_bytes(shim)
        env = dict(os.environ, PYTHONPATH=str(farm))
        child = subprocess.run(
            [sys.executable, "-B", "-c", _CHILD, str(generation), str(shim_path)],
            env=env, stdin=subprocess.DEVNULL, check=False,
        )
    return child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
