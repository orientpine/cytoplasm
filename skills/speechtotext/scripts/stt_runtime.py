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
from collections.abc import Callable, Mapping, Sequence
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


#: 용어집 파일 이름 — 앞이 정본이고 뒤는 이름을 바꾸기 전에 소유자가 이미 써 둔 파일이다.
#: `.csv` 인 이유는 용어집이 두 칸짜리 표이고, Drive 가 표를 Sheets 로 열어 주기 때문이다.
GLOSSARY_FILES: Final = ("용어집.csv", "용어집.txt")
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


def glossary_layers(project: str = "") -> tuple[tuple[str, ...], ...]:
    """Folders a 용어집.csv may sit in, outermost first — the deeper one wins.

    The output tree is nested (`autophagy/전사본/<과제>`), so the glossary is nested with
    it: a name written once at the root holds for every recording, and an inner folder
    overrides that one name without repeating the others.
    """
    taxonomy = _repo("drive_taxonomy")
    deepest = tuple(
        taxonomy.project_parts("transcript", project)
        if project
        else taxonomy.category_parts("transcript")
    )
    return tuple(deepest[:depth] for depth in range(1, len(deepest) + 1))


def _layer_glossary(drive: object, parts: tuple[str, ...]) -> str | None:
    """The glossary in exactly this folder — None when the folder or the file is absent.

    `find_folder_path` and never `ensure_folder_path`: looking for a glossary must not
    create the folder it looked in, and a nested walk looks in three of them.
    """
    folder = drive.find_folder_path(parts)
    if folder is None:
        return None
    named = {str(child.get("name", "")): str(child.get("id", "")) for child in drive.list_children(folder)}
    for name in GLOSSARY_FILES:
        if name not in named:
            continue
        with tempfile.TemporaryDirectory(prefix="stt-glossary-") as tmp:
            dest = Path(tmp) / name
            drive.download_file(named[name], dest)
            return dest.read_text(encoding="utf-8")
    return None


def _fetch_layers(
    layers: Callable[[], Sequence[tuple[str, ...]]], *, scope: str, client: object | None
) -> dict[str, str] | None:
    """Merge each layer, outermost first — None when Drive was not consulted at all.

    Building a client from the environment means talking to Drive, so it needs the same
    opt-in every other Drive touch needs. Without this a unit test that merely runs the
    CLI reaches the real Drive of the owner — and one did, creating a folder named after
    a test fixture. An injected client is the test seam and stays exempt.

    One failed layer takes the whole answer down to None on purpose: a partial merge
    would silently drop an inner override and correct a name with the outer value.
    """
    if client is None and os.environ.get("DRIVE_PUBLISH_ENABLED") != "1":
        return None
    merged: dict[str, str] = {}
    try:
        drive = client if client is not None else _repo("drive_outputs").client_from_environment()
        for parts in layers():
            text = _layer_glossary(drive, parts)
            if text is not None:
                merged.update(dict(stt_polish.parse_glossary(text)))
    except Exception as failure:  # noqa: BLE001 - a missing glossary is not a failed meeting
        print(f"GLOSSARY-FETCH-FAIL scope={scope} {type(failure).__name__}", file=sys.stderr)
        return None
    return merged


def _cache_glossary(pairs: Mapping[str, str]) -> None:
    """Mirror the fetched glossary onto the node — a cache, never the source of truth.

    It is what the opted-out path reads: plaud lifelog transcribes with
    `DRIVE_PUBLISH_ENABLED=0`, so without this file it would correct no name at all.
    """
    path = Path(stt_polish.DEFAULT_GLOSSARY).expanduser()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        rows = "".join(f"{k}\n" if k == v else f"{k}={v}\n" for k, v in pairs.items())
        path.write_text(rows, encoding="utf-8")
    except OSError as failure:
        print(f"GLOSSARY-CACHE-FAIL {type(failure).__name__}", file=sys.stderr)


def project_glossary(project: str, *, client: object | None = None) -> tuple[tuple[str, str], ...]:
    """The innermost layer alone — the outer ones are the other half of the nest."""
    if not project:
        return ()
    fetched = _fetch_layers(lambda: glossary_layers(project)[-1:], scope=project, client=client)
    return () if fetched is None else tuple(fetched.items())


def merged_glossary(project: str, *, client: object | None = None) -> tuple[tuple[str, str], ...]:
    """Outer layers first, the project on top — the deeper name wins.

    Generic mishearings (영무→업무) hold for every meeting; institution names hold for
    one project and must not leak into the transcript of another.
    """
    merged = dict(glossary(client=client))
    merged.update(dict(project_glossary(project, client=client)))
    return tuple(merged.items())


def glossary(*, client: object | None = None) -> tuple[tuple[str, str], ...]:
    """The layers that hold for every project — Drive is the original, the node a cache.

    An explicitly configured path wins outright: the sandbox and an offline node need a
    file they can point at, and a test must not reach a real Drive to be repeatable.
    Drive answering with nothing is an answer, so the cache is emptied to match — a stale
    local copy promoted to source of truth is how a retired name comes back.
    """
    if os.environ.get(stt_polish.GLOSSARY_ENV):
        return stt_polish.load_glossary(os.environ)
    fetched = _fetch_layers(lambda: glossary_layers(""), scope="공통", client=client)
    if fetched is None:
        return stt_polish.load_glossary(os.environ)
    if not fetched:
        print("GLOSSARY-DRIVE-ABSENT scope=공통", file=sys.stderr)
    _cache_glossary(fetched)
    return tuple(fetched.items())


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
