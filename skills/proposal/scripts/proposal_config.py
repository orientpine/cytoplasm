"""Fail-closed configuration and checkout preflight for proposal generation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Mapping


class ConfigError(ValueError):
    """Environment configuration is invalid or incomplete."""


@dataclass(frozen=True, slots=True)
class ProposalConfig:
    docbot_root: Path
    docbot_pin: str
    profile: str
    image_model: str
    image_monthly_cap_usd: float
    refine_pin: str
    drive_root: str
    seed_hwpx_relpath: str = "resource/(주제1) R&D 연구계획서 양식.hwpx"
    seed_sha256: str | None = None
    state_root: Path = Path("~/.hermes/proposal")


@dataclass(frozen=True, slots=True)
class PreflightReport:
    head_sha: str | None
    pin: str
    clean: bool
    seed_sha256: str | None
    ok: bool
    reasons: tuple[str, ...]


def load_config(env: Mapping[str, str] | None = None) -> ProposalConfig:
    values = os.environ if env is None else env
    pin = values.get("PROPOSAL_DOCBOT_PIN", "")
    if re.fullmatch(r"[0-9a-f]{40}", pin) is None:
        raise ConfigError("PROPOSAL_DOCBOT_PIN must be 40 lowercase hexadecimal characters")
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
        Path(values.get("PROPOSAL_DOCBOT_ROOT", "~/kimm-docbot")).expanduser(), pin, profile,
        values.get("PROPOSAL_IMAGE_MODEL", "gpt-image-2"), cap,
        values.get("PROPOSAL_REFINE_PIN", "177e64539cd8b4faf41a2d8c6d187c33d57f79f4"),
        values.get("PROPOSAL_DRIVE_ROOT", "autophagy"),
        seed_sha256=values.get("PROPOSAL_SEED_SHA256"),
        state_root=Path(values.get("PROPOSAL_STATE_ROOT", "~/.hermes/proposal")).expanduser(),
    )


def preflight(cfg: ProposalConfig) -> PreflightReport:
    root = cfg.docbot_root
    reasons: list[str] = []
    head: str | None = None
    clean = False
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            head = result.stdout.strip()
            status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, check=False)
            clean = status.returncode == 0 and not status.stdout
        else:
            reasons.append("not-a-git-checkout")
    except OSError:
        reasons.append("not-a-git-checkout")
    if head is not None and head != cfg.docbot_pin:
        reasons.append("pin-mismatch")
    if head is not None and not clean:
        reasons.append("dirty-worktree")
    seed = root / cfg.seed_hwpx_relpath
    actual: str | None = None
    if seed.is_file():
        actual = hashlib.sha256(seed.read_bytes()).hexdigest()
        if cfg.seed_sha256 is not None and actual != cfg.seed_sha256:
            reasons.append("seed-sha-mismatch")
    else:
        reasons.append("seed-missing")
    return PreflightReport(head, cfg.docbot_pin, clean, actual, not reasons, tuple(reasons))


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
        print(f"ENGINE-PIN-BLOCK: {', '.join(report.reasons)}", file=sys.stderr)
        raise SystemExit(4)


if __name__ == "__main__":
    main(sys.argv[1:])
