#!/usr/bin/env python3
"""Where this skill lives and what it may touch: settings, paths, credentials, Drive.

Split out of the CLI when it crossed the 250-line ceiling. It is a real seam, not a
file-size trick: everything here answers "what does the environment say" or "what may
leave this machine", and none of it knows what a transcript is.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence
from typing import Final

import stt_polish

_LIVE_MEETING_CLI: Final = Path("/srv/autophagy-skills/live/meeting/scripts/meeting_cli.py")
_RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
_MIRROR_CHECKOUT: Final = Path("/srv/autophagy-agents")
_SECRET_KEYS: Final = (
    "OPENAI_API_KEY",
    "LITELLM_AGENT_KEY",
    "LITELLM_BASE_URL",
    "LITELLM_MASTER_KEY",
    "DISCORD_BOT_TOKEN",
)


def read_secrets() -> dict[str, str]:
    """Parse ``~/.env.secrets`` — no-agent cron never injects it (설계규약 (b))."""
    path = Path.home() / ".env.secrets"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def setting(name: str, default: str = "") -> str:
    """Environment first, then ``~/.env.secrets`` — the cron path has only the latter."""
    return os.environ.get(name) or read_secrets().get(name, default)


GLOSSARY_FILE: Final = "용어집.txt"
_DATE_TOKEN: Final = re.compile(r"^\d{4}-?\d{2}-?\d{2}$")


def project_of(name: str) -> str:
    """The project a recording belongs to, read from the name the owner gave it.

    The first `_`-separated token that is not a date. `20260825_해양고신뢰성` and
    `해양고신뢰성_킥오프` both name the same project; a name that is only a date names
    none, and then nothing is filed under a project.
    """
    for token in str(name).split("_"):
        cleaned = token.strip()
        if cleaned and not _DATE_TOKEN.fullmatch(cleaned):
            return cleaned
    return ""


def _repo(module: str):
    root = str(runtime_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    return __import__(f"automation.{module}", fromlist=["_"])


def project_glossary(project: str, *, client: object | None = None) -> tuple[tuple[str, str], ...]:
    """The glossary the owner keeps in that project's Drive folder, if there is one.

    Best-effort by design: a project without a glossary, or a Drive that will not
    answer, must not stop a transcription — it only means no correction this time.
    """
    if not project:
        return ()
    # Building a client from the environment means talking to Drive, so it needs the same
    # opt-in every other Drive touch needs. Without this a unit test that merely runs the
    # CLI reaches the owner's real Drive — and one did, creating a folder named after a
    # test fixture. An injected client is the test seam and stays exempt.
    if client is None and os.environ.get("DRIVE_PUBLISH_ENABLED") != "1":
        return ()
    try:
        drive = client if client is not None else _repo("drive_outputs").client_from_environment()
        folder = drive.ensure_folder_path(tuple(_repo("drive_taxonomy").project_parts("transcript", project)))
        for child in drive.list_children(folder):
            if str(child.get("name", "")) != GLOSSARY_FILE:
                continue
            with tempfile.TemporaryDirectory(prefix="stt-glossary-") as tmp:
                dest = Path(tmp) / GLOSSARY_FILE
                drive.download_file(str(child.get("id", "")), dest)
                return stt_polish.parse_glossary(dest.read_text(encoding="utf-8"))
    except Exception as failure:  # noqa: BLE001 - a missing glossary is not a failed meeting
        print(f"GLOSSARY-FETCH-FAIL project={project} {type(failure).__name__}", file=sys.stderr)
    return ()


def merged_glossary(project: str, *, client: object | None = None) -> tuple[tuple[str, str], ...]:
    """Global entries first, the project's own on top — a project name wins.

    Generic mishearings (영무→업무) hold for every meeting; institution names hold for
    one project and must not leak into another's transcript.
    """
    merged = dict(glossary())
    merged.update(dict(project_glossary(project, client=client)))
    return tuple(merged.items())


def glossary() -> tuple[tuple[str, str], ...]:
    """The owner's names, read once and used twice — as the model's hint and as the fix."""
    return stt_polish.load_glossary(os.environ)


def prompt_for(explicit: str, pairs: Sequence[tuple[str, str]] = ()) -> str:
    """An explicit flag, else the configured hint, else the glossary's own names."""
    return explicit or setting("SPEECHTOTEXT_PROMPT") or stt_polish.prompt_hint(pairs)


def polish_summary(tidied: stt_polish.Polished) -> dict[str, int]:
    return {
        "sentences": tidied.sentences,
        "paragraphs": tidied.paragraphs,
        "collapsed": tidied.collapsed,
        "substitutions": tidied.substitutions,
    }


def load_secrets_into_environment() -> None:
    """no-agent cron never injects `~/.env.secrets`, and the Drive facade reads os.environ.

    Loading them once, here, is what carries `DRIVE_PUBLISH_ENABLED`/`DRIVE_GWS_BIN` to the
    publish facade AND to the meeting child (설계규약 (b)/(b-2)). Without it the CLI
    transcribed a 94-minute meeting and returned `drive_link=""` while the node had the flag
    set the whole time — silent, which is how it survived a release.
    """
    for key, value in read_secrets().items():
        os.environ.setdefault(key, value)


def child_env() -> dict[str, str]:
    """Explicitly hand every resolved credential to the child (설계규약 (b-2))."""
    environment = dict(os.environ)
    resolved = read_secrets()
    for key in _SECRET_KEYS:
        value = environment.get(key) or resolved.get(key, "")
        if value:
            environment[key] = value
    return environment


def runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_RUNTIME_ROOT")
    if override:
        return Path(override)
    if _RELEASE_CURRENT.exists():
        return _RELEASE_CURRENT
    return _MIRROR_CHECKOUT


def meeting_cli_path() -> Path:
    """Prefer the governed live mount; fall back to the runtime release, then this checkout."""
    override = os.environ.get("SPEECHTOTEXT_MEETING_CLI")
    if override:
        return Path(override).expanduser()
    if _LIVE_MEETING_CLI.is_file():
        return _LIVE_MEETING_CLI
    runtime = runtime_root() / "skills/meeting/scripts/meeting_cli.py"
    if runtime.is_file():
        return runtime
    return Path(__file__).resolve().parents[3] / "skills/meeting/scripts/meeting_cli.py"


def transcript_dir() -> Path:
    return Path(
        os.environ.get("SPEECHTOTEXT_TRANSCRIPT_DIR") or "~/.hermes/speechtotext/transcripts"
    ).expanduser()


def publish_transcript(path: Path, label: str, now: datetime, project: str = "") -> str:
    """Best-effort publish through the shared Drive facade (사본 금지). Never raises."""
    if os.environ.get("DRIVE_PUBLISH_ENABLED") != "1":
        return ""
    root = str(runtime_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from automation import drive_outputs  # noqa: PLC0415 - lazy: optional runtime dependency

        result = drive_outputs.publish_best_effort(
            "transcript", label, ((path, label),), on=now.date(), project=project or None
        )
    except Exception as failure:  # noqa: BLE001 - publication must never break transcription
        print(f"DRIVE-PUBLISH-FAIL {type(failure).__name__}", file=sys.stderr)
        return ""
    return result.links[0] if result is not None and result.links else ""
