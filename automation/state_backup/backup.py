"""SC-3 (§10-4): weekly encrypted archive of ``~/.hermes`` local state to Google Drive.

The self skills, memory ledgers and curation counters under ``~/.hermes`` are the ONLY
copy of the node's learned personalization — a dead disk erases them (which is why a
node-local backup was rejected: same disk). The owner decided (2026-08-28) on a weekly
encrypted archive to Google Drive.

Boundaries, deliberately:

* **Backups are not outputs.** The ``drive_outputs`` taxonomy (categories, depth caps,
  period keys) does not apply; the archive lands in a dedicated ``autophagy-backups``
  root. The Drive I/O still rides the ONE low-level client (``drive_client``), so the
  facade conformance keeps holding — no argv copies, no second upload implementation.
* **Nothing leaves unencrypted.** The tar is encrypted with a LOCAL key
  (``~/.hermes/backup/backup.key``, 0600) via openssl before any upload; a missing or
  group/other-readable key fails closed with actionable guidance. The key itself is
  never uploaded — losing it makes every backup unreadable, so the owner keeps an
  offline copy (안내 문구가 그것을 말한다).
* **At-least-once per ISO week.** The cron ticks daily; a delivered-week watermark
  makes later ticks of a delivered week silent no-ops, and the watermark advances only
  after the uploaded bytes were re-downloaded and hash-verified.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol

BACKUP_ROOT_FOLDER: Final = "autophagy-backups"
#: ``~/.hermes`` 아래에서 백업되는 디렉터리 allowlist. 자격증명(`~/.env.secrets`)은
#: 루트가 달라 구조적으로 포함될 수 없고, allowlist 방식이라 벤더 설치본(`hermes-agent`)
#: 같은 재생성 가능 대용량도 실리지 않는다.
DEFAULT_INCLUDE: Final = (
    "skills",
    "selfskill-audit",
    "wiki-curate",
    "memory-curator",
    "supply-chain-watch",
)
RETAIN_GENERATIONS: Final = 8
_OPENSSL_ARGS: Final = (
    "enc", "-aes-256-cbc", "-md", "sha256", "-pbkdf2", "-iter", "200000", "-salt"
)

Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class BackupError(RuntimeError):
    """The backup cannot proceed safely — nothing was uploaded."""


class BackupClient(Protocol):
    """The exact Drive surface a backup needs — satisfied by ``drive_client.DriveClient``."""

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str: ...

    def upsert_file(
        self, local: Path, name: str, parent_id: str, prior_id: str | None = None
    ) -> dict[str, str]: ...

    def verify_owner_only(self, file_id: str) -> None: ...

    def download_and_verify(self, file_id: str, local: Path) -> str: ...

    def list_children(self, folder_id: str) -> list[dict[str, object]]: ...

    def trash_file(self, file_id: str) -> str: ...


def backup_key_path(home: Path) -> Path:
    return home / ".hermes" / "backup" / "backup.key"


def watermark_path(home: Path) -> Path:
    return home / ".hermes" / "backup" / "state.json"


def week_key(now: datetime) -> str:
    return now.strftime("%G-W%V")


def _delivered_week(path: Path) -> str:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = raw.get("delivered_week", "") if isinstance(raw, dict) else ""
    return value if isinstance(value, str) else ""


def _advance_watermark(path: Path, week: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = path.write_text(json.dumps({"delivered_week": week}) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _tar_member_ok(info: tarfile.TarInfo) -> bool:
    return info.isfile() or info.isdir()


def build_archive(home: Path, include: tuple[str, ...], dest: Path) -> int:
    """Pack the allowlisted state into ``dest``; returns the number of files packed."""
    root = home / ".hermes"
    packed = 0

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        nonlocal packed
        if "__pycache__" in info.name.split("/"):
            return None
        if not _tar_member_ok(info):
            return None  # 심링크·소켓은 상태가 아니다 — 따라가지도 싣지도 않는다
        if info.isfile():
            packed += 1
        return info

    with tarfile.open(dest, "w:gz") as archive:
        for name in include:
            source = root / name
            if not source.is_dir() or source.is_symlink():
                continue
            archive.add(source, arcname=name, recursive=True, filter=_filter)
    return packed


def encrypt_archive(
    archive: Path, key_file: Path, dest: Path, runner: Runner = subprocess.run
) -> None:
    """Encrypt with the LOCAL key, failing closed on a missing or exposed key."""
    if not key_file.is_file():
        raise BackupError(
            "BACKUP-KEY-MISSING: 암호화 키가 없어 업로드하지 않습니다 — "
            f"`openssl rand -hex 32 > {key_file} && chmod 600 {key_file}` 로 만들고 "
            "키의 오프라인 사본을 따로 보관하세요(키를 잃으면 모든 백업을 읽을 수 없습니다)"
        )
    mode = stat.S_IMODE(key_file.stat().st_mode)
    if mode & 0o077:
        raise BackupError(
            f"BACKUP-KEY-EXPOSED: 키 파일 권한이 {oct(mode)} 입니다 — chmod 600 후 재시도"
        )
    completed = runner(
        ["openssl", *_OPENSSL_ARGS, "-in", str(archive), "-out", str(dest),
         "-pass", f"file:{key_file}"],
        capture_output=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        raise BackupError(f"BACKUP-ENCRYPT-FAIL: openssl rc={completed.returncode}")


def _prune_old_generations(
    client: BackupClient, folder_id: str, prefix: str, keep: int
) -> int:
    rows = [
        (str(row.get("name", "")), str(row.get("id", "")))
        for row in client.list_children(folder_id)
        if str(row.get("name", "")).startswith(prefix) and str(row.get("id", ""))
    ]
    doomed = sorted(rows, reverse=True)[keep:]
    for _name, file_id in doomed:
        _ = client.trash_file(file_id)
    return len(doomed)


def run_once(
    *,
    home: Path | None = None,
    client: BackupClient | None = None,
    now: datetime | None = None,
    runner: Runner = subprocess.run,
    account_label: str | None = None,
    include: tuple[str, ...] = DEFAULT_INCLUDE,
) -> int:
    if os.environ.get("DRIVE_PUBLISH_ENABLED", "") != "1":
        print("BACKUP-SKIP: DRIVE-PUBLISH-DISABLED (opt-in 미설정)", file=sys.stderr)
        return 0
    account_home = Path.home() if home is None else home
    label = getpass.getuser() if account_label is None else account_label
    moment = datetime.now(UTC) if now is None else now
    week = week_key(moment)
    mark = watermark_path(account_home)
    if _delivered_week(mark) == week:
        return 0  # 이번 주는 이미 배달됐다 — 평일 틱은 조용한 no-op

    with tempfile.TemporaryDirectory(prefix="state-backup-") as scratch:
        plain = Path(scratch) / "state.tar.gz"
        sealed = Path(scratch) / f"state-{label}-{week}.tar.enc"
        packed = build_archive(account_home, include, plain)
        if packed == 0:
            print("BACKUP-SKIP: 백업할 상태가 없습니다", file=sys.stderr)
            return 0
        encrypt_archive(plain, backup_key_path(account_home), sealed, runner)
        plain.unlink()  # 평문은 업로드 경로 근처에 남기지 않는다

        drive = client
        if drive is None:
            from automation.drive_outputs import client_from_environment

            drive = client_from_environment()
        folder_id = drive.ensure_folder_path((BACKUP_ROOT_FOLDER, label))
        uploaded = drive.upsert_file(sealed, sealed.name, folder_id)
        file_id = uploaded["id"]
        drive.verify_owner_only(file_id)
        _ = drive.download_and_verify(file_id, sealed)
        pruned = _prune_old_generations(
            drive, folder_id, f"state-{label}-", RETAIN_GENERATIONS
        )

    _advance_watermark(mark, week)  # 검증까지 끝난 뒤에만 — 실패한 주는 다음 틱이 재시도
    print(f"BACKUP-OK week={week} files={packed} pruned={pruned}")
    return 0


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="state-backup")
    _ = parser.add_argument("--once", action="store_true", required=True)
    _ = parser.parse_args(argv)
    try:
        return run_once()
    except BackupError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
