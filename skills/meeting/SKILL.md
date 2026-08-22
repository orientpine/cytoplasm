---
name: meeting
description: "명시적 !meeting 신호가 붙은 회의록(md/txt/pdf 업로드 또는 본문)에서 결정사항/액션아이템/마일스톤을 추출해 내 Kanban 카드와 milestones.yaml을 갱신하고 타인 항목은 #team에 규약 게시하는 W2-3 스킬. 민감도 게이트(constraint 6) 내장."
author: autophagy-agents
---

# meeting — 회의록 인제스트 (W2-3)

명시적 `!meeting` 신호가 붙은 회의록(md/txt/pdf ≤25MiB 또는 명령 본문)에서 결정사항/액션아이템을
추출해 ①내 항목 → Kanban 카드 + `~/state/milestones.yaml`, ②타인 항목 →
#team 규약(v0) 게시, ③상세 노트 → `~/notes/meetings/`(700, W2-4가 자동
색인)를 수행한다.

## 동작 방식 (중요 — 에이전트가 지켜야 할 규칙)

1. **소유자가 메시지 시작에 토큰 경계가 있는 `!meeting`을 명시한 본문 또는
   md/txt/pdf 첨부만** 게이트웨이 플러그인(`00-meeting-gate`)이 나(에이전트
   LLM)에게 도달하기 전에 가로채 자동 처리한다. 파일명·확장자·MIME·DM 여부는
   회의 의도가 아니며, 표식 없는 첨부는 일반 요청으로 전달된다. 어댑터가
   소유자 검증 후 `meeting_intent=True` boolean metadata를 설정한 경우만 같은
   명시 의도로 신뢰한다. 트리거된 메시지에 대해 내가 할 일은 없다 (플러그인이
   접수/결과를 직접 통지한다).
2. 사용자가 **로컬 파일 경로**를 언급하며 회의록 처리를 요청하면, 파일
   내용을 절대 읽지 말고(컨텍스트에 넣지 말고) 아래 CLI를 실행하라:

   ```bash
   python3 ~/.hermes/skills/meeting/scripts/meeting_cli.py ingest \
     --file <경로> --label "<회의 라벨>" [--with-evidence]
   ```

3. **금지**: 회의록 원문을 내 컨텍스트/응답에 붙여넣기. 민감도 게이트
   (constraint 6)는 CLI 안에서 LLM 호출 전에 실행되며, 특허 민감 문서는
   비-GLM(openai-codex)으로만 추출되고 상세는 `~/notes/meetings/`(700)에만
   남는다. 민감 문서의 각 액션아이템은 공개 문자열(제목·마감·근거)을
   동일한 결정 규칙(`sensitivity-rules.yaml`)으로 **항목 단위 재검사**한다 —
   어떤 규칙에도 걸리지 않은 항목만 `[민감회의] <제목> (마감 …)` 정보형
   카드(근거 + 로컬 노트 출처)로 생성되고, 하나라도 걸리거나 규칙을
   확인할 수 없으면 일반 마스킹 카드로 남는다(fail-closed). 문서 단위
   보호(비-GLM 라우팅, #team 미게시, 원문은 노트에만)는 그대로다.
4. 스캔본 PDF(텍스트 레이어 없음)는 CLI가 "수동 변환 요청"으로 응답한다 —
   내용을 추측해 채우지 마라.
5. 처리 결과 질문에는 CLI가 출력한 JSON 요약(건수/노트 파일명)만 사용하라.

## 지식 파사드 근거

`--with-evidence`는 회의 제목·참석자·주제로 읽기 전용 지식 파사드를 한 번 호출해
관련 선행 회의와 노트를 추출 프롬프트 재료로 넣는다. 근거 본문도 기존 민감도 게이트에
합산하며 patent-sensitive 또는 센티널 근거는 비-GLM으로만 처리한다. 생성 인용은 팩의
`[En]`만 허용하고 출처는 파사드 `sources` 형식 그대로 로컬 회의 노트 말미와 mode 0600
`*.evidence.json`에 남긴다. 민감 회의 원문과 본문은 기존처럼 카드·공개 표면에 싣지 않는다.

근거가 없으면 `근거 없음`, 조회 불가면 `근거 수집 불가`를 기록하고 회의록 생성은 계속한다.
재시도·임계값 하향·자체 검색은 하지 않는다. `meeting evidence --title ... --topics ... --json`은
원문 대신 `evidence_count`와 계층 상태만 미리 보여준다. 정본은
[`지식 계층 규약`](../../docs/guide/지식-계층-규약.md)이다.

## 구성 파일

- 민감도 규칙: `~/.hermes/skills/meeting/configs/sensitivity-rules.yaml`
  (원본: repo `configs/sensitivity-rules.yaml`)
- 추출 프롬프트: `~/.hermes/skills/meeting/prompts/meeting-extraction-v3.md`
  (원본: repo `prompts/meeting-extraction-v3.md`; v1/v2는 codex 에코 이슈로 폐기)
- 런타임 설정: `~/.hermes/meeting/config.json` (owner_id / team_channel_id /
  agent_id / my_names — repo 밖, 600)

## 검증

- 샌드박스: `scripts/scenario.sh` (오프라인, 녹화 응답, SCENARIO-PASS)
- 단위테스트: repo `tests/unit/test_meeting_skill.py`, `tests/unit/test_meeting_knowledge.py`
