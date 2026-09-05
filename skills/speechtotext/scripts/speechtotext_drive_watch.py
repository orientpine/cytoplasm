#!/usr/bin/env python3
"""no-agent cron: pick up new recordings from the watched Drive folder.

Polls **Drive**, not Discord — so it is not a competing consumer of the
realtime agent's messages (설계규약 (a)). Everything else follows the watcher
contract: `~/.env.secrets` is loaded by this process (b), every credential is
handed to the child explicitly (b-2), the repo import goes through the runtime
root resolver (c), the file name is skill-unique (e), and a recording is marked
processed only after its ingest succeeded (f).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

_LIVE_SCRIPTS: Final = "/srv/autophagy-skills/live/speechtotext/scripts"
_SCRIPTS = Path(os.environ.get("SPEECHTOTEXT_SCRIPTS", _LIVE_SCRIPTS)).expanduser()
if _SCRIPTS.is_dir() and str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import stt_drive  # noqa: E402

_RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
_MIRROR_CHECKOUT: Final = Path("/srv/autophagy-agents")
_SECRET_KEYS: Final = (
    "OPENAI_API_KEY",
    "DISCORD_BOT_TOKEN",
)
_CHILD_TIMEOUT: Final = 21600.0

Runner = Callable[[list[str], dict[str, str]], int]


class DriveLike(Protocol):
    def ensure_folder_path(self, parts: tuple[str, ...]) -> str: ...

    def list_children(self, folder_id: str) -> list[dict[str, Any]]: ...

    def verify_owner_only(self, file_id: str) -> None: ...

    def download_file(self, file_id: str, dest: Path) -> str: ...


def _secrets(env: Mapping[str, str]) -> dict[str, str]:
    home = env.get("HOME")
    path = (Path(home) if home else Path.home()) / ".env.secrets"
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


def child_env(env: Mapping[str, str]) -> dict[str, str]:
    """Hand every resolved credential to the child explicitly (설계규약 (b-2))."""
    environment = dict(env)
    secrets = _secrets(env)
    for key in _SECRET_KEYS:
        value = environment.get(key) or secrets.get(key, "")
        if value:
            environment[key] = value
    return environment


def cli_path(env: Mapping[str, str]) -> Path:
    override = env.get("SPEECHTOTEXT_CLI")
    if override:
        return Path(override).expanduser()
    live = Path(_LIVE_SCRIPTS) / "speechtotext_cli.py"
    if live.is_file():
        return live
    return Path(__file__).resolve().with_name("speechtotext_cli.py")


def _default_runner(argv: list[str], env: dict[str, str]) -> int:
    completed = subprocess.run(  # noqa: S603 - argv built from resolved interpreter + CLI path
        argv, env=env, check=False, timeout=_CHILD_TIMEOUT
    )
    return completed.returncode


def run_once(
    *,
    client: DriveLike,
    env: Mapping[str, str],
    runner: Runner,
    now: datetime,
) -> dict[str, object]:
    """One tick: resolve the folder, ingest what is new, mark only what worked."""
    folder_id = client.ensure_folder_path(stt_drive.folder_parts(env))
    children = client.list_children(folder_id)
    state_file = stt_drive.state_path(env)
    summary: dict[str, object] = {
        "scanned": len(children),
        "ingested": 0,
        "failed": 0,
        "skipped": 0,
    }
    for audio in stt_drive.pending(children, stt_drive.load_state(state_file)):
        try:
            client.verify_owner_only(audio.file_id)
        except Exception:  # noqa: BLE001 - a shared recording is skipped, never ingested
            summary["skipped"] = int(summary["skipped"]) + 1
            print(f"SPEECHTOTEXT-SKIP reason=not-owner-only id={audio.file_id}", file=sys.stderr)
            continue
        workdir = Path(tempfile.mkdtemp(prefix="stt-drive-"))
        try:
            local = workdir / audio.name
            digest = client.download_file(audio.file_id, local)
            argv = [
                sys.executable,
                str(cli_path(env)),
                "ingest",
                "--file",
                str(local),
                "--label",
                audio.label,
            ]
            code = runner(argv, child_env(env))
            if code == 0:
                stt_drive.mark_processed(state_file, audio, now=now, digest=digest)
                summary["ingested"] = int(summary["ingested"]) + 1
            else:
                summary["failed"] = int(summary["failed"]) + 1
                print(f"SPEECHTOTEXT-INGEST-FAIL rc={code} id={audio.file_id}", file=sys.stderr)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return summary


def _repo_root(env: Mapping[str, str]) -> str:
    root = env.get("AUTOPHAGY_RUNTIME_ROOT") or (
        str(_RELEASE_CURRENT) if _RELEASE_CURRENT.exists() else str(_MIRROR_CHECKOUT)
    )
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


def _pipeline_lock(env: Mapping[str, str]):
    """파이프라인 상호배제는 speechtotext 의 것도 meeting 의 것도 아닌 공용 정의다."""
    _repo_root(env)
    from automation import pipeline_lock  # noqa: PLC0415 - runtime-root import

    return pipeline_lock


def _drive_client(env: Mapping[str, str]):
    _repo_root(env)
    from automation.drive_client import DriveClient  # noqa: PLC0415 - runtime-root import

    cache = Path(env.get("DRIVE_FOLDER_CACHE") or "~/.hermes/drive/folders.json").expanduser()
    return DriveClient(
        gws_bin=env.get("DRIVE_GWS_BIN") or env.get("DRIVE_PUBLISH_GWS_BIN") or "gws",
        folder_cache=cache,
    )


def main(argv: list[str] | None = None) -> int:
    for key, value in _secrets(os.environ).items():
        os.environ.setdefault(key, value)
    try:
        pipeline_lock = _pipeline_lock(os.environ)
    except ImportError:
        print("SPEECHTOTEXT-WATCH-BLOCK: 파이프라인 lock 을 불러오지 못해 실행하지 않았습니다.")
        return 1
    state_file = stt_drive.state_path(os.environ)
    state_file.parent.mkdir(mode=stt_drive.DIR_MODE, parents=True, exist_ok=True)
    # 회의록까지가 한 파이프라인이라 lock 도 하나다 — meeting 야간 배치와 같은 파일을 잡는다.
    with pipeline_lock.hold(os.environ) as acquired:
        if not acquired:
            print("SPEECHTOTEXT-WATCH-BUSY")
            return 0
        try:
            summary = run_once(
                client=_drive_client(os.environ),
                env=os.environ,
                runner=_default_runner,
                now=datetime.now(UTC),
            )
        except stt_drive.DriveScanRefused as refusal:
            print(refusal.notice)
            return refusal.exit_code
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
