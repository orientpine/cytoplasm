---
name: recall
description: "개인 RAG(personal_cha) 검색 스킬. `!recall <질문>` 명시 호출 + 개인/프로젝트 지식 질문에 자동 사용. 검색 결과를 출처(위키 경로/회의/보고 id)와 함께 인용하고, 결과가 없으면 반드시 '기억 없음'이라고 답한다(지어내기 금지). RAG 노드 다운 시 '검색 불가' 안내 후 일반 답변. W2-5."
version: 1.1.0
author: autophagy-agents
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [RAG, Memory, Recall, Source-Attribution, Autophagy]
prerequisites:
  commands: [python3]
---

# 개인 기억 회수 (recall)

cha의 개인 RAG(`personal_cha` 컬렉션, W2-1 MCP memory server)를 검색해
**출처가 붙은 컨텍스트**로 답한다. 검색은 로컬 MCP 한 곳으로만 나가며,
외부 임베딩/LLM API를 절대 호출하지 않는다.

## 언제 쓰나 (2가지 진입점)

1. **명시 호출**: 사용자 메시지가 `!recall <질문>` 형태면 **반드시** 아래
   검색 명령을 실행하고 그 결과로만 답한다.
2. **자동 사용**: cha의 개인/프로젝트/팀 지식(위키에 정리한 내용, 회의,
   동료 보고, 과거 작업/대화)에 대한 질문에 답하기 전에 **먼저 검색**한다.
   임계값 이상의 결과(status=hit)가 있으면 그 컨텍스트를 근거로 답한다.
   일반 상식·코딩 등 개인 기억과 무관한 질문에는 검색을 생략해도 된다.

## 검색 명령

```bash
python3 ~/.hermes/skills/recall/scripts/recall_cli.py search "<질문>" --json
```

출력은 `recall-v1` JSON 한 개다(스키마는 `scripts/recall_core.py` 문서화):
`status`가 `hit`(결과+출처) / `no_memory`(기억 없음) / `unavailable`(검색 불가)
중 하나다. `--json`을 빼면 사람이 읽는 텍스트 렌더링이 나온다.

## 특허 민감 분류 경계 (v2, model-aware — 2026-07-22)

`metadata.sensitivity == "patent-sensitive"`인 결과는 기본적으로 제외한다.
단 하나의 예외: 에이전트의 **주 모델 경로가 non-GLM으로 기계 검증**될 때
(recall이 `~/.hermes/config.yaml`의 `model.default`/`model.provider`를 직접 읽어
판정, 판정 불가시 fail-closed=제외) 포함하되, 각 행 content 앞에
`[[PATENT-SENSITIVE-RECALL]]` 센티널을 부착한다.

- 제외 시: 원문·출처·식별자 없이 건수만 `N건은 민감 분류로 제외`로 알린다.
- 포함 시: `N건 patent-sensitive 포함 — 주 모델 non-GLM 확인 …` 안내가 함께 출력된다.
- **GLM 폴백 윈도우 폐쇄**: 주 모델 장애/쿼터로 대화가 glm-main 폴백으로
  넘어가도 LiteLLM 게이트웨이 pre-call 가드가 센티널을 실은 요청을 HTTP 403으로
  거부한다(`configs/litellm-staging/custom_callbacks.py`) — 특허 내용은 어떤
  경로로도 GLM 제공자에 도달하지 않는다.
- recall에는 호출자가 포함을 강제할 CLI 표면이 없다(재포함 opt-in 없음) —
  판정은 오직 결정론적 모델-경로 가드만 내린다.
- `sensitivity` 키가 없는 행은 비민감으로 취급하지만, Obsidian 특허 문서는 이
  메타데이터를 항상 싣는다는 전제다.

## 답변 규칙 (절대 준수)

1. **status=hit** → `results[*].excerpt`를 근거로 답하고, 답변 끝에 반드시
   출처를 표기한다:

   ```
   <답변 본문>

   출처:
   - 위키: w2-5-노트.md (score 0.61)
   - 회의: 2026-07-15-회의요약.md (score 0.55)
   ```

   출처 문자열은 `results[*].attribution`을 그대로 쓴다(위키 경로/회의
   문서/`#agents-log` 메시지 id/task id/세션 id — W2-4가 붙인 메타데이터).
   컨텍스트에 **없는** 내용은 답에 보태지 않는다.
2. **status=no_memory** → 정확히 **"기억 없음"** 이라고 답한다. 추측·지어내기
   금지. 필요하면 "기억 없음 — 관련 내용이 개인 RAG에 없습니다. 위키에
   정리해 두면 다음에 기억할 수 있어요." 처럼 한 문장만 덧붙인다.
3. **status=unavailable** → "지금 기억 검색이 불가합니다(RAG 노드 응답
   없음)"라고 먼저 알린 뒤, 일반 지식으로만 답한다. **재시도 루프 금지** —
   CLI 자체가 1회만 시도하며, 같은 턴에서 다시 호출하지 않는다.
4. 검색 결과의 민감한 원문(위키 노트 전문 등)은 cha의 DM 밖(공개 채널,
   repo, git)으로 내보내지 않는다.

## Sandbox scenario

배포 파이프라인용 `scripts/scenario.sh`: 오프라인 픽스처로 hit 스키마/출처
표기, 임계값 미달 → `기억 없음`, RAG 다운 → `검색 불가`(1회 시도, 재시도
없음)를 검증하고 `SCENARIO-PASS`를 출력한다. 네트워크 호출·실시크릿 없음.
