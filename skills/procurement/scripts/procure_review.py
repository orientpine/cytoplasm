"""Review-request DM transport for the procurement skill (W4-4).

The ONLY outbound surface of this skill: a DM to the owner (cha) asking for
human review. ≤25 MiB attaches the draft; larger files are uploaded to cha's
own Drive (gws CLI) and the DM carries the link. Submission is ALWAYS human —
this module has no mail/submit code path at all.

Sandbox hooks: PROCURE_DISCORD_STUB=<dir> records the would-be DM as JSON
instead of calling Discord. Drive upload goes through automation.drive_outputs
(opt-in via DRIVE_PUBLISH_ENABLED; unset means zero Drive calls, empty link).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from procure_core import DM_MAX_BYTES, review_mode

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"


class ReviewError(RuntimeError):
    """Review DM could not be delivered (exit 6)."""


def _notice_channel() -> str:
    """ON-2: 검토 요청이 갈 채널 — 해석(지정 채널/DM 오픈)은 owner_notice 파사드만 한다."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    release = Path("/srv/autophagy-agent-current")
    root = override or str(release if release.is_dir() else Path("/srv/autophagy-agents"))
    if root not in sys.path:
        sys.path.insert(0, root)
    from automation.owner_notice import resolve_notice_target

    try:
        target = resolve_notice_target(_token())
    except ReviewError:
        raise
    except Exception as error:  # noqa: BLE001 - 원인 유형만 남기고 exit 6 계약 유지
        raise ReviewError(f"통지 채널 해석 실패: {type(error).__name__}") from None
    if not target:
        raise ReviewError("통지 대상 미해석 — interop config owner_id/owner_notice_channel_id 확인")
    return target


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
    link = ""
    if mode == "drive-link":
        try:
            from automation.drive_outputs import publish_best_effort

            result = publish_best_effort("procurement", file.stem, [(file, file.stem)])
            if result is not None and result.links:
                link = result.links[0]
        except ImportError:
            link = ""
    content = review_note(file, mode, note, link)
    stub = os.environ.get("PROCURE_DISCORD_STUB", "")
    if stub:
        record = {"mode": mode, "size": size, "file": file.name, "content": content}
        out = Path(stub) / f"dm-{uuid.uuid4().hex[:8]}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return f"REVIEW-DM-SENT message=stub:{out.name} mode={mode} size={size}"
    channel_id = _notice_channel()
    if mode == "attach":
        message = _post_attachment(channel_id, file, content)
    else:
        message = _api("POST", f"/channels/{channel_id}/messages", {"content": content})
    return f"REVIEW-DM-SENT message={message['id']} mode={mode} size={size}"



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
