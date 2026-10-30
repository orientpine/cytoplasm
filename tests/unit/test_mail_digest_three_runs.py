"""The mail digest runs three times a day: 09:00, 13:00 and 19:00 KST (owner 2026-10-30).

New file rather than test_mail_digest.py (its output is pinned by an FS3 record,
tests/AGENTS.md). Each case fails when the behaviour it names is reverted: the timer
declaration firing once a day, a later run showing a mail or handing over an event an
earlier run already did, a later run opening a second thread or keeping the first run's
time in the thread name, and a later empty run posting "no new mail" again.
All ids and mails are synthetic.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_approval  # noqa: E402
import triage_binding  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_digest  # noqa: E402
import triage_digest_thread  # noqa: E402
import triage_transport  # noqa: E402

from automation.cron_schedule import on_calendar  # noqa: E402
from automation.interop import digest_thread  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
THREAD = "1529000000000000710"
_DAY = datetime(2026, 10, 30, tzinfo=KST)


def _schedule(job: str) -> str:
    for line in (_REPO / "skills" / "mail" / "deploy-manifest.txt").read_text(encoding="utf-8").splitlines():
        if f"|{job}|" in line:
            return line.split("schedule=", 1)[1].split(";", 1)[0]
    raise AssertionError(f"{job} is not declared")


def test_the_digest_timer_fires_at_09_13_19_kst_which_is_00_04_10_utc() -> None:
    calendar = on_calendar(_schedule("mail-daily-digest"), "Asia/Seoul")
    assert calendar == "*-*-* 09,13,19:00:00 Asia/Seoul"
    hours = sorted(_DAY.replace(hour=hour).astimezone(UTC).hour for hour in (9, 13, 19))
    assert hours == [0, 4, 10]


def test_the_failure_check_follows_every_digest_run() -> None:
    assert on_calendar(_schedule("mail-triage-repair-daily"), "Asia/Seoul") == "*-*-* 09,13,19:40:00 Asia/Seoul"


class _Clock(datetime):
    """Stands in for ``datetime`` inside triage_digest so a run happens at a chosen KST time."""

    at = _DAY

    @classmethod
    def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
        return cls.at if tz is None else cls.at.astimezone(tz)  # type: ignore[arg-type]


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A digest with fake mail, fake Discord and the real store, thread state and selection."""
    monkeypatch.setenv(digest_thread.STATE_ENV, str(tmp_path / "digest-threads"))
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    state = SimpleNamespace(inbox=[], posts=[], flat=[], opened=[], handed=[])

    monkeypatch.setattr(triage_digest, "datetime", _Clock)
    monkeypatch.setattr(triage_core, "utc_now", lambda: _Clock.at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    monkeypatch.setattr(triage_transport, "_list_mails", lambda _limit, _sync: list(state.inbox))
    monkeypatch.setattr(triage_transport, "_get_mail", lambda uid: {"uid": uid, "body": "합성 본문"})
    monkeypatch.setattr(triage_transport, "_sent_mails", lambda _limit: [])

    def build(detail: dict, item_no: int, *, digest_day: str = "") -> tuple[dict, dict]:
        uid = str(detail["uid"])
        shared = {"item_no": item_no, "uid": uid, "sender_masked": "h", "category": "important",
                  "note": "", "recv_date": str(detail["date"])}
        dm = {**shared, "subject": detail["subject"], "sender": "", "cc": "", "summary": "요약",
              "flags": ("schedule_needed",), "replied_at": "", "calendar_card": "", "calendar_when": "",
              "calendar_title": "", "schedule_text": f"{uid} 일정"}
        return dm, {**shared, "subject": detail["subject"], "summary": "요약", "flags": "schedule_needed"}

    monkeypatch.setattr(triage_digest, "build_item", build)

    def delegate(text: str, _uid: str, day: str, *, reply_to: str, reply_channel: str):
        state.handed.append((text, day, reply_channel))
        return triage_transport.ScheduleNote("")

    monkeypatch.setattr(triage_transport, "_delegate_schedule", delegate)
    monkeypatch.setattr(triage_confirm, "post_in",
                        lambda channel, body: state.posts.append((channel, body)) or f"m-{len(state.posts)}")
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: state.flat.append(body) or f"f-{len(state.flat)}")
    monkeypatch.setattr(triage_digest, "_attach_reactions", lambda *_a: None)

    surface = SimpleNamespace(
        RequestThread=lambda title, label, icon: SimpleNamespace(title=title, label=label, icon=icon),
        ApprovalKind=SimpleNamespace(MAIL_REPLY="mail-reply"),
        request_thread_name=lambda _kind, request: f"{request.icon} {request.label} · {request.title}",
    )

    def resolve(_kind: object, _directory: object, _owner: object, *, request: object) -> object:
        state.opened.append(request.title)  # type: ignore[attr-defined]
        return SimpleNamespace(channel_id=THREAD, surface="agent-chat-thread", policy_version=8, guild_id="1")

    surface.resolve_new_binding = resolve
    modules = {"approval_surface": surface, "digest_thread": digest_thread}
    monkeypatch.setattr(triage_approval, "_repo_module", lambda name: modules[name])
    monkeypatch.setattr(triage_binding, "approval_directory", object)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "100000000000000001")
    monkeypatch.setattr(triage_digest_thread, "settle", lambda: None)
    return state


