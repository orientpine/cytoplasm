"""라이프로그 노트의 교정 참고 문서를 읽고, 무엇이 바뀌었는지 남기는 자리 — 효과 경계.

판정은 공용 엔진(`automation.term_correction`)이 하고, 적용은 노트를 렌더하기 직전
`note.corrected_lifelog_note` 가 순수하게 한다. 여기 남는 것은 두 효과뿐이다: 그 노드가 쓸
참고 문서를 어디서 읽는가(Drive 층 또는 노드 캐시), 그리고 고친 어절을 어디에 적는가.

plaud 는 `DRIVE_PUBLISH_ENABLED=0` 으로 돈다 — 개인 녹음은 Drive 로 나가지 않는다. 그래서
조회가 노드 캐시로 내려가는 것은 우회가 아니라 **정상 경로**이고, 캐시가 비어 있으면 교정이
한 건도 없는 것이 정상이다.

둘 다 fail-soft 다. 참고 문서를 못 읽거나 로그를 못 써도 노트는 그대로 나간다 — 교정은
노트의 전제가 아니라 품질이고, 여기서 예외를 올리면 녹음 하나가 영영 얼지 못한다.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence

from automation import term_correction, term_correction_log, term_glossary


def glossary(env: Mapping[str, str] | None = None) -> term_correction.Glossary:
    """이 노드가 라이프로그 노트에 쓸 교정 참고 문서 (바깥 층 → 안쪽 층 병합).

    종류 이름 `lifelog` 은 참고 문서 폴더(`autophagy/라이프로그/용어집.csv`)와 감사 로그의
    `document` 가 같은 말을 쓰게 하는 열쇠라 두 자리 모두 글자 그대로 적는다.
    """
    try:
        return term_glossary.glossary_for("lifelog", env=env)
    except Exception as failure:  # noqa: BLE001 - 참고 문서가 없다고 노트가 멈추지는 않는다
        print(f"GLOSSARY-FETCH-FAIL kind=lifelog {type(failure).__name__}", file=sys.stderr)
        return ()


def record(
    corrections: Sequence[term_correction.Correction],
    *,
    label: str,
    env: Mapping[str, str] | None = None,
) -> int:
    """고친 어절을 감사 로그에 남기고 적힌 수를 돌려준다 — `label` 은 그 녹음의 이름이다.

    `stage="note"` 는 이 문서의 어느 경로가 고쳤는지다. 같은 녹음이 여러 문서를 낳으므로
    그 구분이 없으면 로그만 보고는 중복인지 새 교정인지 알 수 없다.
    """
    try:
        return term_correction_log.record(
            corrections, document="lifelog", label=label, project="", stage="note", env=env
        )
    except Exception as failure:  # noqa: BLE001 - 로그는 관측 수단이지 파이프라인의 전제가 아니다
        print(f"{term_correction_log.MARKER} {type(failure).__name__}", file=sys.stderr)
        return 0
