# 특허 반출 — 단일 라이브 승인

## 무엇을
특허 초안의 암호화 Drive 백업 요청을 공유 승인 생명주기(`request_owner_approval`)로 관리한다.
slug별 승인 키(`patent:{slug}`)에는 살아 있는 요청이 1건만 있고, 이미 승인된 `APPROVED`
매니페스트는 실행 전까지 그대로 보존된다.

## 왜
기존 `export-prepare`는 같은 slug로 다시 실행할 때 매니페스트의 `message_id`를 곧바로
덮어썼다. cha가 앞선 메시지에 ✅를 눌렀어도 워처는 새 id만 보므로 승인이 고아가 될 수
있었고, 반응 조회와 상태 전이 사이에도 매니페스트 교체 경합이 있었다.

## 사용 시나리오

### 정상 경로
1. cha가 `export-prepare --slug S`를 실행하면 평문 SHA-256·고정 폴더·mode·만료에 바인딩된
   요청 1건이 **행위 봇의 소유자 DM**에 올라온다(정책 v4, AS-2.3 이전에는 개인 서버
   `#approvals`). nonce는 게시 성공 뒤 매니페스트 commit 시 생성된다.
2. 같은 내용으로 다시 실행하면 새 메시지나 nonce를 만들지 않고 기존 PENDING을 재사용한다.
3. 승인 내용이 바뀌면 옛 메시지를 먼저 삭제하고 기존 PENDING을 CANCELLED로 supersede한 뒤
   새 요청 하나만 게시한다.
4. cha의 ✅를 watcher가 `APPROVED`로 반영하면, cha가 `export-execute`를 직접 실행해 암호화·
   ACL 사전검사·업로드를 수행한다.

### 실패·거부 경로
- 매니페스트가 이미 `APPROVED`이면 삭제나 CANCELLED 전이 없이 `owner-decided`로 연기한다.
- 매니페스트가 손상됐거나 승인 façade import가 실패하면 새 요청을 게시하지 않는다.
- Discord 메시지가 바인딩과 다르거나 확인할 수 없으면 기존 요청을 파괴하지 않고 거부·연기한다.
- watcher가 반응 조회 전후 `(slug, nonce, plaintext_sha256)` 변경을 발견하면 상태 전이를 중단한다.
- 정책 v4 이전에 `#approvals`로 게시된 매니페스트는 저장된 바인딩으로 그대로 소비된다 —
  현재 정책으로 재조준하지 않고, 저장 표면과 정책이 서로 모순되면 fail-closed 거부한다(SI-1).

## 관련
- 스킬/기능: `skills/patent-prep`, W5-5 특허·기술이전 준비
- 어댑터: `skills/patent-prep/scripts/patent_export_approval.py`
- producer: `skills/patent-prep/scripts/patent_export.py`
- watcher: `skills/patent-prep/scripts/patent_export_confirm_reaction_watch.py`
- 승인: `automation/interop/approval_lifecycle.py`; lease는 기존 `patent_export_manifest.lock` 재사용
- 승인 표면 정책: `automation/interop/approval_surface.py` (`PATENT_EXPORT` → `OWNER_DM` at v4)
- 테스트: `tests/unit/test_patent_single_live_request.py`, `tests/unit/test_patent_export_binding.py`
