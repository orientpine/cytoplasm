"""The owner's own Discord message is the release approval basis (no card, no ✅).

Owner 2026-11-02 21:35 KST (voice, Q447): "릴리즈도 승인 필요 없을 것 같아. 내라고 하면 네가
해서 릴리즈하고 배포까지 하는 걸로 하자." A release therefore starts from a link to the
owner's message that asked for it. The node agent account checks that message once when
the record is written (the owner's own non-bot message in the release server, at most 24
hours old, naming a release or a deploy, never used for another HEAD) and again, without
the age limit, every time a deploy step asks the record to authorize a surface.

Nothing else about the release changes: the record still binds version, HEAD and every
surface digest in its action hash, the tag is still signed on the workstation, and the
node still converges only to a verified signed tag. Stored card records keep their ✅
replay unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.interop.approval_lifecycle import Probe
from automation.release_spec import RELEASE_ACTION, ReleaseSpec

BASIS: Final = "owner-command"
#: Words that name a release. Short verbs such as "내" are deliberately absent: they turn up
#: in unrelated owner messages, and a stray match here would cut and deploy a release.
KEYWORD: Final = re.compile(r"릴리스|릴리즈|release|배포|deploy", re.IGNORECASE)
_LINK: Final = re.compile(
    r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/([0-9]{1,20})/([0-9]{1,20})/([0-9]{1,20})/?\Z"
)
_VOICE_FLAG: Final = 1 << 13
_AUDIO_LIMIT: Final = 25 * 1024 * 1024
_COMMANDS_DIRNAME: Final = "release-commands"

Fetch = Callable[[str], Mapping[str, object]]
Download = Callable[[str], bytes]


class CommandRefused(ValueError):
    """The linked message cannot authorize a release; nothing is tagged or deployed."""


@dataclass(frozen=True, slots=True)
class Link:
    guild_id: str
    channel_id: str
    message_id: str


def parse_link(url: str) -> Link:
    match = _LINK.fullmatch(url.strip())
    if match is None:
        raise CommandRefused("owner command must be a Discord message link")
    return Link(*match.groups())


def is_command(record: Mapping[str, str]) -> bool:
    return record.get("approval_basis", "") == BASIS


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _voice_attachment(message: Mapping[str, object]) -> Mapping[str, object] | None:
    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        return None
    flags = message.get("flags")
    voice = isinstance(flags, int) and bool(flags & _VOICE_FLAG)
    for item in attachments:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("content_type") or "")
        if voice or kind.startswith("audio/") or str(item.get("filename") or "").startswith("voice-message"):
            return item
    return None


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "cytoplasm-release-command"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(_AUDIO_LIMIT + 1)
    if len(data) > _AUDIO_LIMIT:
        raise CommandRefused("owner voice message is larger than the release command limit")
    return data


def verify(
    url: str, *, owner_id: str, guild_id: str, token: str, transcript: str | None,
    fetch: Fetch, download: Download | None = None, now: float | None = None,
) -> dict[str, str]:
    """Check the linked owner message and return the basis fields the record keeps.

    A voice message has no text, so its words come from the receiver's transcript of it
    (the inbox row the caller looked up); the record then keeps the audio and transcript
    digests so the words that authorized the release can be traced to that recording.
    """
    from automation.owner_origin import OriginRefused, verify_owner_origin

    link = parse_link(url)
    if link.guild_id != guild_id:
        raise CommandRefused("owner command is not in the release server")
    seen: dict[str, Mapping[str, object]] = {}

    def cached(path: str, _token: str) -> dict:
        if path not in seen:
            seen[path] = fetch(path)
        return dict(seen[path])

    try:
        origin = verify_owner_origin(
            link.channel_id, link.message_id, owner_id, token, now=now, fetch=cached,
        )
    except OriginRefused as error:
        raise CommandRefused(str(error)) from None
    message = seen[f"/channels/{link.channel_id}/messages/{link.message_id}"]
    channel = seen[f"/channels/{link.channel_id}"]
    if str(channel.get("guild_id") or "") != guild_id:
        raise CommandRefused("owner command is not in the release server")
    content = str(message.get("content") or "").strip()
    fields = {
        "approval_basis": BASIS,
        "owner_command_url": origin.link,
        "owner_command_guild_id": guild_id,
        "owner_command_verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }
    if content:
        words = content
        fields |= {"owner_command_kind": "text", "owner_command_text_sha256": _sha256(content.encode())}
    else:
        voice = _voice_attachment(message)
        if voice is None:
            raise CommandRefused("owner command has neither text nor a voice message")
        words = (transcript or "").strip()
        if not words:
            raise CommandRefused("owner voice command has no transcript; pass the receiver inbox")
        try:
            audio = (download or _download)(str(voice.get("url") or ""))
        except (OSError, ValueError, urllib.error.URLError):
            raise CommandRefused("owner voice message could not be read") from None
        fields |= {
            "owner_command_kind": "voice",
            "owner_command_audio_sha256": _sha256(audio),
            "owner_command_transcript_sha256": _sha256(words.encode()),
        }
    if KEYWORD.search(words) is None:
        raise CommandRefused("owner command does not ask for a release or a deploy")
    return fields


def _ledger(gate_dir: Path, message_id: str) -> Path:
    return gate_dir / _COMMANDS_DIRNAME / message_id


def claim(gate_dir: Path, message_id: str, head: str) -> None:
    """One owner message authorizes one HEAD. Re-running the same release is a resume."""
    path = _ledger(gate_dir, message_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        used = path.read_text(encoding="utf-8").strip()
        if used != head:
            raise CommandRefused(f"owner command was already used for {used[:12]}") from None
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        _ = stream.write(head + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def new_record(spec: ReleaseSpec, link: Link, basis: Mapping[str, str]) -> dict[str, str]:
    """The same record fields a card record replays, with the owner message instead of a card."""
    return {
        "version": spec.version,
        "head_sha": spec.head_sha,
        "release_nonce": spec.release_nonce,
        "surface_digests": json.dumps(spec.surface_digests, separators=(",", ":"), ensure_ascii=True),
        "patch_notes": spec.patch_notes,
        "render_version": str(spec.render_version),
        "major_note": spec.major_note,
        "card_summary": spec.card_summary,
        "previous_surfaces": json.dumps(spec.previous_surfaces, separators=(",", ":")),
        "message_id": link.message_id,
        "channel_id": link.channel_id,
        "action_hash": spec.action_hash(),
        "approval_action": RELEASE_ACTION,
        "approval_destination": f"release:{spec.version}",
        **basis,
    }


def write_record(path: Path, record: Mapping[str, str]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".release.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            _ = stream.write(json.dumps(dict(record), sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def probe(
    record: Mapping[str, str], spec: ReleaseSpec, *, gate_dir: Path, owner_id: str, fetch: Fetch,
) -> Probe:
    """Approved iff the record is intact, its command was claimed for this HEAD, and the
    linked message is still the owner's own. A read failure is unverifiable, never approved."""
    if record.get("action_hash", "") != spec.action_hash():
        return Probe.BINDING_MISMATCH
    channel_id, message_id = record.get("channel_id", ""), record.get("message_id", "")
    try:
        link = parse_link(record.get("owner_command_url", ""))
    except CommandRefused:
        return Probe.BINDING_MISMATCH
    if (link.channel_id, link.message_id, link.guild_id) != (
        channel_id, message_id, record.get("owner_command_guild_id", ""),
    ):
        return Probe.BINDING_MISMATCH
    try:
        used = _ledger(gate_dir, message_id).read_text(encoding="utf-8").strip()
    except OSError:
        return Probe.BINDING_MISMATCH
    if used != spec.head_sha:
        return Probe.BINDING_MISMATCH
    try:
        message = fetch(f"/channels/{channel_id}/messages/{message_id}")
    except (OSError, ValueError, urllib.error.URLError):
        return Probe.UNVERIFIABLE
    author = message.get("author")
    if (not isinstance(author, dict) or str(author.get("id")) != owner_id or author.get("bot")
            or str(message.get("id")) != message_id):
        return Probe.BINDING_MISMATCH
    return Probe.APPROVED


