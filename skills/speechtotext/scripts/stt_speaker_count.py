"""전사본이 말해 주는 화자 수를 받아 재분리할지 정한다 — 모델도 파일도 부르지 않는다.

음향만으로 화자 **수**를 정할 수 없다는 것이 실측이다(2026-09-07 노드). 임계값 사다리는
0.8→18 · 1.0→6 · 1.2→2 · 1.35→1 군집으로 절벽이라 어떤 고정값도 두 녹음을 동시에 맞히지
못한다. 그런데 화자 수를 **주면** 분리기는 그 수를 상한으로 다시 묶는다 — 같은 4.5분
녹음에서 pyannote 는 `--num-speakers 3` 으로 45.6/42.9/11.4% 세 사람을 냈고(자동은 2명),
그 세 번째 목소리는 발화 시간 11.4% 라 잔여 군집이 아니다.

즉 모자란 것은 분리기가 아니라 **k 를 아는 일**이고, 그 근거(자기소개·호칭·질문응답 짝)는
오디오가 아니라 전사본 텍스트에 있다. 여기는 그 답을 어떻게 받을지만 정한다.
"""

from __future__ import annotations

import json
import re
from typing import Final

import stt_asides

_FENCE: Final = re.compile(r"^```[a-zA-Z]*\n|\n```$")
_INTEGER: Final = re.compile(r"^-?\d+$")
_CLOCK: Final = re.compile(r"^\[(?:\d{2}|--):(?:\d{2}|--):(?:\d{2}|--)\]")
_CLOCK_CHARS: Final = 10
_KEY: Final = "speaker_count"


def parse_count(raw: str | None, *, limit: int) -> int | None:
    """정수 하나만 답으로 받는 엄격 파서. 그 외는 전부 "답이 아니다".

    "3~4"·"3명"·산문에서 숫자를 주워 오면 모델이 망설인 자리를 우리가 확신으로 바꾸는
    것이고, 그렇게 만든 라벨은 문서에서 누군가의 발언처럼 읽힌다. 범위 밖(0 이하·상한
    초과)도 답이 아니다 — 상한은 분리기의 상한과 같은 값이어야 한다.
    """
    if not raw:
        return None
    text = _FENCE.sub("", raw.strip()).strip()
    value: object = text
    if text.startswith("{"):
        try:
            payload: object = json.loads(text)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get(_KEY)
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not _INTEGER.match(value):
            return None
        value = int(value)
    if not isinstance(value, int):
        return None
    return value if 1 <= value <= limit else None


def should_redo(estimated: int | None, observed: int) -> int | None:
    """다시 돌릴 화자 수, 아니면 None.

    답이 없거나 지금 보이는 화자 수와 같으면 돌리지 않는다 — 같은 답에 분리기를 한 번 더
    쓰는 것은 시간만 쓰고 문서를 바꾸지 않는다. 두 방향 모두 의미가 있다: 답이 더 작으면
    쪼개진 한 목소리를 도로 묶고, 더 크면 뭉쳐진 사람들을 푼다.
    """
    if estimated is None or estimated == observed:
        return None
    return estimated


# 노드 실측(2026-09-07, 같은 오디오·같은 전사·같은 프롬프트): 라벨을 남긴 초안에는 모델이
# 화자 2명, 라벨을 걷어낸 초안에는 3명(소유자 기준점)이라 답했다. 초안은 1차 분리 라벨로
# 조립되므로 모델은 문서에 보이는 라벨 **종류를 그대로 세어** 돌려준다 — 프롬프트에 "라벨을
# 믿지 말고 대화를 읽어라" 라고 적어도 그 앵커를 이기지 못했다. 그래서 세지 말라고 부탁하는
# 대신 셀 것을 주지 않는다. 시각은 남긴다: 근거 줄을 가리키는 좌표이고 화자를 암시하지 않는다.
def unlabelled(draft: str) -> str:
    """블록 헤더와 문장 안 끼어듦에서 화자 라벨을 걷어낸 초안 — 시각과 말만 남는다."""
    return "\n".join(
        line[:_CLOCK_CHARS] if _CLOCK.match(line) else stt_asides.extract(line)[0]
        for line in draft.splitlines()
    )
