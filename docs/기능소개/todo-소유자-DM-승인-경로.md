# todo 소유자-DM 승인 경로 (TRACK-A)

## 무엇을

`todo` 스킬의 Google Tasks 쓰기를 **request → 오너-DM 게시 → cha ✅ → 일회 claim → create → 재조회 검증 → archive** 한 줄기로 묶은 승인 사이클. 이 문서는 [Google Tasks 승인 기반 쓰기](google-tasks-승인-쓰기.md)가 요약만 하고 지나간 **상태 전이와 그 전이를 지키는 장치**를 다룬다.

## 왜

승인은 "DM을 한 번 보냈다"가 아니라 "인가된 argv가 정확히 한 번 실행됐다"여야 한다. 그 사이에 끼어드는 사고가 셋 있었다. 승인 없이 CLI가 바로 쓰는 경로, 같은 승인으로 두 번 쓰는 경로, insert 직후 프로세스가 죽어 실행 여부를 아무도 모르는 경로다. 세 번째가 가장 나쁘다. 자동 재삽입하면 중복 등록이고, 그냥 성공으로 치면 거짓 보고다.

## 사용 시나리오

- **happy**: `todo_cli request`가 `TodoApprovalSpec`(key·action_hash·target_id·argv 요약·surface·channel_id·policy_version)을 만들어 `ApprovalKind.TODO`의 새 바인딩을 해석하고 오너 DM에 게시한다. 레코드는 `pending`. cha가 ✅ → 워처(`todo_confirm_reaction_watch.py`)가 owner-only 리액션만 읽어 승인 원장에 적고 그 generation을 `archived`로 넘긴다. 같은 argv로 `todo_cli create`를 돌리면 claim이 `write_started`를 배타 생성(`O_CREAT|O_EXCL`, 0600)한 뒤 Tasks에 1회 insert하고, `gws tasks tasks get`으로 제목·ID를 재조회해 일치할 때만 receipt를 확정한다.
- **승인 없음**: 레코드가 없으면 `TODO-FAIL [4]`. 외부 호출 0.
- **재사용 시도**: 이미 소비된 generation으로 create → exit 4, 외부 호출 0. 새 `request`→✅로 새 generation을 만들어야 다시 1회가 열린다.
- **중단 후 재기동**: `write_started`가 남아 있으면 exit 7로 멈춘다. 자동 재삽입도, 자동 성공 처리도 하지 않고 소유자 조정 대상으로 남긴다. archive를 쓴 뒤 pending 정리 중에 끊겨도 같은 terminal 전이를 재시도할 뿐, 다른 archive로 덮어쓰지 않는다.
- **배포 샌드박스**: `scenario.sh`가 승인 런타임 루트를 보존하도록 고쳐져(PR #130) 샌드박스 실행이 실제 승인 상태를 오염시키지 않는다.

## 관련

- 코드: `skills/todo/scripts/{todo_cli.py, todo_cli_model.py, todo_preflight.py}`, 승인부 `todo_approval.py`·`todo_approval_model.py`·`todo_approval_store.py`·`todo_approval_runtime.py`, 일회성 `todo_execution_claim.py`, 표면 `todo_discord.py`·`todo_confirm_reaction_watch.py`
- 재사용: `automation/interop/approval_lifecycle.py`(파사드), `approval_lease.py`(FileKeyLease + PostingJournal), `approval_surface.py`(`ApprovalKind.TODO` = 오너 DM), `approval_directory.py`
- 강제: `tests/unit/approval_conformance_inventory.py` 등재 + `test_approval_lifecycle_conformance.py`, `test_todo_approval_producer.py`, `test_todo_execution_claim.py`, `test_todo_approval_e2e.py`, `test_todo_runtime_root.py`
- 증적: `docs/qa/RTS-6/04-a1-ssot.txt` ~ `08-a5-e2e.txt`, `09-review-fixes.txt`. 착지: PR #125(`f128f9a8`), 샌드박스 후속 PR #130(`2bb42a70`)
