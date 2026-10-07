"""mailon 목록 순회 회귀 — 이미 저장된 메일만 있는 쪽에서 순회를 멈춘다.

2026-10-06 노드 실측: 받은메일함이 9366건(약 469쪽)으로 늘고 서버가 쪽당 약 2.3초라,
동기화가 목록 전체(상한 500쪽)를 넘기다 래퍼 제한 900초에 끊겼다(신규 0건 저장). 목록은
최신순이므로 한 쪽이 전부 이미 저장된 메일이면 그 뒤는 더 오래된 메일이다. 전체 순회는
`--full-scan` 으로 남는다. 새 검사라 기존 mailon 시험과 파일을 나눈다.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"


def _install_third_party_stubs() -> None:
    for name in ("pyotp", "dotenv", "bs4"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.TOTP = stub.load_dotenv = stub.BeautifulSoup = object  # type: ignore[attr-defined]
            sys.modules[name] = stub


_install_third_party_stubs()
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon.scraper import InboxScraper  # noqa: E402


def _page(start: int, size: int = 20) -> dict:
    return {
        "result": True,
        "folder": {"newMsgNum": 0},
        "contents": [{"mailUid": str(uid), "folderUid": 49527, "timeMillis": 0}
                     for uid in range(start, start - size, -1)],
    }


class _Scraper(InboxScraper):
    def __init__(self, pages: int) -> None:
        super().__init__(None, Path("/nonexistent"))  # type: ignore[arg-type]
        self.folder_uid = "49527"
        self.pages = pages
        self.fetched: list[int] = []

    def fetch_list_page(self, page: int) -> dict:
        self.fetched.append(page)
        if page > self.pages:
            return {"result": True, "contents": []}
        return _page(10_000 - (page - 1) * 20)


def _uids(first_page: int, last_page: int) -> frozenset[str]:
    return frozenset(str(uid) for page in range(first_page, last_page + 1)
                     for uid in range(10_000 - (page - 1) * 20, 10_000 - page * 20, -1))


def test_the_walk_stops_at_the_first_page_of_already_saved_mails() -> None:
    scraper = _Scraper(pages=400)

    refs = scraper.list_inbox(known=_uids(3, 400))

    assert scraper.fetched == [1, 2, 3]
    assert len(refs) == 60


def test_a_page_with_one_unsaved_mail_keeps_the_walk_going() -> None:
    scraper = _Scraper(pages=6)
    known = _uids(1, 6) - {str(10_000 - 5), str(10_000 - 20 - 5)}

    scraper.list_inbox(known=known)

    assert scraper.fetched == [1, 2, 3]


def test_full_scan_walks_every_page() -> None:
    scraper = _Scraper(pages=6)
    skip = set(_uids(1, 6))

    assert list(scraper.iter_new_mails(skip, full_scan=True)) == []
    assert scraper.fetched == [1, 2, 3, 4, 5, 6, 7]


def test_a_normal_sync_passes_the_saved_uids_to_the_walk() -> None:
    scraper = _Scraper(pages=6)

    assert list(scraper.iter_new_mails(set(_uids(1, 6)))) == []
    assert scraper.fetched == [1]
