"""Producer-only release card preflight; stored-record replay does not import this."""
from __future__ import annotations

from dataclasses import replace
from typing import Final

from automation.release_spec import (
    NEW_RENDER_VERSION,
    EnvelopeUnavailable,
    ReleaseSpec,
    ReleaseSpecError,
)

#: 봉투를 부를 수 없을 때 신규 카드가 대신 렌더하고 레코드에 적는 직전 판본.
PREVIOUS_RENDER_VERSION: Final = 3
#: 게시 전 거절이 stderr 에 내는 머리말 — release.sh 는 이 줄을 그대로 되울린다.
CARD_REFUSED_PREFIX: Final = "RELEASE-CARD-REFUSED:"


def card_for_new_request(spec: ReleaseSpec) -> tuple[ReleaseSpec | None, str]:
    """신규 카드를 게시 전에 완성한다 — 판본 확정도 1900자 예산도 첫 효과보다 먼저다."""
    # 판본은 여기서 한 번만 정해진다: 레코드가 적은 판본과 실제로 게시된 바이트가 어긋나면
    # 소유자의 ✅ 가 묶인 카드를 두 번 다시 만들 수 없다. 봉투를 부를 수 없으면 직전 판본으로
    # 내려가 그 판본을 기록하고, 한도를 넘으면 카드도 레코드도 저널도 남기지 않고 거절한다.
    for version in (NEW_RENDER_VERSION, PREVIOUS_RENDER_VERSION):
        candidate = replace(spec, render_version=version)
        try:
            content = candidate.render()
        except EnvelopeUnavailable:
            continue
        except ReleaseSpecError as error:
            return None, f"{CARD_REFUSED_PREFIX} {error}"
        return replace(candidate, posted_text=content), ""
    return None, f"{CARD_REFUSED_PREFIX} release owner envelope is unavailable"
