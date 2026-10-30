"""YooDori sees To/Cc/Bcc only when it opens one mail (owner Q393, 2026-10-30).

"메일 열어 볼 때만 보이게, 요약·알림·디스코드·기록엔 계속 가림": `mail-get` / `gmail-get` return the
recipient addresses as written; `mail-list`, `gmail-list`, `mail-search` snippets, the gate log and the
forward/reply quote do not gain them. The vendored MailOn now keeps the Bcc of a sent mail (the webmail
view shows `bcc-item-N` rows to the sender) and the sync fills it in for sent mails saved before.
"""
from __future__ import annotations

import importlib.util
import json
import shlex
import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE.parents[1] / "skills" / "mail" / "vendor"
_SCRIPTS = _HERE.parents[1] / "skills" / "mail" / "scripts"
for path in (_SCRIPTS, _HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
# Load only the vendored `mailon` package: putting vendor/ on sys.path would let its regular
# `tests` package shadow the repo's namespace `tests` (test_yoodori_gmail imports tests.unit).
if "mailon" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "mailon", _VENDOR / "mailon" / "__init__.py", submodule_search_locations=[str(_VENDOR / "mailon")])
    assert _spec is not None and _spec.loader is not None
    sys.modules["mailon"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["mailon"])
# The view parser needs bs4+lxml, which the CI image does not carry (same stubs as
# test_mailon_large_box_and_buttons.py); the parser cases run where they are installed.
HAVE_PARSER = all(importlib.util.find_spec(name) is not None for name in ("bs4", "lxml"))
needs_parser = pytest.mark.skipif(not HAVE_PARSER, reason="bs4/lxml not installed")
for name in ("pyotp", "dotenv", "bs4"):
    if name not in sys.modules and importlib.util.find_spec(name) is None:
        stub = types.ModuleType(name)
        stub.TOTP = stub.load_dotenv = stub.BeautifulSoup = object  # type: ignore[attr-defined]
        sys.modules[name] = stub

import mail_quote  # noqa: E402
from automation.yoodori_gate import gmail_read as gmail  # noqa: E402
from automation.yoodori_gate import yoodori_read_gate as gate  # noqa: E402
from mailon import heal  # noqa: E402
from mailon.scraper import parse_view_async_html  # noqa: E402
from mailon.state import StateDB  # noqa: E402
from mailon.writer import Mail, _yaml_escape, build_markdown, write_mail  # noqa: E402
from test_mail_wrapper import run_cli, stub_repo  # noqa: E402,F401

TO = "to-person@example.invalid"
CC = "cc-person@example.invalid"
BCC = ("hidden-one@example.invalid", "hidden-two@example.invalid")
ADDRESSES = (TO, CC, *BCC)
CC_FIELD = f'"참조" <{CC}>'
BCC_FIELD = f'"하나" <{BCC[0]}>, {BCC[1]}'

VIEW_HTML = f"""<input type="hidden" id="timeMillis" value="1793336974000" />
<div id="mvw_wrap"><div class="hd"><h4>세미나 안내</h4></div>
<li id="from-item-0" data-address="owner@example.invalid" data-personal="형" />
<li id="to-item-0" data-address="{TO}" data-personal="" />
<li id="cc-item-0" data-address="{CC}" data-personal="참조" />
<dl class="adr-wrap" data-field="bcc"><p>Bcc</p><ul>
<li id="bcc-item-0" data-address="{BCC[0]}" data-personal="하나" />
<li id="bcc-item-1" data-address="{BCC[1]}" data-personal="" />
</ul></dl>
<div class="bd"><div class="ct">본문입니다</div></div></div>"""


def _sent_mail(uid: str = "u-101", folder: str = "sent") -> Mail:
    return Mail(uid=uid, folder=folder, subject="세미나 안내", sender='"형" <owner@example.invalid>', to=TO,
                cc=CC_FIELD, bcc=BCC_FIELD, date=datetime(2026, 10, 30, 9, 0), body_text="본문입니다")


def _store(repo: Path, mail: Mail, rel: str = "data/mails/2026/07/f1.md") -> Path:
    path = write_mail(mail, repo / "data" / "mails", repo)
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    path.rename(target)
    return target


@needs_parser
def test_view_parser_reads_every_bcc_row() -> None:
    parsed = parse_view_async_html(VIEW_HTML)
    assert (parsed["to"], parsed["cc"], parsed["bcc"]) == (TO, CC_FIELD, BCC_FIELD)


def test_writer_keeps_bcc_in_front_matter_only() -> None:
    # Given / When
    md = build_markdown(_sent_mail(), Path("/repo"), Path("/repo/data/mails/x.md"))
    front, _, body = md.partition("\n---\n")
    # Then: stored once in the front matter, never in the rendered header/body
    assert f"bcc: {_yaml_escape(BCC_FIELD)}" in front.splitlines()
    assert not any(address in body for address in BCC)


