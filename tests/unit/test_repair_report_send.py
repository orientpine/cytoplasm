from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from urllib.error import HTTPError

import pytest

from automation.interop.discord_transport import SentMessage
from automation.interop.report import ReportStatus, TaskReport, format_report, parse_report
from automation.repair import repair_report_send as subject
from automation.repair.repair_report_queue import REASON_CODES, ReportRequest


TIMESTAMP = datetime(2026, 8, 7, 1, 2, 3, 456789, tzinfo=UTC)
FULL_ID = "123456789012345678"


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("real network access")

    monkeypatch.setattr(subject, "urlopen", forbidden)
    monkeypatch.setattr(subject, "_bot_user_id_cache", None)


def request(*, operation: str = "complete", reason: str = "applied") -> ReportRequest:
    return ReportRequest("a" * 32, operation, "t_report", reason, "1", "b" * 64, TIMESTAMP.isoformat())


def report_content(*, task_id: str = "t_report", status: ReportStatus = ReportStatus.DONE,
                   timestamp: datetime = TIMESTAMP) -> str:
    return format_report(TaskReport("agent-test", task_id, status, "완료", (), timestamp))


def message(message_id: str, *, author: str = "999", content: str | None = None) -> dict:
    return {"id": message_id, "author": {"id": author}, "content": content or report_content()}


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subject, "load_config", lambda: {
        "agent_id": "agent-test", "agents_log_channel_id": "channel-test",
    })


@pytest.mark.parametrize(
    ("reason", "phrase"),
    [
        ("applied", "수리 패치를 적용하고 회귀 검증을 통과했습니다"),
        ("sandbox_rejected", "샌드박스 검증에서 거절되어 패치를 적용하지 않았습니다"),
        ("bank_red", "회귀 뱅크 상태가 red여서 패치를 적용하지 않았습니다"),
        ("bank_failed_reverted", "회귀 뱅크 실패로 패치를 되돌렸습니다"),
        ("owner_cancelled", "소유자가 승인을 취소했습니다"),
        ("approval_expired", "승인 대기가 만료되었습니다"),
        ("unspecified", "자동 수리가 중단되어 티켓을 다시 열었습니다"),
    ],
)
def test_send_report_uses_fixed_safe_phrase_and_round_trips_timestamp(
    monkeypatch: pytest.MonkeyPatch, reason: str, phrase: str,
) -> None:
    configure(monkeypatch)
    captured: list[str] = []

    @dataclass(frozen=True, slots=True)
    class Transport:
        def send(self, body: str) -> tuple[SentMessage, ...]:
            captured.append(body)
            return (SentMessage(FULL_ID),)

    operation = "complete" if reason == "applied" else "reopen"
    suffix = subject.send_report(request(operation=operation, reason=reason), TIMESTAMP, transport=Transport())
    parsed = parse_report(captured[0])

    assert set(subject.PHRASE) == REASON_CODES
    assert parsed is not None
    assert parsed.summary == phrase
    assert parsed.status is (ReportStatus.DONE if operation == "complete" else ReportStatus.BLOCKED)
    assert parsed.timestamp.isoformat() == TIMESTAMP.isoformat()
    assert not any(token in captured[0] for token in (".py", "@", "sk-", "ghp_", FULL_ID))
    assert suffix == FULL_ID[-4:]


def test_send_report_propagates_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)

    class BrokenTransport:
        def send(self, body: str) -> tuple[SentMessage, ...]:
            raise RuntimeError("send failed")

    with pytest.raises(RuntimeError, match="send failed"):
        subject.send_report(request(), TIMESTAMP, transport=BrokenTransport())


def test_send_report_direct_sender_returns_only_suffix(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    configure(monkeypatch)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self) -> bytes: return json.dumps({"id": FULL_ID}).encode()

    calls: list[int] = []
    monkeypatch.setattr(subject, "urlopen", lambda *args, **kwargs: calls.append(1) or Response())

    assert subject.send_report(request(), TIMESTAMP) == "5678"
    assert len(calls) == 1
    output = capsys.readouterr()
    assert FULL_ID not in output.out + output.err


@pytest.mark.parametrize("payload", [{}, {"agent_id": "agent"}, {"agents_log_channel_id": "channel"}])
def test_load_config_rejects_missing_fields(monkeypatch: pytest.MonkeyPatch, tmp_path, payload: dict) -> None:
    path = tmp_path / "interop.json"
    if payload:
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(path))

    with pytest.raises((subject.InteropConfigError, FileNotFoundError), match="interop|No such file"):
        subject.load_config()


def test_load_config_reads_only_required_identifiers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    path = tmp_path / "interop.json"
    path.write_text(json.dumps({"agent_id": "agent", "agents_log_channel_id": "channel", "ignored": 3}))
    monkeypatch.setenv("INTEROP_CONFIG", str(path))

    assert subject.load_config() == {"agent_id": "agent", "agents_log_channel_id": "channel"}


def test_bot_user_id_fetches_once_then_uses_cache() -> None:
    calls: list[str] = []

    def fetcher(path: str):
        calls.append(path)
        return {"id": "999"}

    assert subject.bot_user_id(fetcher=fetcher) == "999"
    assert subject.bot_user_id(fetcher=fetcher) == "999"
    assert calls == ["/users/@me"]


