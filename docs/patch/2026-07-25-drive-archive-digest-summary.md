# 2026-07-25 — drive-archive: 배치 다이제스트 본문 요약 (E11)

## 무엇
`#approvals`에 올라가는 배치 다이제스트 본문을 **적응형**으로 바꿨다. 12건을 넘으면
경로 나열 대신 **분류별 집계**(건수 + Drive 대상 폴더 + `전체 목록` 포인터)를, 12건
이하면 각 경로에 대상 분류를 병기한 전체 목록을 보여준다. 승인 게이트·해시 바인딩·
업로드 범위는 그대로다.

## 왜 (소유자 요청)
최초 전체 동기화 다이제스트가 361건의 경로를 그대로 나열해 1859자/32줄(경로 26건 +
`- …외 335건`)의 벽이 됐다 — "승인 판단에 쓰기엔 너무 길다"(cha, 2026-07-25). 어차피
잘려 나가는 목록이라 ✅ 판단에 필요한 정보를 주지 못하고, 승인 표면으로서의 효용이
떨어진다.

## 어떻게
- **적응형 임계 12건**(`digest._LIST_LIMIT`): 초과 시 분류별 집계, 이하 시
  ``- `경로` → `분류/`  (sha12)`` 형태의 전체 경로 목록. 작은 델타에서는 경로 자체가
  정보이므로 요약하지 않는다.
- **라우팅 테이블 단일화**: 신규 `automation/drive_archive/routing.py`가
  `ARCHIVE_CLASSES`(plans·notepads·features·qa·patch·misc) / `classify(rel)` /
  `route_parts(root_name, rel)`를 소유한다. `uploader.py`는 자체 정의를 버리고 이
  `route_parts`를 import — 메시지가 실제 업로드 위치와 다른 폴더를 안내할 수 없다.
- **`DigestRequest`로 렌더러 순수성 유지**: `digest.render`는 frozen
  `DigestRequest(manifest, action_hash, root_name, tracked)` 하나만 받는다. `tracked`
  (cursor 키 집합)가 `신규 N · 변경 M` 분해를 만든다 — 디스크·env·시계 접근 없음.
  `sync_cli._changed_manifest()`는 cursor 1회 읽기로 frozen `ChangedBatch(batch, tracked)`를
  돌려주는 `_changed_batch()`가 됐다.
- **안전성 불변**: `action_hash`가 헤더·푸터에 문자 그대로 실려
  `confirm.reaction_decision`의 내용 바인딩(`pending.action_hash in message_content`,
  확인 불가 시 fail-closed)이 그대로 성립. 해시는 여전히 `BatchManifest.to_arguments()`
  에서만 계산되고, **본문에 무엇이 보이든 배치의 모든 파일이 업로드된다**(푸터
  `✅ = {N}건 전체 업로드 승인`이 실제 총건수를 명시). `_MAX_CONTENT`는 1900 유지
  (Discord 하드 한계 2000), `_MAX_LABEL = 80`이 env 파생 프로젝트·루트 이름을 클램프.
- **기각한 대안(재논의 방지)**: ① 다이제스트에 총 바이트 크기 표기 — 순수 렌더러
  안에 `os.stat` I/O를 들여놓게 된다. ② 청킹으로 다이제스트를 여러 Discord 메시지에
  분할 — 두 번째 메시지는 리액션을 달고 있지 않아 단일 메시지 내용 바인딩이 깨진다.

## 변경 파일
- 신규: `automation/drive_archive/routing.py`, 단위 테스트
  `tests/unit/test_drive_archive_routing.py`, `tests/unit/test_drive_archive_digest_summary.py`.
- 수정: `automation/drive_archive/digest.py`(`DigestRequest` + 적응형 본문),
  `automation/drive_archive/uploader.py`(`route_parts` import로 교체),
  `automation/drive_archive/sync_cli.py`(`_changed_batch` → `ChangedBatch`),
  `tests/unit/test_drive_archive_cli.py`, `tests/unit/test_drive_archive_digest_limit.py`,
  `tests/unit/test_drive_archive_confirm.py`.
- 문서: `docs/guide/drive-archive.md`(다이제스트 본문 절 추가 + 기본 루트 폴더명
  `Autophagy 문서아카이브`로 정정), `automation/AGENTS.md`, `.omo/plans/autophagy-agents.md`.

## 검증
- 실측 축소: 361건 기준 **1859자/32줄 → 467자/14줄**. 분포 qa 329 · patch 21 ·
  plans 6 · notepads 4 · features 1.
- 결정적 증거: 새 렌더러를 실제 스코프에 돌렸을 때 `action_hash
  sha256:db329608230600aae794d4b23678f8db81e7a742d76158755539f8b67f36b09f` — 구 렌더러가
  게시한 프로덕션 메시지의 해시와 바이트 동일 → **렌더링은 게이트 해시에 영향을 주지 않음**.
- `pytest tests/unit -k drive_archive` → 47 passed(기존 39). `pytest tests/unit` →
  1170 passed, 0 failed. — 위 수치는 베이스 `c39197a` 기준 최초 측정값이다.
- 리베이스 재검증: 같은 날 origin/main이 `c980101`(gated upload resumable)까지 전진해
  `27f091f` 위로 리베이스한 뒤 재실행 — `-k drive_archive` **53 passed**(resumable 테스트 6건
  포함), `pytest tests/unit` **1193 passed 0 failed**, e2e ALL PASS. `uploader.py`에서 두 변경이
  공존한다: 이 패치의 `from ...routing import route_parts`와 resumable 경로의 커서 스킵이
  서로 다른 훅이라 충돌 없이 병합됐고, `uploader` 안의 `route_parts` 정의는 0개로 단일
  라우팅 테이블이 유지된다.
- `bash tests/e2e/run_bank.sh --scenario drive-archive` → ALL PASS(오프라인, 실전송 0건).
- 증적 `docs/qa/E11/02-digest-summary.txt`.
