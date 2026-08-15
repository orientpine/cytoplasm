"""Review-request DM transport for the procurement skill (W4-4).

The ONLY outbound surface of this skill: a DM to the owner (cha) asking for
human review. ≤25 MiB attaches the draft; larger files are uploaded to cha's
own Drive (gws CLI) and the DM carries the link. Submission is ALWAYS human —
this module has no mail/submit code path at all.

Sandbox hooks: PROCURE_DISCORD_STUB=<dir> records the would-be DM as JSON
instead of calling Discord; PROCURE_GWS_BIN overrides the gws binary.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from procure_core import DM_MAX_BYTES, review_mode

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"


class ReviewError(RuntimeError):
    """Review DM could not be delivered (exit 6)."""


def owner_id() -> str:
    config = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    try:
        owner = json.loads(config.read_text(encoding="utf-8")).get("owner_id")
    except OSError:
        raise ReviewError(f"interop config 읽기 실패: {config}") from None
    if not isinstance(owner, str) or not owner:
        raise ReviewError("interop config에 owner_id가 없습니다")
    return owner


def max_bytes() -> int:
    return int(os.environ.get("PROCURE_DM_MAX_BYTES", DM_MAX_BYTES))


def review_note(file: Path, mode: str, note: str, link: str) -> str:
    body = f"📄 서류 초안 검토 요청: `{file.name}`"
    if note:
        body += f"\n{note}"
    body += f"\n(Drive 링크: {link})" if mode == "drive-link" else ""
    return body + "\n검토 후 **제출은 cha가 직접** 해주세요 — 이 스킬은 어디에도 제출하지 않습니다."


def send_review(file: Path, note: str) -> str:
    """Returns 'REVIEW-DM-SENT message=<id> mode=<mode> size=<bytes>'."""
    size = file.stat().st_size
    mode = review_mode(size, max_bytes())
    link = _drive_upload(file) if mode == "drive-link" else ""
    content = review_note(file, mode, note, link)
    stub = os.environ.get("PROCURE_DISCORD_STUB", "")
    if stub:
        record = {"mode": mode, "size": size, "file": file.name, "content": content}
        out = Path(stub) / f"dm-{uuid.uuid4().hex[:8]}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return f"REVIEW-DM-SENT message=stub:{out.name} mode={mode} size={size}"
    channel = _api("POST", "/users/@me/channels", {"recipient_id": owner_id()})
    if mode == "attach":
        message = _post_attachment(str(channel["id"]), file, content)
    else:
        message = _api("POST", f"/channels/{channel['id']}/messages", {"content": content})
    return f"REVIEW-DM-SENT message={message['id']} mode={mode} size={size}"


_FOLDER_MIME = "application/vnd.google-apps.folder"


def _drive_root_name() -> str:
    return os.environ.get("PROCURE_DRIVE_ROOT", "Autophagy 산출물")


def _drive_period() -> str:
    return os.environ.get("PROCURE_DRIVE_PERIOD") or datetime.now().strftime("%Y-%m")


def _q_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _ensure_folder(gws: str, parts: list[str]) -> str:
    """Find-or-create each folder in ``parts`` under My Drive root; return leaf id."""
    parent = "root"
    for name in parts:
        query = (
            f"name = '{_q_escape(name)}' and '{parent}' in parents "
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
            raise ReviewError(f"drive 폴더 확보 실패: {name}")
    return parent


def _drive_upload(file: Path) -> str:
    gws = os.environ.get("PROCURE_GWS_BIN", "gws")
    parent = _ensure_folder(gws, [_drive_root_name(), "procurement", _drive_period()])
    created = _run_json([gws, "drive", "+upload", str(file), "--parent", parent])
    file_id = str(created.get("id", ""))
    if not file_id:
        raise ReviewError(f"drive 업로드 응답에 id 없음: {created}")
    meta = _run_json(
        [gws, "drive", "files", "get",
         "--params", json.dumps({"fileId": file_id, "fields": "webViewLink"})]
    )
    return str(meta.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view")


def _run_json(argv: list[str]) -> dict:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise ReviewError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} 실패 rc={proc.returncode}")
    decoded, _ = json.JSONDecoder().raw_decode(proc.stdout.strip() or "{}")
    return decoded if isinstance(decoded, dict) else {}


def _token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise ReviewError("DISCORD_BOT_TOKEN 누락 — 검토 DM 전송 불가")
    return token


def _api(method: str, path: str, payload: dict | None = None) -> dict:
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {_token()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    with urlopen(request, timeout=60) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _post_attachment(channel_id: str, file: Path, content: str) -> dict:
    boundary = f"----procure{secrets.token_hex(12)}"
    payload = json.dumps(
        {"content": content, "attachments": [{"id": 0, "filename": file.name}]},
        ensure_ascii=False,
    ).encode("utf-8")
    body = b"".join(
        [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            "Content-Type: application/json\r\n\r\n".encode("utf-8"), payload,
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            f"filename=\"{file.name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8"),
            file.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = Request(
        f"{API}/channels/{channel_id}/messages",
        data=body,
        headers={
            "Authorization": f"Bot {_token()}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urlopen(request, timeout=120) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))
