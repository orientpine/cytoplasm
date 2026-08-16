#!/usr/bin/env python3
"""No-agent cron: flip patent-export manifest state from owner reactions.

Dumb state-flipper ONLY. It polls owner ✅/⛔ reactions on the pending export
approval messages and transitions the manifest (PENDING→APPROVED on ✅;
PENDING/APPROVED→CANCELLED on ⛔ — the latter revokes a not-yet-executed
approval). It NEVER reads the draft, encrypts, or uploads: the irreversible
Drive upload happens solely in the foreground manual ``export-execute``.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Final

_LIVE_SCRIPTS: Final = "/srv/autophagy-skills/live/patent-prep/scripts"
_SCRIPTS = Path(os.environ.get("PATENT_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
_SKILL_ROOT = _SCRIPTS.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts import patent_export_gate as gate  # noqa: E402
from scripts import patent_export_manifest as manifest  # noqa: E402

_LONG_DIGITS = re.compile(r"\d{5,}")


def _redact(text: str) -> str:
    """Keep long digit runs (ids) out of the cron's stderr breadcrumb."""
    return _LONG_DIGITS.sub("[MASKED-NUM]", text)[:300]


def _process(entry: manifest.Manifest, now_ts: int) -> None:
    """Read and apply one reaction under the manifest's existing exact-binding lock."""
    try:
        with manifest.lock(entry.slug):
            current = manifest.load_manifest(entry.slug)
            expected = (entry.slug, entry.nonce, entry.plaintext_sha256)
            if (current.slug, current.nonce, current.plaintext_sha256) != expected:
                return
            try:
                reaction = gate.reaction_state(current)
            except gate.ExportGateError:
                return
            if reaction is None:
                return
            refreshed = manifest.load_manifest(entry.slug)
            if (
                refreshed.slug,
                refreshed.nonce,
                refreshed.plaintext_sha256,
            ) != (current.slug, current.nonce, current.plaintext_sha256):
                return
            if reaction == gate.CANCEL_EMOJI:
                manifest.transition(
                    current.slug,
                    allowed_from={manifest.State.PENDING, manifest.State.APPROVED},
                    to=manifest.State.CANCELLED,
                )
            elif reaction == gate.APPROVE_EMOJI:
                manifest.transition(
                    current.slug,
                    allowed_from={manifest.State.PENDING},
                    to=manifest.State.APPROVED,
                    approval_ts=now_ts,
                )
    except manifest.ManifestError:
        # Locked by a concurrent export-execute, or state already advanced.
        return


def run_once(now_ts: int) -> None:
    """Reflect owner reactions into every active manifest; never touch a draft."""
    for entry in manifest.list_active():
        _process(entry, now_ts)


def main() -> int:
    """Single cron tick with a redacted final error boundary."""
    try:
        run_once(manifest.now_ts())
    except Exception as error:  # noqa: BLE001 — final cron alert boundary.
        print(f"patent-export-watch error: {_redact(str(error))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
