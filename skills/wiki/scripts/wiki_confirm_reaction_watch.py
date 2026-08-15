#!/usr/bin/env python3
"""Poll pending wiki confirmation reactions through the single wiki gate resolver."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

ENV_SECRETS = Path.home() / ".env.secrets"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _load_env_secrets(path: Path = ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _install_repo_path() -> None:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        repo = Path(override).expanduser()
    else:
        current = Path("/srv/autophagy-agent-current")  # release runtime (DG-4)
        repo = current if current.is_dir() else Path("/srv/autophagy-agents")
    scripts = Path(
        os.environ.get("WIKI_SCRIPTS", str(repo / "skills" / "wiki" / "scripts"))
    ).expanduser()
    for path in (repo, scripts):
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))[:300]


def run_once() -> int:
    _load_env_secrets()
    _install_repo_path()
    import wiki_gate  # noqa: PLC0415

    failures = 0
    for draft in wiki_gate.list_drafts():
        if draft.get("status") != "pending":
            continue
        if not draft.get("confirm_message_id"):
            continue
        try:
            wiki_gate.resolve_reaction(draft)
        except wiki_gate.GateError as error:
            if error.exit_code != 1:
                failures += 1
                print(f"wiki-confirm-reaction-watch error: {_redact(str(error))}", file=sys.stderr)
        except (HTTPError, URLError, OSError) as error:
            failures += 1
            print(f"wiki-confirm-reaction-watch error: {_redact(str(error))}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    try:
        return run_once()
    except Exception as error:  # noqa: BLE001,BROAD_EXCEPT_OK — final cron alert boundary.
        print(f"wiki-confirm-reaction-watch error: {_redact(str(error))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
