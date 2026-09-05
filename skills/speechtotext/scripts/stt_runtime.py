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


#: 소유자가 참고 문서 파일 하나를 명시하는 옛 이름 — 샌드박스·오프라인 노드는 이것만 갖는다.
GLOSSARY_ENV: Final = "SPEECHTOTEXT_GLOSSARY"
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


def glossary(project: str = "") -> tuple[tuple[str, str], ...]:
    """전사 **전에** 모델에게 주는 힌트의 재료 — 전사본을 고치는 데는 쓰지 않는다.

    교정은 산출 문서를 만들 때 일어난다(docs/guide/용어-교정-규약.md). 그래서 층 조회·노드
    캐시·Drive 옵트인은 전부 automation/term_glossary 가 하고, 여기는 그 답을 받아 인식 조건
    으로만 쓴다 — 사본을 두면 같은 낱말이 스킬마다 달라진다.

    명시된 파일은 명시한 대로 읽는다: 샌드박스와 오프라인 노드의 결정성이 여기 걸려 있고,
    소유자가 이미 그 경로를 설정해 둔 노드가 있다.
    """
    explicit = os.environ.get(GLOSSARY_ENV)
    if explicit:
        try:
            content = Path(explicit).expanduser().read_text(encoding="utf-8")
        except OSError:
            return ()
        return _repo("term_correction").parse_glossary(content)
    return _repo("term_glossary").glossary_for("transcript", project)


def prompt_for(explicit: str, pairs: Sequence[tuple[str, str]] = ()) -> str:
    """An explicit flag, else the configured hint, else the glossary's own names."""
    configured = explicit or setting("SPEECHTOTEXT_PROMPT")
    return configured or _repo("term_correction").prompt_hint(pairs)


def polish_summary(tidied: stt_polish.Polished) -> dict[str, int]:
    return {
        "sentences": tidied.sentences,
        "paragraphs": tidied.paragraphs,
        "collapsed": tidied.collapsed,
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
