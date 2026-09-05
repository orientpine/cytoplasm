"""교정 감사 로그 — 어느 문서에서 무엇이 무엇으로 바뀌었는지, 노드에만.

교정은 이제 산출 문서를 만드는 여러 자리에서 일어난다(회의록·라이프로그 노트 …). 그래서 로그도
스킬마다 하나가 아니라 **한 벌**이다 — 나뉘면 같은 오탐을 두 파일에서 따로 찾아야 한다.

레코드에는 바뀐 **어절**만 담는다. 문장을 함께 남기면 문서 본문이 로그로 새어 나간다.
파일은 체크아웃 밖 0700/0600 이고, 다른 스킬들의 `~/.hermes/<skill>/logs/*.jsonl` 관례를 따른다.

쓰기 실패는 한 줄 마커일 뿐 문서 생성을 멈추지 않는다. 로그는 관측 수단이지 파이프라인의
전제가 아니다.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation import term_correction

LOG_ENV: Final = "TERM_CORRECTION_LOG"
DEFAULT_LOG: Final = "~/.hermes/term-glossary/logs/corrections.jsonl"
DIR_MODE: Final = 0o700
FILE_MODE: Final = 0o600
MARKER: Final = "CORRECTION-LOG-FAIL"


def log_path(env: Mapping[str, str] | None = None) -> Path:
    """어디에 적히는가 — 체크아웃 밖이 기본이고 env 로 옮길 수 있다(샌드박스·테스트)."""
    source = os.environ if env is None else env
    return Path(source.get(LOG_ENV) or DEFAULT_LOG).expanduser()


def record(
    corrections: Sequence[term_correction.Correction],
    *,
    document: str,
    label: str,
    project: str,
    stage: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> int:
    """교정 내역을 한 줄에 하나씩 덧붙이고, 실제로 적힌 수를 돌려준다.

    `document` 는 어느 종류의 문서가 고쳐졌는지(meeting·lifelog …)이고 `stage` 는 그 문서의
    어느 경로가 고쳤는지다. 같은 회의가 여러 문서를 낳으므로 그 구분이 없으면 로그만 보고는
    중복인지 새 교정인지 알 수 없다.
    """
    if not corrections:
        return 0
    stamp = (now or datetime.now(UTC)).isoformat()
    path = log_path(env)
    lines = "".join(
        json.dumps(
            {
                "at": stamp,
                "document": document,
                "label": label,
                "project": project,
                "stage": stage,
                "kind": correction.kind,
                "term": correction.term,
                "before": correction.before,
                "after": correction.after,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
        for correction in corrections
    )
    try:
        path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        path.parent.chmod(DIR_MODE)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(lines)
        path.chmod(FILE_MODE)
    except OSError as failure:
        print(f"{MARKER} {type(failure).__name__}", file=sys.stderr)
        return 0
    return len(corrections)
