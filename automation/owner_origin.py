"""The owner's own recent Discord message as the basis for an action taken without a card.

One implementation for the YooDori gate's owner-command jobs and the cardless release (owner
2026-11-02 Q447), and for the card-approved dashboard todo the gate also takes as the owner's basis. The gate's
jobs also take the owner's tap on a question card (owner 2026-11-06 17:04 KST); the release does not.
It imports nothing from automation at module level, so the deploy gate can stage it alone and the gate's standalone libexec copy can import it lazily.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

DISCORD_EPOCH_MS = 1420070400000
ORIGIN_MAX_AGE_S = 24 * 3600
# The owner's tap on a question card (dori ask) as his message for the YooDori gate's jobs (owner 2026-11-06 17:04 KST
# "카드 버튼 답을 노드가 근거로 받게 바꿔.", Q523 2026-11-07 for the egress gate): the same rule as the fork's dori-gate
# (orientpine/omo-dori-mode-experimental#370). The fork folds a tapped card into one text,
# "✅ **Q<n>** <question>\n→ **<answer>**\n-# <by> · <when>", <by> being its words.byButton.
CARD_ANSWER = re.compile(r"(?:✅|\[[^\]\n]+\]) \*\*Q[1-9][0-9]*\*\* (?s:.+?)\n→ \*\*(?P<answer>(?s:.+?))\*\*\n"
                         r"-# (?P<by>[^\n·]+?) · [^\n]+")
CARD_ANSWER_WORDS = frozenset({"button", "버튼"})
COMPONENTS_V2 = 1 << 15


class OriginRefused(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Origin:
    channel_id: str
    message_id: str
    link: str


Fetch = Callable[[str, str], dict]


def _discord_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "yoodori-gate (cytoplasm)"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("unexpected Discord response")
    return value


def verify_owner_origin(
    channel_id: str, message_id: str, owner_id: str, token: str, *,
    now: float | None = None, fetch: Fetch = _discord_get, card_answers: bool = False,
) -> Origin:
    """The origin must be the owner's own (non-bot) message from the last 24 hours.

    With ``card_answers`` (the YooDori gate's jobs, not the release) the owner's tap on a question card counts too:
    see ``_card_answered_at``. Its 24 hours run from the tap, not from the card's post.
    """
    if not token:
        raise OriginRefused("origin check needs the node Discord credential")
    moment = now if now is not None else time.time()
    sent = ((int(message_id) >> 22) + DISCORD_EPOCH_MS) / 1000
    if not card_answers and moment - sent > ORIGIN_MAX_AGE_S:
        raise OriginRefused("origin message is older than 24 hours; ask the owner again")
    try:
        message = fetch(f"/channels/{channel_id}/messages/{message_id}", token)
        channel = fetch(f"/channels/{channel_id}", token)
    except (OSError, ValueError, urllib.error.URLError):
        raise OriginRefused("origin message could not be read; nothing was run") from None
    author = message.get("author")
    if (isinstance(author, dict) and str(author.get("id")) == owner_id
            and not author.get("bot") and str(message.get("id")) == message_id):
        when = sent
    elif card_answers and (tapped := _card_answered_at(message, channel_id, message_id, token, fetch)) is not None:
        when = tapped
    else:
        raise OriginRefused("origin message is not the owner's own message")
    if not 0 <= moment - when <= ORIGIN_MAX_AGE_S:
        raise OriginRefused("origin message is older than 24 hours; ask the owner again")
    return Origin(channel_id, message_id, _place(channel_id, message_id, channel, "origin"))


def _card_answered_at(message: dict, channel_id: str, message_id: str, token: str, fetch: Fetch) -> float | None:
    """When the owner tapped the question card at these coordinates, or None when it is no tapped card.

    The message must be this node bot's own post (``/users/@me``; the node's notice bot is YooDori's, which posts
    the cards), not a webhook, a components-v2 card that is one container holding one text, folded by an edit into
    the answered form with a non-empty answer and a button word as its by-line (a reply or a typed answer is not a
    tap). Discord does not say who tapped: the fork answers only the owner's tap, and the egress gate in front of
    YooDori refuses any hand-made answered card (card-answer-forged) but the tap's own UPDATE_MESSAGE callback.
    """
    author = message.get("author")
    outer = message.get("components")
    inner = (outer[0].get("components") if isinstance(outer, list) and len(outer) == 1 and isinstance(outer[0], dict)
             and outer[0].get("type") == 17 else None)
    if (str(message.get("id")) != message_id or str(message.get("channel_id")) != channel_id
            or message.get("webhook_id") or not isinstance(author, dict) or author.get("bot") is not True
            or not isinstance(message.get("flags"), int) or not message["flags"] & COMPONENTS_V2
            or not isinstance(inner, list) or len(inner) != 1 or not isinstance(inner[0], dict)
            or inner[0].get("type") != 10):
        return None
    answered = CARD_ANSWER.fullmatch(str(inner[0].get("content", "")))
    tapped = _epoch(message.get("edited_timestamp"))
    if not answered or not answered["answer"].strip() or answered["by"].casefold() not in CARD_ANSWER_WORDS \
            or tapped is None:
        return None
    try:
        me = fetch("/users/@me", token)
    except (OSError, ValueError, urllib.error.URLError):
        raise OriginRefused("origin message could not be read; nothing was run") from None
    if not isinstance(me, dict) or str(author.get("id")) != str(me.get("id")):
        return None
    return tapped


def _epoch(value: object) -> float | None:
    """An ISO timestamp with its zone as epoch seconds; None for a missing, malformed or zoneless one."""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return None if stamp.tzinfo is None else stamp.timestamp()


def _place(channel_id: str, message_id: str, channel: dict, what: str) -> str:
    """The message's link in its server (or the DM), or a refusal when it cannot be placed."""
    guild = channel.get("guild_id")
    from automation.interop.owner_message import discord_link

    if channel.get("type") == 1 and not guild:
        link = discord_link(space="dm", channel_id=channel_id, message_id=message_id)
    elif isinstance(guild, str) and guild.isdigit():
        link = discord_link(space="guild", guild_id=guild, channel_id=channel_id, message_id=message_id)
    else:
        raise OriginRefused(f"{what} channel could not be placed")
    if not link.url:
        raise OriginRefused(f"{what} message link could not be built")
    return link.url


