"""mailon 받은메일함 folderUid 해석 회귀 — 목록 행이 끝내 그려지지 않아도 동기화를 잇는다.

2026-10-06 노드 실측: 로그인 뒤 `/mail` 이 `readyState=interactive` 에 머물며 사이드바
(Inbox 3)는 그렸지만 목록 행 `a.mail-metadata` 는 90초 내내 0개였고, 동기화가
`could not resolve inbox folderUid from DOM` 으로 죽었다. 같은 세션의 목록 API 는 그
folderUid 로 정상 응답했다. 행이 없을 때만 사이드바 이름 옆 folderUid 를 쓰되, 목록 API 가
그 폴더임을 확인할 때만 받아들인다.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"


def _install_third_party_stubs() -> None:
    if "pyotp" not in sys.modules:
        pyotp = types.ModuleType("pyotp")
        pyotp.TOTP = lambda *_a, **_k: None  # type: ignore[attr-defined]
        sys.modules["pyotp"] = pyotp
    if "dotenv" not in sys.modules:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *_a, **_k: None  # type: ignore[attr-defined]
        sys.modules["dotenv"] = dotenv
    if "bs4" not in sys.modules:
        try:
            __import__("bs4")
        except ImportError:
            bs4 = types.ModuleType("bs4")
            bs4.BeautifulSoup = object  # type: ignore[attr-defined]
            sys.modules["bs4"] = bs4


_install_third_party_stubs()
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon.browser import BrowserError  # noqa: E402
from mailon.scraper import InboxScraper  # noqa: E402


class _Browser:
    def __init__(self, *, row_uid: str | None, label_uid: str | None, api: dict) -> None:
        self.row_uid = row_uid
        self.label_uid = label_uid
        self.api = api
        self.api_bodies: list[str] = []
        self.harvests = 0

    def find_click(self, *_args, **_kwargs) -> None:
        raise BrowserError("no sidebar link")

    def wait_ms(self, _ms: int) -> None:
        return None

    def eval_js(self, js: str) -> str:
        if "a.mail-metadata" in js:
            return json.dumps(self.row_uid)
        if "const NAME" in js:
            self.harvests += 1
            return json.dumps(self.label_uid)
        if "list_async" in js:
            self.api_bodies.append(js)
            return json.dumps(json.dumps(self.api))
        raise AssertionError(f"unexpected script: {js[:80]}")


def _scraper(browser: _Browser) -> InboxScraper:
    return InboxScraper(browser, Path("/nonexistent"), all_folders=True)  # type: ignore[arg-type]


def test_rows_never_render_but_the_api_confirms_the_inbox_label_uid() -> None:
    browser = _Browser(row_uid=None, label_uid="49527",
                       api={"result": True, "folder": {"folderUid": 49527}, "contents": []})
    scraper = _scraper(browser)

    assert scraper.resolve_inbox_folder_uid(timeout_s=0) == "49527"
    assert scraper.folder_uid == "49527"
    assert scraper.all_folders is True
    assert "allFolder=false" in browser.api_bodies[0]


@pytest.mark.parametrize("api", [
    {"result": False},
    {"result": True, "folder": {"folderUid": 49543}},
])
def test_a_label_uid_the_api_does_not_confirm_still_fails(api: dict) -> None:
    scraper = _scraper(_Browser(row_uid=None, label_uid="49527", api=api))

    with pytest.raises(RuntimeError, match="could not resolve inbox folderUid"):
        scraper.resolve_inbox_folder_uid(timeout_s=0)
    assert scraper.folder_uid is None


def test_rendered_rows_win_without_consulting_the_sidebar() -> None:
    browser = _Browser(row_uid="49527", label_uid="11111", api={})

    assert _scraper(browser).resolve_inbox_folder_uid(timeout_s=0) == "49527"
    assert browser.harvests == 0
    assert browser.api_bodies == []
