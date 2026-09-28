"""괄호 그림 인용이 붙은 문장도 '-다' 종결로 읽혀야 윤문 결과가 문체 검사를 통과한다.

`test_proposal_refine.py` 는 FS3 정산이 고정한 파일이라 새 사례를 따로 둔다. 2026-09-28 노드 실측에서
`… 겹친다 ([[FIG:x]]).` 형식(figures 단계와 윤문 recast 가 만드는 정식 형식)의 문장이 `… 겹친다 ().` 로
남아 비-다 종결로 잡혔고, 호스트가 그 문장을 조금만 고쳐도 '새 위반' 으로 계산돼 5개 청크가 모두 거부됐다.
"""

from __future__ import annotations

from skills.proposal.scripts.proposal_refine import verify_invariants


def _style(original: str, candidate: str) -> bool:
    checks = {check.name: check.passed for check in verify_invariants(original, candidate)}
    return checks["kimm-style"]


def test_a_rewritten_sentence_that_keeps_its_figure_citation_passes_the_style_gate() -> None:
    original = "실차 굴착은 여러 원인이 겹친다 ([[FIG:fig-s1-01]]). 원인을 분리한다.\n"
    candidate = "실차 굴착에서는 여러 원인이 함께 겹친다 ([[FIG:fig-s1-01]]). 원인을 분리한다.\n"

    assert _style(original, candidate)


def test_a_newly_introduced_non_da_ending_is_still_rejected() -> None:
    original = "실차 굴착은 여러 원인이 겹친다 ([[FIG:fig-s1-01]]).\n"
    candidate = "실차 굴착은 여러 원인이 겹침 ([[FIG:fig-s1-01]]).\n"

    assert not _style(original, candidate)
