# 2-store 메모리 재배치 (TRACK-D)

## 무엇을

메모리 재배치가 `MEMORY.md` 하나만 보던 것을 **`MEMORY.md` + `USER.md` 두 store**로 넓혔다. `OPS_REFERENCE`로 분류된 운영 사실이면 어느 쪽에 있든 후보가 되고, 소유자 ✅ 뒤 Obsidian 운영-참조 노트로 옮겨 RAG 검증 후 원본을 지운다. 기존 MEMORY 경로는 **바이트 그대로** 두고, USER만 `user--` 파일명 namespace로 분리한다.

## 왜

v1은 USER.md를 통째로 제외했다. 신원·전역 스타일이 거기 있으니 건드리지 말자는 보수적 선택이었다. 그런데 실제로는 USER.md에도 운영 참조가 흘러들어와 캡(1,375자)을 잡아먹고 있었고, 그 항목들은 분류기가 이미 `OPS_REFERENCE`로 정확히 골라내고 있었다. 회수할 수 있는 공간을 규칙 하나 때문에 놔두고 있던 셈이다.

동시에 확장을 그냥 하면 안 되는 이유가 있었다. 두 store에 **같은 텍스트**가 있으면 note 파일 경로가 충돌한다. 그리고 이미 배포된 MEMORY 건들의 note path·plan hash·RAG key·action hash가 조금이라도 바뀌면 소유자가 승인한 해시가 무효가 된다.

## 사용 시나리오

- **happy(USER 항목)**: cron 틱이 두 store를 분류 → 가장 크게 회수되는 `OPS_REFERENCE` 항목이 USER.md에 있으면 그것을 고른다 → note 계획의 파일명 앞에 `user--`를 붙여 `RelocationPlan`을 만들고 composite `action_hash`(source_kind + entry_digest + note_plan_sha256 + delete_intent)를 계산 → 오너 DM 승인 → Obsidian 쓰기 + 원격 read-back → RAG 인제스트 확인 → 5-게이트 통과 시 USER.md에서 그 항목 하나만 삭제.
- **MEMORY 호환**: `build_relocation_plan(entry_text)`의 기본 `source_kind="memory"`는 `plan_note` 결과를 **손대지 않는다**. 진행 중이던 MEMORY 건의 승인 해시가 그대로 유효하다.
- **동일 텍스트 충돌**: MEMORY와 USER에 같은 문장이 있어도 note relpath가 갈리므로 서로 덮어쓰지 않는다. `entry_digest`도 source-qualified라 한쪽 처리 이력이 다른 쪽 재제안을 막지 않는다.
- **중복 방지**: 후보 선택은 store별 인덱스 집합을 따로 들고 claim하므로, 한 store에서 같은 텍스트가 여러 번 나와도 각각 한 번씩만 잡힌다. 이미 레코드가 있는 digest는 건너뛴다.
- **RAG key 불변**: `rag_source_key`는 여전히 `obsidian:<note_relpath>` 형식이다. 검증 로직은 그대로다.
- **거부/대기**: ⛔·미반응·쓰기 실패·RAG 미인제스트는 모두 원본 무손상. 옛 "USER는 v1 재배치 대상 아님" 거절(`RelocationError`)은 제거됐다.

## 관련

- 코드: `automation/memory_relocate/{discover.py, plan.py, propose.py, effects_live.py}`
- 배경 문서: [메모리 재배치](메모리-재배치.md)(5-게이트·승인 바인딩 전체 그림), [메모리 큐레이터](메모리-큐레이터.md)(분류·삭제 단일 경로)
- 강제: `tests/unit/test_memory_relocate_two_store.py`, `test_memory_relocate_discover.py`, `test_memory_relocate_propose.py`, `tests/e2e/test_memory_relocate_two_store.py`
- 증적: `docs/qa/RTS-6/13-d1-red.txt`·`14-d2-green.txt`·`15-d3-e2e.txt`. 착지: PR #129(`8640b2b2`)