def test_mail_get_returns_to_cc_and_bcc_verbatim(stub_repo: Path) -> None:  # noqa: F811
    # Given: the sent mail as the vendored MailOn writes it
    _store(stub_repo, _sent_mail())
    # When
    rc, out = run_cli("get", "u-101")
    # Then
    assert rc == 0
    assert out["mail"]["to"] == TO
    assert out["mail"]["cc"] == CC_FIELD
    assert out["mail"]["bcc"] == BCC_FIELD


def test_mail_get_marks_a_not_yet_filled_bcc_as_null(stub_repo: Path) -> None:  # noqa: F811
    # Given: a mail saved before the writer kept Bcc (no `bcc:` line)
    md = _store(stub_repo, _sent_mail())
    md.write_text("\n".join(line for line in md.read_text().splitlines() if not line.startswith("bcc:")))
    # When
    rc, out = run_cli("get", "u-101")
    # Then
    assert rc == 0 and out["mail"]["to"] == TO and out["mail"]["bcc"] is None


def test_mail_get_without_a_stored_file_has_empty_recipients(stub_repo: Path) -> None:  # noqa: F811
    rc, out = run_cli("get", "u-102")
    assert rc == 0 and (out["mail"]["to"], out["mail"]["cc"], out["mail"]["bcc"]) == ("", "", None)


def test_masked_get_masks_every_recipient(stub_repo: Path) -> None:  # noqa: F811
    _store(stub_repo, _sent_mail())
    rc, out = run_cli("get", "u-101", "--masked")
    text = json.dumps(out, ensure_ascii=False)
    assert rc == 0 and not any(address in text for address in ADDRESSES)
    assert all(out["mail"][key].startswith("sha256:") for key in ("to", "cc", "bcc"))


def test_list_and_search_do_not_gain_recipients(stub_repo: Path) -> None:  # noqa: F811
    # Given
    _store(stub_repo, _sent_mail())
    # When
    _, listed = run_cli("list", "--limit", "5")
    _, by_bcc = run_cli("search", "--query", BCC[0])
    _, by_body = run_cli("search", "--query", "본문입니다")
    # Then: list rows keep their old fields; the Bcc is neither searchable nor in a snippet
    assert all(not {"to", "cc", "bcc"} & set(row) for row in listed["mails"])
    assert by_bcc["count"] == 0
    assert by_body["count"] == 1
    assert not any(address in json.dumps(out, ensure_ascii=False)
                   for out in (listed, by_bcc, by_body) for address in BCC)


def test_forward_and_reply_quote_never_carry_bcc(stub_repo: Path) -> None:  # noqa: F811
    md = _store(stub_repo, _sent_mail())
    detail = {"subject": "세미나 안내", "sender": "owner@example.invalid", "date": "2026-10-30T09:00:00",
              "body": md.read_text(encoding="utf-8")}
    quote = mail_quote.render_quote(mail_quote.parse_original(detail))
    assert TO in quote and not any(address in quote for address in BCC)


class _FakeScraper:
    def __init__(self, fail: frozenset[str] = frozenset()) -> None:
        self.fetched: list[tuple[str, str]] = []
        self.fail = fail

    def fetch_mail_html(self, uid: str, folder_uid: str) -> str:
        self.fetched.append((uid, folder_uid))
        if uid in self.fail:
            raise RuntimeError("view_async failed")
        return VIEW_HTML


@pytest.fixture()
def view_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heal, "parse_view_async_html", lambda html: {"bcc": BCC_FIELD if html == VIEW_HTML else ""})


def _db_with(tmp_path: Path, rows: list[tuple[str, str, str]]) -> tuple[StateDB, Path]:
    root = tmp_path / "root"
    db = StateDB(root / "data" / "state.db")
    for uid, folder, recv in rows:
        mail = _sent_mail(uid, folder)
        mail.bcc = ""
        path = write_mail(mail, root / "data" / "mails", root)
        text = "\n".join(line for line in path.read_text().splitlines() if not line.startswith("bcc:"))
        path.write_text(text, encoding="utf-8")
        db.record_message(uid=uid, folder=folder, subject="s", sender="f", recv_date=recv,
                          markdown_path=str(path.relative_to(root)))
    return db, root


def _md(db: StateDB, root: Path, uid: str) -> str:
    return (root / db.message_row(uid)["markdown_path"]).read_text(encoding="utf-8")