def test_find_report_includes_message_at_exclusive_upper_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    calls: list[str] = []

    def fetcher(path: str):
        calls.append(path)
        if path == "/users/@me":
            return {"id": "999"}
        before = int(path.split("before=", 1)[1].split("&", 1)[0])
        return [message("200")] if before > 200 else []

    found, cursor, exhausted = subject.find_report(
        task_id="t_report", status=ReportStatus.DONE, timestamp_iso=TIMESTAMP.isoformat(),
        upper="200", lower="100", cursor=None, fetcher=fetcher,
    )

    assert (found, cursor, exhausted) == (True, "200", True)
    assert "before=201" in calls[0]


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (message("200", author="other-bot"), False),
        (message("200", content=report_content(timestamp=datetime(2026, 8, 7, tzinfo=UTC))), False),
        (message("100"), False),
        ({"id": "200", "author": {"id": "999"}, "content": "invalid"}, False),
    ],
)
def test_find_report_rejects_wrong_identity_or_out_of_window(
    monkeypatch: pytest.MonkeyPatch, candidate: dict, expected: bool,
) -> None:
    configure(monkeypatch)

    def fetcher(path: str):
        if path == "/users/@me":
            return {"id": "999"}
        return [candidate] if "before=201" in path else []

    found, _, _ = subject.find_report(
        task_id="t_report", status=ReportStatus.DONE, timestamp_iso=TIMESTAMP.isoformat(),
        upper="200", lower="100", cursor=None, fetcher=fetcher,
    )
    assert found is expected


def test_find_report_descends_newest_to_oldest_until_lower_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    messages = [
        message(str(value), content=report_content(task_id="t_other"))
        for value in (500, 400, 300, 201)
    ] + [message("101")]

    def fetcher(path: str):
        if path == "/users/@me":
            return {"id": "999"}
        before = int(path.split("before=", 1)[1].split("&", 1)[0])
        page = [item for item in messages if int(item["id"]) < before][:2]
        return page

    found, cursor, exhausted = subject.find_report(
        task_id="t_report", status=ReportStatus.DONE, timestamp_iso=TIMESTAMP.isoformat(),
        upper="500", lower="100", cursor=None, fetcher=fetcher,
    )

    assert found is True
    assert cursor == "101"
    assert exhausted is True


def test_iter_window_pauses_at_max_pages_and_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    values = list(range(300, 239, -1))

    def fetcher(path: str):
        before = int(path.split("before=", 1)[1].split("&", 1)[0])
        return [{"id": str(value)} for value in values if value < before][:1]

    first, cursor, exhausted = subject._iter_window(
        upper="300", lower="200", cursor=None, max_pages=50, fetcher=fetcher,
    )
    second, next_cursor, next_exhausted = subject._iter_window(
        upper="300", lower="200", cursor=cursor, max_pages=50, fetcher=fetcher,
    )

    assert (len(first), cursor, exhausted) == (50, "251", False)
    assert (second[0]["id"], next_cursor, next_exhausted) == ("250", "240", True)


def test_iter_window_empty_page_is_exhausted_before_cursor_reaches_lower(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)

    items, cursor, exhausted = subject._iter_window(
        upper="500", lower="100", cursor="400", fetcher=lambda path: [],
    )

    assert (items, cursor, exhausted) == ([], "400", True)


def test_find_report_fetch_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)

    with pytest.raises(OSError, match="fetch failed"):
        subject.find_report(
            task_id="t_report", status=ReportStatus.DONE, timestamp_iso=TIMESTAMP.isoformat(),
            upper="200", lower="100", cursor=None,
            fetcher=lambda path: (_ for _ in ()).throw(OSError("fetch failed")),
        )


def test_find_any_report_ignores_timestamp_but_checks_author(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    old = report_content(timestamp=datetime(2025, 1, 1, tzinfo=UTC))

    def fetcher(path: str):
        if path == "/users/@me":
            return {"id": "999"}
        if path.endswith("messages?limit=1"):
            return [message("200", content=old)]
        return [message("200", content=old)] if "before=201" in path else []

    assert subject.find_any_report(
        task_id="t_report", status=ReportStatus.DONE, lower="0", fetcher=fetcher,
    ) is True

    subject._bot_user_id_cache = None
    assert subject.find_any_report(
        task_id="t_report", status=ReportStatus.DONE, lower="0",
        fetcher=lambda path: {"id": "999"} if path == "/users/@me" else (
            [message("200", author="other-bot")] if path.endswith("messages?limit=1") else (
                [message("200", author="other-bot")] if "before=201" in path else []
            )
        ),
    ) is False


@pytest.mark.parametrize(("page", "expected"), [([], "0"), ([{"id": "987"}], "987")])
def test_channel_watermark_normalizes_empty_and_returns_latest(
    monkeypatch: pytest.MonkeyPatch, page: list[dict], expected: str,
) -> None:
    configure(monkeypatch)
    assert subject.channel_watermark(fetcher=lambda path: page) == expected


def test_empty_channel_lower_zero_lookup_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    calls: list[str] = []

    def fetcher(path: str):
        calls.append(path)
        return {"id": "999"} if path == "/users/@me" else []
    lower = subject.channel_watermark(fetcher=fetcher)

    assert subject.find_any_report(
        task_id="t_report", status=ReportStatus.DONE, lower=lower, fetcher=fetcher,
    ) is False


def test_direct_sender_budget_caps_real_attempts_at_twenty(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(subject.time, "sleep", lambda seconds: None)
    headers = Message()
    headers["Retry-After"] = "0"
    opener_calls = 0
    budget_calls = 0

    def opener(*args, **kwargs):
        nonlocal opener_calls
        opener_calls += 1
        raise HTTPError("https://discord.invalid", 429, "limited", headers, None)

    def budget() -> None:
        nonlocal budget_calls
        if budget_calls >= 20:
            raise RuntimeError("budget exhausted")
        budget_calls += 1

    monkeypatch.setattr(subject, "urlopen", opener)

    with pytest.raises(RuntimeError, match="budget exhausted"):
        subject.send_report(request(), TIMESTAMP, budget=budget)

    assert budget_calls == 20
    assert opener_calls == 20
