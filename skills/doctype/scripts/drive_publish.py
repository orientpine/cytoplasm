"""Vendored Drive-publish helper: upload a FINAL deliverable into
``<DRIVE_OUTPUTS_ROOT>/<doc_type>/<YYYY-MM>/<file>`` (4-level) and return its
webViewLink.

Self-contained (gws CLI only) so it can be vendored into any skill without
importing automation.*. Uploads are review artifacts to the owner's OWN Drive
(not external sends) — no approval gate. Pass ONLY final deliverables (drafts
are the caller's concern).

``publish_best_effort`` is OPT-IN (``DRIVE_PUBLISH_ENABLED=1``) so tests and
non-production contexts make ZERO Drive calls by default; the deployed skills
set that env to activate immediate upload.

VENDORED COPY — keep skills/{report,proposal,doctype}/scripts/drive_publish.py
byte-identical.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

_FOLDER_MIME = "application/vnd.google-apps.folder"


class DrivePublishError(RuntimeError):
    """A gws Drive call failed or returned an unusable response (fail closed)."""


def _gws() -> str:
    return os.environ.get("DRIVE_PUBLISH_GWS_BIN") or os.environ.get("PROCURE_GWS_BIN") or "gws"


def _root() -> str:
    return os.environ.get("DRIVE_OUTPUTS_ROOT", "Autophagy 산출물")


def _period() -> str:
    return os.environ.get("DRIVE_PUBLISH_PERIOD") or datetime.now().strftime("%Y-%m")


def _run_json(argv: list[str]) -> dict:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)  # noqa: S603
    if proc.returncode != 0:
        raise DrivePublishError(
            f"{' '.join(argv[:4])} 실패 rc={proc.returncode}: {proc.stderr.strip()[:200]}"
        )
    decoded, _ = json.JSONDecoder().raw_decode(proc.stdout.strip() or "{}")
    return decoded if isinstance(decoded, dict) else {}


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _ensure_folder(gws: str, parts: list[str]) -> str:
    parent = "root"
    for name in parts:
        query = (
            f"name = '{_q(name)}' and '{parent}' in parents "
            f"and mimeType = '{_FOLDER_MIME}' and trashed = false"
        )
        listed = _run_json(
            [gws, "drive", "files", "list", "--params",
             json.dumps({"q": query, "fields": "files(id)", "pageSize": 1})]
        )
        found = listed.get("files")
        if isinstance(found, list) and found:
            parent = str(found[0].get("id", ""))
        else:
            created = _run_json(
                [gws, "drive", "files", "create", "--json",
                 json.dumps({"name": name, "mimeType": _FOLDER_MIME, "parents": [parent]})]
            )
            parent = str(created.get("id", ""))
        if not parent:
            raise DrivePublishError(f"drive 폴더 확보 실패: {name}")
    return parent


def publish(file: Path, doc_type: str) -> str:
    """Upload ``file`` into ``<root>/<doc_type>/<YYYY-MM>/`` and return webViewLink."""
    gws = _gws()
    parent = _ensure_folder(gws, [_root(), doc_type, _period()])
    created = _run_json([gws, "drive", "+upload", str(file), "--parent", parent])
    file_id = str(created.get("id", ""))
    if not file_id:
        raise DrivePublishError(f"drive 업로드 응답에 id 없음: {created}")
    meta = _run_json(
        [gws, "drive", "files", "get",
         "--params", json.dumps({"fileId": file_id, "fields": "webViewLink"})]
    )
    return str(meta.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view")


def publish_best_effort(file: Path, doc_type: str) -> str:
    """Publish only when enabled (``DRIVE_PUBLISH_ENABLED=1``); never raises.

    Opt-in so tests/non-production make zero Drive calls by default.
    """
    if os.environ.get("DRIVE_PUBLISH_ENABLED") != "1":
        return ""
    try:
        return publish(file, doc_type)
    except (DrivePublishError, OSError):
        return ""