# A dashboard todo the owner approved by its card is his basis too (owner 2026-11-03 06:09 KST Q2 "고친다: 카드 승인
# 할 일도 근거로 받음 (따로 PR, 추천)", after Q454/Q455: a card-approved todo goes at once, one the board took in
# without a card asks first). Same strength as the egress gate's X-Dori-Owner-Todo: the dashboard is asked on every
# use, nothing is cached.
TODO_OPEN_STATUSES = ("열림", "진행")
_DIGITS = re.compile(r"[0-9]{1,20}")


@dataclass(frozen=True, slots=True)
class TodoBasis:
    todo_id: str
    source_kind: str
    source_id: str
    channel_id: str
    message_id: str
    link: str


Ask = Callable[[str, str], object]
FetchAny = Callable[[str, str], object]


def _approved_age(value: object, now: float) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return None if stamp.tzinfo is None else now - stamp.timestamp()


def _owner_reacted(users: object, owner_id: str) -> bool:
    if not isinstance(users, list):
        raise ValueError("unexpected reaction list")
    return any(isinstance(user, dict) and str(user.get("id")) == owner_id and not user.get("bot") for user in users)


def verify_owner_todo(
    todo_id: str, owner_id: str, token: str, *, ask: Ask, fetch: FetchAny, now: float | None = None,
) -> TodoBasis:
    """The todo must have come in through the owner's ✅ on its card, be open and approved in the last 24 hours.

    ``ask(method, path)`` reads the dashboard's gate API (``/api/todos/<id>``): the todo id must match,
    ``auto_applied`` must be exactly false with the card's channel and message, status 열림/진행, approval
    0-24 hours old, and a source to bind the use to. Then the card itself is read on Discord: it must be this
    node bot's own message, the owner's own account (not a bot) reacted ✅ and not ⛔. Nothing is recorded here;
    the caller checks its target against the source and then calls ``record_todo_use``.
    """
    if not token:
        raise OriginRefused("todo check needs the node Discord credential")
    try:
        data = ask("GET", f"/api/todos/{quote(todo_id, safe='')}")
    except (OSError, ValueError, urllib.error.URLError):
        raise OriginRefused("the dashboard could not be asked about the todo; nothing was run") from None
    if not isinstance(data, dict) or data.get("todo_id") != todo_id:
        raise OriginRefused("no such todo on the dashboard")
    card = data.get("card")
    channel_id = str(card.get("channel_id", "")) if isinstance(card, dict) else ""
    message_id = str(card.get("message_id", "")) if isinstance(card, dict) else ""
    if data.get("auto_applied") is not False or not _DIGITS.fullmatch(channel_id) or not _DIGITS.fullmatch(message_id):
        raise OriginRefused("the todo was not approved on the owner's card; ask the owner")
    if data.get("status") not in TODO_OPEN_STATUSES:
        raise OriginRefused("the todo is not open (done or on hold)")
    age = _approved_age(data.get("approved_at"), now if now is not None else time.time())
    if age is None or not 0 <= age <= ORIGIN_MAX_AGE_S:
        raise OriginRefused("the todo's card approval is older than 24 hours; ask the owner")
    source = data.get("source")
    kind = str(source.get("kind", "")) if isinstance(source, dict) else ""
    source_id = str(source.get("id", "")) if isinstance(source, dict) else ""
    if not kind or not source_id:
        raise OriginRefused("the todo names no source to read")
    card_path = f"/channels/{channel_id}/messages/{message_id}"
    try:
        me = fetch("/users/@me", token)
        message = fetch(card_path, token)
        channel = fetch(f"/channels/{channel_id}", token)
        approved = _owner_reacted(fetch(f"{card_path}/reactions/{quote('✅')}?limit=100", token), owner_id)
        cancelled = _owner_reacted(fetch(f"{card_path}/reactions/{quote('⛔')}?limit=100", token), owner_id)
    except (OSError, ValueError, urllib.error.URLError):
        raise OriginRefused("the todo's card could not be read; nothing was run") from None
    author = message.get("author") if isinstance(message, dict) else None
    if (not isinstance(me, dict) or not isinstance(author, dict) or not author.get("bot")
            or str(author.get("id")) != str(me.get("id")) or str(message.get("id")) != message_id):
        raise OriginRefused("the todo's card is not this node's own card")
    if cancelled or not approved:
        raise OriginRefused("the owner's ✅ (without ⛔) is not on the todo's card")
    if not isinstance(channel, dict):
        raise OriginRefused("todo card channel could not be placed")
    return TodoBasis(todo_id, kind, source_id, channel_id, message_id, _place(channel_id, message_id, channel, "todo card"))


def record_todo_use(todo_id: str, *, ask: Ask) -> int:
    """Record one use on the dashboard (``POST /api/todos/<id>/use``); a failure refuses the action."""
    try:
        data = ask("POST", f"/api/todos/{quote(todo_id, safe='')}/use")
    except (OSError, ValueError, urllib.error.URLError):
        raise OriginRefused("the todo's use could not be recorded on the dashboard; nothing was run") from None
    uses = data.get("uses") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("todo_id") != todo_id or not isinstance(uses, int) or uses < 1:
        raise OriginRefused("the todo's use could not be recorded on the dashboard; nothing was run")
    return uses
