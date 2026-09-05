"""교정 로그 — 무엇이 무엇으로 바뀌었는지, 노드에만.

2026-09-05 전에는 다듬기가 교정 **횟수**만 돌려줬다. 그 숫자는 plaud 경로에서 버려지고
(transcribe_live._cli_result 는 transcript_path 와 model 만 꺼낸다) Drive 워처 경로에서는
cron stdout 으로만 흘러갔다. 그래서 바른 용어 교정이 낱말을 잘못 고쳐도 그것을 발견할 자리가
파이프라인 어디에도 없었다.

레코드에는 바뀐 **어절**만 담는다 — 문장을 함께 남기면 전사본 본문이 로그로 새어 나간다.
파일은 체크아웃 밖 0700/0600 이고, 다른 다섯 스킬의 ~/.hermes/<skill>/logs/*.jsonl 관례를
그대로 따른다.

쓰기 실패는 한 줄 마커일 뿐 전사를 멈추지 않는다. 로그는 관측 수단이지 파이프라인의 전제가
아니다.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import stt_terms

LOG_ENV: Final = "SPEECHTOTEXT_CORRECTION_LOG"
DEFAULT_LOG: Final = "~/.hermes/speechtotext/logs/corrections.jsonl"
DIR_MODE: Final = 0o700
FILE_MODE: Final = 0o600
MARKER: Final = "CORRECTION-LOG-FAIL"


def log_path(env: Mapping[str, str] | None = None) -> Path:
    """어디에 적히는가 — 체크아웃 밖이 기본이고 env 로 옮길 수 있다(샌드박스·테스트)."""
    source = os.environ if env is None else env
    return Path(source.get(LOG_ENV) or DEFAULT_LOG).expanduser()


def record(
    corrections: Sequence[stt_terms.Correction],
    *,
    label: str,
    project: str,
    stage: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> int:
    """교정 내역을 한 줄에 하나씩 덧붙이고, 실제로 적힌 수를 돌려준다.

    고친 것이 없으면 파일을 만들지 않는다 — 빈 원장은 읽을 사람에게 잡음이다. stage 는 어느
    경로가 고쳤는지다(transcribe / polish / absorb): 같은 문서가 여러 번 다듬어지므로 그 구분이
    없으면 로그만 보고는 중복인지 새 교정인지 알 수 없다.
    """
    if not corrections:
        return 0
    stamp = (now or datetime.now(UTC)).isoformat()
    path = log_path(env)
    lines = "".join(
        json.dumps(
            {
                "at": stamp,
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
