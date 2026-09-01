"""스트릭 상태를 **적지 못했을 때** 워처가 그 사실을 돌려받는가.

`store()` 가 OSError 를 삼키면 카운터가 임계치 아래에 얼어붙는데 `record()` 는 정상
반환해 워처는 "기록됨"으로 보고 exit 0 을 낸다 — 배너도 없다. 즉 상태 루트가 쓰기
불가인 노드에서는 사고가 영원히 침묵할 수 있다(follow-ups 2026-08-24).

**왜 별도 파일인가**: `tests/unit/test_watch_failure_streak.py` 의 출력 해시는 FS3 정산
레코드 `ea32cae9…`(`.omo/evidence/fs3/completions/task-5.json`)에 고정돼 있고,
`tests/unit/test_fs3_replay_gate.py` 가 그 재생을 매번 대조한다. 그 파일에 케이스를
더하면 과거 RED/GREEN 증적이 재현되지 않는다 — 원장을 고쳐 맞추는 것은 증적 위조이므로,
새 검사는 고정되지 않은 이 파일에 둔다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SOURCE = _REPO / "skills" / "mail" / "scripts" / "watch_failure_streak.py"


def _module():
    spec = importlib.util.spec_from_file_location("watch_failure_streak_store", _SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def streak(tmp_path: Path):
    return _module(), tmp_path


def test_unpersisted_failure_is_returned_to_the_watcher(streak) -> None:
    # Given: a state root that cannot hold the counter (a file where a directory belongs).
    module, tmp_path = streak
    root = tmp_path / "not-a-directory"
    _ = root.write_text("not a directory", encoding="utf-8")

    # When: a failure is recorded against it.
    result = module.record("mail-triage-watch", ok=False, threshold=3, root=root)

    # Then: the watcher is told, instead of being handed a silent success.
    assert result == "failure streak state was not persisted"


def test_a_writable_root_still_returns_no_unpersisted_notice(streak) -> None:
    # Then: the new signal fires only on the failure it names — a normal record is unchanged.
    module, tmp_path = streak
    result = module.record("mail-triage-watch", ok=False, threshold=3, root=tmp_path / "state")

    assert result != "failure streak state was not persisted"
