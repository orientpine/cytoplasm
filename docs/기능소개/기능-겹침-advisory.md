# 기능 겹침 advisory (SC-4)

## 무엇을

에이전트가 만든 자가 스킬(`~/.hermes/skills`)의 SKILL.md description·tags 낱말을
배포본(live governed) 스킬들과 대조해, 기능이 겹치는 **다른 이름**의 자가 스킬을
아침 자가 스킬 감사 보고에 `OVERLAPS-GOVERNED:<배포 스킬>` 한 줄로 알린다.
차단이 아니라 **보고**다 — 자가 저작의 무승인 착지는 소유자 결정(2026-08-15 옵션 B)
그대로 유지된다.

## 왜

이름 대조(SHADOWS-GOVERNED)와 콘텐츠 해시 델타는 '같은 이름'만 잡는다 — 에이전트가
`recall`과 기능이 겹치는 `mem-search` 같은 스킬을 만들면 탐지가 0이었다(cha 지적,
2026-08-28). 피해는 보안이 아니라(외부효과는 게이트가 출처와 무관하게 잡는다)
**라우팅 비일관성**(calendar↔coordination 이중 발동 사고와 같은 계열)과 **중복 개발**이다.

## 어떻게 판정하나

- stdlib 토큰 겹침(no-agent cron은 LLM-free 유지): containment(작은 쪽 기준 교집합
  비율) ≥ 0.5 이고 겹친 낱말 5개 이상일 때만.
- **오탐 상한을 실측으로 보정**: governed 스킬 18개를 상호 대조한 최고치가
  0.386(calendar↔coordination — 진짜 인접 도메인)이라 그 아래는 전부 무음이다.
  이 성질 자체가 회귀 테스트로 고정되어 임계 여유가 줄면 테스트가 먼저 빨개진다.
- 같은 이름은 SHADOWS-GOVERNED 소유라 이중 보고하지 않고, 읽을 수 없는 SKILL.md
  하나가 advisory 전체를 죽이지 않으며, advisory의 어떤 실패도 감사 보고 본연을
  막지 않는다(fail-soft).

## 사용 시나리오

- 에이전트가 `recall`을 베낀 `mem-search` 자가 스킬을 만들면 → 다음 아침 보고에
  `OVERLAPS-GOVERNED:recall 자가 스킬 mem-search 기능 겹침(score=…, 겹친 낱말: …) -
  archive 하거나 repo 로 승격(코드화→PR→릴리스)` 한 줄이 실린다. 해소까지 매일 반복.
- 소유자 조치는 둘 중 하나: `hermes curator archive <name>`(회수), 또는 쓸 만하면
  repo로 **승격** — 승격되면 배포본이 생겨 이름 충돌 가드가 그때부터 작동한다.
- 무관한 자가 스킬(예: 하이쿠 생성기)은 아무 줄도 만들지 않는다.

## 관련

- 판정: `automation/selfskill_audit/overlap.py` · 보고 배선: `automation/selfskill_audit/report.py`
- 회귀: `tests/unit/test_selfskill_overlap.py` · 계획: `.omo/plans/release-convergence-and-versioned-approval.md` §5.2 SC-4
- 배포: 별도 홈 배포물 없음 — 기존 `selfskill_audit_watch` cron이 릴리스 런타임의
  이 코드를 그대로 실행한다(릴리스 수렴이 곧 반영).
