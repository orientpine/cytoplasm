"""Read-only external-resource preflight for proposal generation stages.

Hermes-dependent stages run on provisioned nodes; workstation tests use fake transports and request
``--require hermes`` only when validating node readiness. The owner provisions image credentials in
``~/.env.secrets`` under the configured environment name; wrappers spawning image clients must pass
that credential explicitly in the child environment rather than relying on implicit inheritance.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, Literal, TypeAlias


CheckStatus: TypeAlias = Literal["present", "absent", "mismatch"]
REQUIRED_BINARIES: Final = (
    "gws",
    "soffice",
    "pdfinfo",
    "pdfimages",
    "pdftotext",
    "uv",
    "gh",
)
_REFINE_PIN: Final = "177e64539cd8b4faf41a2d8c6d187c33d57f79f4"
_SEED_RELPATH: Final = "resource/(주제1) R&D 연구계획서 양식.hwpx"


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _expand_home(value: str, values: Mapping[str, str]) -> Path:
    """Expand a leading home shorthand using the supplied environment mapping."""
    home = values.get("HOME")
    if home and (value == "~" or value.startswith("~/")):
        value = home + value[1:]
    return Path(value).expanduser()


def _which(name: str, values: Mapping[str, str]) -> str | None:
    """Resolve a binary against the supplied PATH, or the process PATH as fallback."""
    path = values.get("PATH")
    if path is None:
        path = os.environ.get("PATH")
    try:
        return shutil.which(name, path=path)
    except TypeError:
        # Supports existing test doubles that predate shutil.which's path argument.
        return shutil.which(name)


def _picture_carrier(root: Path, configured: str | None) -> Path | None:
    if configured:
        candidate = root / configured
        return candidate if candidate.is_file() else None
    fixtures = root / "tests" / "fixtures"
    if not fixtures.is_dir():
        return None
    return next(
        (
            path
            for path in fixtures.rglob("*")
            if path.is_file()
            and "picture" in path.name.lower()
            and "carrier" in path.name.lower()
        ),
        None,
    )


def collect_report(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Inspect resources without creating, cloning, or modifying any runtime state."""
    values = os.environ if env is None else env
    docbot = _expand_home(values.get("PROPOSAL_DOCBOT_ROOT", "~/kimm-docbot"), values)
    refine = _expand_home(values.get("PROPOSAL_REFINE_ROOT", "~/.hermes/im-not-ai"), values)
    refine_pin = values.get("PROPOSAL_REFINE_PIN", _REFINE_PIN)
    key_name = values.get("PROPOSAL_IMAGE_API_KEY_ENV", "OPENAI_API_KEY")

    checks: dict[str, CheckStatus] = {}
    for binary in (*REQUIRED_BINARIES, "hermes", "codex"):
        checks[binary] = "present" if _which(binary, values) is not None else "absent"
    checks["docbot-root"] = "present" if docbot.is_dir() else "absent"
    seed = docbot / values.get("PROPOSAL_SEED_HWPX_RELPATH", _SEED_RELPATH)
    checks["seed-hwpx"] = "present" if seed.is_file() else "absent"
    carrier = _picture_carrier(docbot, values.get("PROPOSAL_PICTURE_CARRIER"))
    checks["picture-carrier"] = "present" if carrier is not None else "absent"

    head = _git_head(refine) if refine.is_dir() else None
    if head is None:
        checks["refine-checkout"] = "absent"
    else:
        checks["refine-checkout"] = "present" if head == refine_pin else "mismatch"
    checks["image-api-key"] = "present" if values.get(key_name, "").strip() else "absent"

    codex_home = _expand_home(values.get("CODEX_HOME", "~/.codex"), values)
    checks["codex-auth"] = "present" if (codex_home / "auth.json").is_file() else "absent"

    configured_chrome = values.get("PROPOSAL_PREVIEW_CHROME")
    if configured_chrome:
        chrome = _expand_home(configured_chrome, values)
        chrome_available = chrome.is_file() and os.access(chrome, os.X_OK)
    else:
        chrome_available = any(
            _which(name, values) is not None
            for name in ("google-chrome", "chromium", "chromium-browser", "chrome")
        )
    checks["chrome"] = "present" if chrome_available else "absent"

    codex_image_transport = (
        values.get("PROPOSAL_IMAGE_TRANSPORT", "live") == "codex"
        and checks["codex"] == "present"
        and checks["codex-auth"] == "present"
    )
    stages = {
        "images": (
            "present"
            if checks["picture-carrier"] == "present"
            and (checks["image-api-key"] == "present" or codex_image_transport)
            else "blocked"
        ),
        "refine": "present" if checks["refine-checkout"] == "present" else "blocked",
        "visual-review": "present" if checks["chrome"] == "present" else "blocked",
    }
    return {"checks": checks, "stages": stages}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proposal-preflight")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--stage")
    return parser


def _print_report(report: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return
    checks = report["checks"]
    stages = report["stages"]
    assert isinstance(checks, dict)
    assert isinstance(stages, dict)
    for name, status in checks.items():
        print(f"{name}: {status}")
    for name, status in stages.items():
        print(f"stage:{name}: {status}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = collect_report()
    _print_report(report, args.json)
    checks = report["checks"]
    stages = report["stages"]
    assert isinstance(checks, dict)
    assert isinstance(stages, dict)

    blocked = [name for name in REQUIRED_BINARIES if checks[name] != "present"]
    for required in args.require:
        status = checks.get(required)
        if status != "present" and required not in blocked:
            blocked.append(required)
    if args.stage is not None and stages.get(args.stage) != "present":
        blocked.append(args.stage)
    if blocked:
        print(f"PREFLIGHT-BLOCK: {', '.join(blocked)}", file=sys.stderr)
        raise SystemExit(4)
    if stages["images"] != "present":
        print("PREFLIGHT-WARN: images blocked; image API key is absent", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
