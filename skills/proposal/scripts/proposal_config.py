"""Fail-closed configuration and in-tree engine preflight for proposal generation."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

ENGINE_ROOT: Final = Path(__file__).resolve().parents[1] / "engine"
SEED_RELPATH: Final = "resource/(주제1) R&D 연구계획서 양식.hwpx"


class ConfigError(ValueError):
    """Environment configuration is invalid or incomplete."""


@dataclass(frozen=True, slots=True)
class ProposalConfig:
    profile: str
    image_model: str
    image_monthly_cap_usd: float
    refine_pin: str
    drive_root: str
    state_root: Path = Path("~/.hermes/proposal")


@dataclass(frozen=True, slots=True)
class PreflightReport:
    engine_root: str
    seed_sha256: str | None
    ok: bool
    reasons: tuple[str, ...]


def load_config(env: Mapping[str, str] | None = None) -> ProposalConfig:
    values = os.environ if env is None else env
    profile = values.get("PROPOSAL_PROFILE", "10-page")
    if profile not in {"30-page", "10-page"}:
        raise ConfigError("PROPOSAL_PROFILE must be 30-page or 10-page")
    try:
        cap = float(values.get("PROPOSAL_IMAGE_MONTHLY_CAP_USD", "10"))
    except ValueError as error:
        raise ConfigError("PROPOSAL_IMAGE_MONTHLY_CAP_USD must be a non-negative float") from error
    if cap < 0:
        raise ConfigError("PROPOSAL_IMAGE_MONTHLY_CAP_USD must be a non-negative float")
    return ProposalConfig(
        profile,
        values.get("PROPOSAL_IMAGE_MODEL", "gpt-image-2"),
        cap,
        values.get("PROPOSAL_REFINE_PIN", "177e64539cd8b4faf41a2d8c6d187c33d57f79f4"),
        values.get("PROPOSAL_DRIVE_ROOT", "autophagy"),
        state_root=Path(values.get("PROPOSAL_STATE_ROOT", "~/.hermes/proposal")).expanduser(),
    )


def preflight(cfg: ProposalConfig | None = None) -> PreflightReport:
    """Check the render engine this repository ships, not another checkout's HEAD."""
    _ = cfg
    reasons: list[str] = []
    if not ENGINE_ROOT.is_dir():
        reasons.append("engine-missing")
    elif not any(ENGINE_ROOT.rglob("*.py")):
        reasons.append("engine-sources-missing")

    seed = ENGINE_ROOT / SEED_RELPATH
    seed_sha256: str | None = None
    if seed.is_file():
        seed_sha256 = hashlib.sha256(seed.read_bytes()).hexdigest()
    else:
        reasons.append("seed-missing")

    return PreflightReport(str(ENGINE_ROOT), seed_sha256, not reasons, tuple(reasons))


def main(argv: list[str]) -> None:
    if "--preflight" not in argv:
        return
    try:
        report = preflight(load_config())
    except ConfigError as error:
        print(f"CONFIG-ERROR: {error}", file=sys.stderr)
        raise SystemExit(4) from error
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    if not report.ok:
        print(f"ENGINE-BLOCK: {', '.join(report.reasons)}", file=sys.stderr)
        raise SystemExit(4)


if __name__ == "__main__":
    main(sys.argv[1:])
