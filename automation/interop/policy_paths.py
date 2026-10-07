"""Where the external-effect gate reads its rule file from.

Order: ``EXTERNAL_EFFECT_DENYLIST_PATH`` when set, else the
``configs/external-effect-tools.yaml`` of the generation this module runs from
(a root-owned read-only release tree, so the gated agent cannot edit its own
rules), else the legacy account-home copy. The home answer is returned even
when the file is absent: the gate then fails closed on a missing rule file.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

ENV_OVERRIDE: Final = "EXTERNAL_EFFECT_DENYLIST_PATH"
RULES_NAME: Final = "external-effect-tools.yaml"


def denylist_path(env: Mapping[str, str] = os.environ) -> Path:
    override = env.get(ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()
    try:
        release = Path(__file__).resolve().parents[2] / "configs" / RULES_NAME
        if release.is_file():
            return release
    except OSError:
        pass
    home = Path(env["HOME"]) if env.get("HOME") else Path.home()
    return home / ".hermes" / "interop" / RULES_NAME
