"""Report whether the vendored Hermes gateway is actually carrying our patches.

WHY: on 2026-08-16 a `hermes update` replaced the vendored source and left its autostash
unrestored, so all three `hermes_compat` patches were off production for two days. One of
them (`busy-path-pre-gateway-dispatch`) is what makes busy-path messages reach the
skill-generation observation and the meeting-gate fail-closed veto, so a gate-adjacent
hook was simply off. Nothing said so: the unit stayed `active/running`, and the manifest's
own note claimed `automation/healthcheck.sh` surfaced a missing patch — that check did not
exist. A false assurance is worse than none, because it is read during an incident.

This module only *detects*. Re-applying the patches and restarting the gateway pair are
external effects that belong to the owner ledger (「게이트웨이 재시동 규칙」: agent and peer
restart together), so nothing here runs `hermes`, ssh, or systemctl.

Exit codes: 0 every patch applied · 1 at least one applied-check failed · 2 at least one
patch could not be judged. Unknown outranks missing on purpose — 부재는 PASS 가 아니고,
"읽을 수 없었다" 를 "빠졌다" 로 보고하면 원인 규명이 엉뚱한 곳에서 시작된다.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_MANIFEST: Final = Path(__file__).with_name("manifest.json")
DEFAULT_INSTALL_ROOT: Final = Path.home() / ".hermes" / "hermes-agent"

APPLIED: Final = "PATCHED"
MISSING: Final = "MISSING"
UNKNOWN: Final = "UNKNOWN"


class PatchStateError(RuntimeError):
    """The manifest itself could not be read — never degrade this to 'no patches'."""


@dataclass(frozen=True, slots=True)
class Patch:
    patch_id: str
    target: str
    marker: str
    applier: str


@dataclass(frozen=True, slots=True)
class Probe:
    patch: Patch
    state: str
    detail: str


def load_patches(manifest_path: Path = DEFAULT_MANIFEST) -> tuple[Patch, ...]:
    """Read the patch inventory; an unreadable manifest raises rather than returns ()."""
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PatchStateError(f"manifest unreadable: {manifest_path}") from error
    entries = payload.get("patches") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise PatchStateError(f"manifest carries no patches: {manifest_path}")
    patches: list[Patch] = []
    for entry in entries:
        try:
            patches.append(
                Patch(
                    patch_id=str(entry["id"]),
                    target=str(entry["target"]),
                    marker=str(entry["marker"]),
                    applier=str(entry["applier"]),
                )
            )
        except (KeyError, TypeError) as error:
            raise PatchStateError(f"manifest entry is incomplete: {entry!r}") from error
    return tuple(patches)


def probe_patch(install_root: Path, patch: Patch) -> Probe:
    """Look for the patch marker in its target file, in the install we were handed."""
    target = install_root / patch.target
    try:
        source = target.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return Probe(patch, UNKNOWN, f"target unreadable ({error.__class__.__name__}): {target}")
    if patch.marker in source:
        return Probe(patch, APPLIED, f"marker {patch.marker} present in {patch.target}")
    return Probe(patch, MISSING, f"marker {patch.marker} absent from {patch.target}")


def probe_patches(
    install_root: Path, manifest_path: Path = DEFAULT_MANIFEST
) -> tuple[Probe, ...]:
    return tuple(probe_patch(install_root, patch) for patch in load_patches(manifest_path))


def verdict(probes: Sequence[Probe]) -> int:
    """Unknown (2) outranks missing (1); an empty probe set is never a pass."""
    if not probes:
        return 2
    if any(probe.state == UNKNOWN for probe in probes):
        return 2
    if any(probe.state == MISSING for probe in probes):
        return 1
    return 0


def render(probes: Sequence[Probe], install_root: Path) -> str:
    lines = [f"hermes-compat patch state @ {install_root}"]
    lines += [f"  {probe.state:8s} {probe.patch.patch_id} — {probe.detail}" for probe in probes]
    missing = [probe.patch for probe in probes if probe.state == MISSING]
    if missing:
        lines.append(
            "  re-apply is an owner action (게이트웨이 재시동 규칙: agent+peer together): "
            + ", ".join(sorted({patch.applier for patch in missing}))
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--install-root", type=Path, default=DEFAULT_INSTALL_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    arguments = parser.parse_args(argv)
    try:
        probes = probe_patches(arguments.install_root, arguments.manifest)
    except PatchStateError as error:
        print(f"{UNKNOWN}  {error}")
        return 2
    print(render(probes, arguments.install_root))
    return verdict(probes)


if __name__ == "__main__":
    raise SystemExit(main())