def node_probe(record: Mapping[str, str], spec: ReleaseSpec) -> Probe:
    """`probe` with the node gate's own credential, owner and state directory."""
    from automation import skill_gate

    def fetch(path: str) -> Mapping[str, object]:
        value = skill_gate._api("GET", path)  # noqa: SLF001 - the gate owns its Discord seam
        if not isinstance(value, dict):
            raise ValueError("unexpected Discord response")
        return value

    try:
        owner_id = skill_gate._owner_id()  # noqa: SLF001
    except SystemExit:
        return Probe.UNVERIFIABLE
    return probe(record, spec, gate_dir=skill_gate.GATE_DIR, owner_id=owner_id, fetch=fetch)


def inbox_transcript(inbox: Path, message_id: str) -> str | None:
    """The receiver's transcript of one owner message, from its JSON-lines inbox."""
    try:
        lines = inbox.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and str(row.get("id", "")) == message_id:
            text = row.get("transcript")
            return text if isinstance(text, str) and text.strip() else None
    return None


def cmd_attach(args: argparse.Namespace) -> int:
    """Workstation half: put the command link (and a voice transcript) into the plan file."""
    path = Path(args.plan_file)
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        link = parse_link(args.url)
    except CommandRefused as error:
        print(f"RELEASE-COMMAND-REFUSED: {error}", file=sys.stderr)
        return 4
    payload["owner_command_url"] = args.url.strip()
    transcript = inbox_transcript(Path(args.inbox), link.message_id) if args.inbox else None
    if transcript is not None:
        payload["owner_command_transcript"] = transcript
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-command")
    commands = parser.add_subparsers(dest="command", required=True)
    attach = commands.add_parser("attach")
    _ = attach.add_argument("--plan-file", required=True)
    _ = attach.add_argument("--url", required=True)
    _ = attach.add_argument("--inbox", default="")
    args = parser.parse_args(argv)
    return cmd_attach(args)


if __name__ == "__main__":
    raise SystemExit(main())