def _mail(uid: str, at: datetime) -> dict:
    return {"uid": uid, "subject": f"합성 {uid}", "date": at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _run(node: SimpleNamespace, hour: int, day: datetime = _DAY) -> list[tuple[str, str]]:
    _Clock.at = day.replace(hour=hour)
    before = len(node.posts)
    assert triage_digest.run_digest(limit=20, sync=False, dry_run=False) == 0
    return node.posts[before:]


def _cards(posts: list[tuple[str, str]]) -> list[str]:
    return [body.splitlines()[0] for _channel, body in posts[1:]]


def test_each_run_shows_only_the_mail_no_earlier_run_showed_and_hands_its_event_over_once(
    node: SimpleNamespace,
) -> None:
    node.inbox = [_mail("u-1", _DAY.replace(hour=7)), _mail("u-2", _DAY.replace(hour=8))]
    first = _run(node, 9)
    node.inbox.append(_mail("u-3", _DAY.replace(hour=11)))
    second = _run(node, 13)
    node.inbox.append(_mail("u-4", _DAY.replace(hour=15)))
    third = _run(node, 19)

    assert _cards(first) == ["### 1. 합성 u-1", "### 2. 합성 u-2"]
    assert _cards(second) == ["### 1. 합성 u-3"]
    assert _cards(third) == ["### 1. 합성 u-4"]
    assert [text for text, _day, _channel in node.handed] == ["u-1 일정", "u-2 일정", "u-3 일정", "u-4 일정"]


def test_the_days_runs_share_one_thread_named_by_the_day_and_each_run_heads_its_own_post(
    node: SimpleNamespace,
) -> None:
    node.inbox = [_mail("u-1", _DAY.replace(hour=7))]
    first = _run(node, 9)
    node.inbox.append(_mail("u-2", _DAY.replace(hour=12)))
    second = _run(node, 13)

    assert node.opened == ["다이제스트 10-30"]
    assert {channel for channel, _ in first + second} == {THREAD}
    assert first[0][1].splitlines()[0] == "## 📬 09:00 다이제스트 · 신규 1건 (일정 1)"
    assert second[0][1].splitlines()[0] == "## 📬 13:00 다이제스트 · 신규 1건 (일정 1)"
    assert node.flat == []


def test_a_later_empty_run_stays_silent_but_the_days_first_empty_run_still_says_so(
    node: SimpleNamespace,
) -> None:
    node.inbox = [_mail("u-1", _DAY.replace(hour=7))]
    _run(node, 9)
    assert _run(node, 13) == [] and node.flat == []

    next_day = _DAY + timedelta(days=1)
    assert _run(node, 9, next_day) == []
    assert len(node.flat) == 1 and node.flat[0].splitlines()[-1] == "신규 메일 없음"
    assert _run(node, 13, next_day) == [] and len(node.flat) == 1
