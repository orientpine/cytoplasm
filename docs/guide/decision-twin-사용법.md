# 의사결정 디지털 트윈 — 사용법

cha의 판단·선호·원칙을 위키에 축적하고, 에이전트가 "<owner-name>이라면?"에 답할 때
그 축적을 권위 근거로 쓰는 실사용 흐름. 스키마·안전장치 규범은
[decision-twin-스키마.md](decision-twin-스키마.md), 게이트/명령 상세는
`skills/wiki/SKILL.md` 참조.

## C1 — 판단 기준 등록 (stated flow)

판단·선호·원칙을 **말 한마디로** 트윈에 등록한다.

1. **Discord 에이전트 DM에 자연어로 선언한다:**
   ```
   "앞으로 회의는 오전에만 잡아. 금요일 오후는 절대 안 돼."
   "GPU 구매 판단 기준을 위키에 저장해줘: 성능/가격보다 전력효율 우선"
   "외주 결정할 땐 항상 납기보다 품질이 우선이야"
   ```
2. **에이전트가 twin 초안을 만든다** — 문구에서 `kind`를 추론(선호→`preference`,
   규칙→`principle`, 특정 결정→`decision`), `provenance: stated`(본인 직접 선언 =
   최고 신뢰, **`strict` 권위가 허용되는 유일한 채널**), kind별 본문 템플릿
   (Trigger/Rule/Exceptions 등)을 구성한다.
3. **확인 메시지에 ✅·⛔가 미리 부착되어 올라온다** → **✅ 한 번 탭 = 저장**,
   ⛔ = 폐기. `wiki-confirm-watch` cron이 1분마다 리액션을 확인하므로
   에이전트가 자리에 없어도 탭만 하면 저장된다.
4. **저장 후 ~10분 내** RAG(`personal_cha`)에 색인되어 recall 검색과
   의사결정 컨설트에서 **권위 규칙**으로 쓰인다.

## D3 — 결정 기록 루프 (decision-record)

에이전트에게 결정을 위임/질문할 때 트윈이 작동하고, 결과가 다시 축적된다.

1. **결정을 물어본다:**
   ```
   "<owner-name>이라면 이 미팅 요청 어떻게 할까?"
   "이 견적, 진행할까 말까?"
   ```
2. **에이전트가 7단계 컨설트를 실행한다**: 요청 분류 → `twin_consult`
   (위키 활성 규칙을 충돌해소 랭킹으로) → recall(Obsidian 포함 선례 검색) →
   **3계층 답변**:
   ```
   [위키 규칙]    활성 원칙: "외주는 품질 우선" (stated/strict, 2026-07-22)
   [RAG 선례]    유사 결정 3건: 20250507_HD기술이전 미팅에서 …
   [불확실·충돌]  review_after 만료 규칙 1건은 강등 인용만
   ```
3. **판단 규칙**: 활성·비만료 strict/default 규칙이 있으면 그것을 기본값으로
   제안한다. 규칙이 없거나 / 충돌 / 만료 / 민감 / 외부효과면 **cha에게 질문**
   (fail-closed). `authority: strict`여도 메일·캘린더 등 실행은 언제나 기존
   승인 게이트를 거친다(SI-1 — 트윈 노트는 판단 근거이지 실행 권한이 아니다).
4. **실제 결정이 내려지면** 에이전트가 **decision record 초안**을 만든다
   (Context / Decision / Rationale & Trade-offs / What would change my mind;
   cha가 결정=`stated`, 에이전트가 기존 규칙을 따름=`observed`) → ✅ 탭 → 저장 →
   **다음 컨설트부터 선례로 작동**. 과거 결정을 뒤집으면 `supersedes`로 대체 기록.

## 보너스 채널 (요청 시 실행)

| 요청 | 동작 | 상한 |
|------|------|------|
| "Obsidian에서 내 판단 패턴 뽑아줘" | `twin_distill`이 후보 초안 제안 (Evidence + Counterexample 필수) | `provenance: inferred`, authority `default`까지 |
| "게이트 이력에서 내 경향 분석해줘" | `twin_observe`가 "경향일까요?" advisory 제안 | `provenance: observed`, authority `advisory` 고정 |

둘 다 **cha의 ✅ 없이는 절대 활성화되지 않는다** (SI-3).

## 수명 관리

- `review_after` 경과 노트는 컨설트에서 authority 1단계 강등 + `expired` 표시,
  `cleanup-suggest`가 `REVIEW-EXPIRED`로 재확인을 제안한다 (SI-2).
- 대체된 결정은 수정하지 말고 새 노트에 `supersedes: <옛-슬러그>`로 기록한다.
- 조회·백링크·정리 제안·`twin_consult`는 읽기 전용이라 확인 없이 즉시 실행된다.