def test_sync_backfills_bcc_of_old_sent_mails_once(tmp_path: Path, view_parser: None) -> None:
    # Given: two old sent mails without a bcc line and one inbox mail
    db, root = _db_with(tmp_path, [("s1", "sent", "2026-10-28T10:00:00"), ("s2", "sent", "2026-10-29T10:00:00"),
                                   ("i1", "inbox", "2026-10-29T11:00:00")])
    scraper = _FakeScraper()
    # When
    filled = heal.backfill_bcc(db, scraper, root, folder_uids={"sent": "77"})
    again = heal.backfill_bcc(db, scraper, root, folder_uids={"sent": "77"})
    # Then: only the sent folder is read, newest first, once each
    assert (filled, again) == (2, 0)
    assert scraper.fetched == [("s2", "77"), ("s1", "77")]
    assert all(address in _md(db, root, "s1") for address in BCC)
    assert "bcc:" not in _md(db, root, "i1")


def test_bcc_backfill_waits_for_the_sent_folder_and_retries_a_failure(tmp_path: Path, view_parser: None) -> None:
    db, root = _db_with(tmp_path, [("s1", "sent", "2026-10-28T10:00:00")])
    before = _md(db, root, "s1")
    assert heal.backfill_bcc(db, _FakeScraper(), root, folder_uids={}) == 0
    assert heal.backfill_bcc(db, _FakeScraper(frozenset({"s1"})), root, folder_uids={"sent": "77"}) == 0
    assert _md(db, root, "s1") == before
    assert heal.backfill_bcc(db, _FakeScraper(), root, folder_uids={"sent": "77"}) == 1


def test_bcc_backfill_is_capped_per_run(tmp_path: Path, view_parser: None) -> None:
    rows = [(f"s{i}", "sent", f"2026-10-{i + 1:02d}T10:00:00") for i in range(heal.BCC_BACKFILL_MAX_PER_RUN + 2)]
    db, root = _db_with(tmp_path, rows)
    assert heal.backfill_bcc(db, _FakeScraper(), root, folder_uids={"sent": "77"}) == heal.BCC_BACKFILL_MAX_PER_RUN


@needs_parser
def test_bcc_backfill_with_the_real_view_parser(tmp_path: Path) -> None:
    db, root = _db_with(tmp_path, [("s1", "sent", "2026-10-28T10:00:00")])
    assert heal.backfill_bcc(db, _FakeScraper(), root, folder_uids={"sent": "77"}) == 1
    assert f"bcc: {_yaml_escape(BCC_FIELD)}" in _md(db, root, "s1").splitlines()


def _gmail_message(mid: str) -> dict[str, gmail.Json]:
    return {"id": mid, "snippet": "요약", "payload": {"mimeType": "text/plain", "headers": [
        {"name": "From", "value": "reader@example.invalid"}, {"name": "To", "value": TO},
        {"name": "Cc", "value": CC}, {"name": "Bcc", "value": ", ".join(BCC)},
        {"name": "Subject", "value": "제목"}, {"name": "Date", "value": "Fri, 30 Oct 2026"},
    ], "body": {"data": ""}}}


@pytest.mark.parametrize("words", [["get", "abcdef0123456789"], ["list", "2"]])
def test_gmail_get_shows_recipients_and_list_does_not(
    words: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        params = json.loads(argv[6])
        response = ({"messages": [{"id": "abcdef0123456789"}]} if argv[4] == "list"
                    else _gmail_message(params["id"]))
        return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")
    monkeypatch.setattr(gmail.subprocess, "run", run)
    assert gmail.main(words) == 0
    out = json.loads(capsys.readouterr().out)
    if words[0] == "get":
        assert (out["mail"]["to"], out["mail"]["cc"], out["mail"]["bcc"]) == (TO, CC, ", ".join(BCC))
    else:
        assert not any(address in json.dumps(out) for address in ADDRESSES)


@pytest.mark.parametrize("command", [["mail-get", "9939935"], ["mail-get", "9939935", "--body"],
                                     ["gmail-get", "abcdef0123456789"], ["mail-list", "5"]])
def test_gate_log_never_holds_an_address(
    command: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the read child prints recipients, as mail-get now does
    log = tmp_path / "yoodori-gate.log"
    monkeypatch.setattr(gate, "LOG", log)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", shlex.join(command))
    def child(*_args: object, **_kwargs: object) -> SimpleNamespace:
        print(json.dumps({"mail": {"to": TO, "cc": CC, "bcc": ", ".join(BCC)}}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(gate.subprocess, "run", child)
    # When
    assert gate.main() == 0
    # Then: YooDori got the addresses, the node log only the command name and rc
    assert TO in capsys.readouterr().out
    text = log.read_text(encoding="utf-8")
    stamp, name, rc, seconds = text.split()
    assert "@" not in text and (name, rc) == (command[0], "rc=0") and seconds.startswith("s=")
