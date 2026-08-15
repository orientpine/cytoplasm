# 기관메일 엔진(mailon) 리포 통합 — vendoring

**완료:** 2026-07-30 · **티켓:** repair `t_dff04dc2`(발단) · **스킬:** `mail`

## 무엇을

기관메일(mailon.kr) 발송·동기화 엔진인 `mailon` 패키지를 **별도 리포(`orientpine/emailAutomation`)에서 autophagy 안으로 통합**했다. 소스는 `skills/mail/vendor/mailon/`에 **무수정(byte-identical) vendoring**되고, 배포 시 체크아웃 밖 런타임 릴리스로 materialise되어 실행된다. 이제 mailon의 단일 진실은 autophagy 리포다.

## 왜

`emailAutomation`은 별도 GitHub 리포였는데, prod 노드의 `agent` 계정 키가 그 리포에 **read-only**였다. 그 결과 수리·기능 커밋이 배포 체크아웃(`~agent/emailAutomation`)에 쌓이기만 하고 origin에 push되지 못해 **좌초**했다 — 2026-07-30 시점 3개 티켓치(t_f8308332, t_4beedeb2, t_dff04dc2)의 수정이 미push 상태로 누적돼, 다음 정렬 때 유실될 위험이었다. "별도 리포인데 push 경로·드리프트 감시가 없다"는 구조적 결함이다. 리포를 단일화하면 이 좌초가 원천 차단된다.

## 어떻게 (구조)

- **vendoring**: `skills/mail/vendor/mailon/`(14개 .py, 무수정) + `vendor/requirements.txt`(4개 서드파티 `pyotp`/`python-dotenv`/`beautifulsoup4`/`lxml`, prod 검증 버전으로 고정). 서드파티는 **스코프 venv**로 격리 — 메인 트리 stdlib-전용의 명시적 예외(RAG 하위서비스와 동일 패턴).
- **런타임 릴리스**: mailon의 `config.py`가 `PROJECT_ROOT`(=`__file__.parent.parent`) 기준으로 `data/`·`logs/`에 런타임 쓰기를 하므로, 소스를 무수정 유지하려면 체크아웃 밖에서 실행해야 한다. `skills/mail/scripts/mailon_runtime_release.sh`가 배포 시 노드 로컬에서 버전형 릴리스를 구성한다:
  - `~/.hermes/mailon-runtime/current` → `releases/<src-digest>` (원자적 심링크)
  - `data`·`logs`·`.venv`는 `state/`·`venvs/`로 심링크 → 재배포·롤백에도 SQLite DB·venv 유지, **git 체크아웃 절대 안 더럽힘**
  - 릴리스 `mailon/`은 vendor 소스와 byte-hash 대조 후에만 활성화
- **wrapper**: `mail_wrapper_read.py`의 `_cfg()`가 `current`를 resolve해 python·db·mails 경로를 **같은 릴리스에서 파생**한다(split-brain 방지). subprocess 격리(자격증명 비유입) 유지.

## 사용 시나리오

- **소유자 관점**: 변화 없음. 기존처럼 mail 스킬로 기관메일 read/발송을 쓴다. 내부적으로 실행 위치만 `~/emailAutomation` → `~/.hermes/mailon-runtime/current`로 바뀐다.
- **개발자 관점(happy path)**: mailon을 고칠 일이 생기면 이제 autophagy 리포의 `skills/mail/vendor/mailon/`을 직접 고치고 → 커밋 → 푸시 → `skills/mail/deploy.sh`로 배포한다. 별도 리포·별도 push 경로가 없다.
- **실패/거부 경로**: 배포 시 vendor 트리가 origin/main에 없으면 provenance 가드가 `DEPLOY-BLOCK`. 릴리스 빌드 중 의존성 설치 실패(재현 불가)·lxml native import 실패·byte-hash 불일치면 `MAILON-RUNTIME-BLOCK`으로 중단하고 기존 `current`를 유지한다.

## 관련

- 소스: `skills/mail/vendor/mailon/`, `skills/mail/vendor/requirements.txt`
- 런타임/배포: `skills/mail/scripts/mailon_runtime_release.sh`, `skills/mail/deploy.sh`
- wrapper: `skills/mail/scripts/mail_wrapper_read.py`(`_cfg`)
- 규약·상세: 루트 `AGENTS.md`(vendored 하위 패키지 격리 예외), `docs/guide/기관메일-인터페이스.md`
- **prod cutover (완료, 2026-07-30)**: 런타임 릴리스 빌드 → mail 스킬 재배포(owner ✅ MOUNT) → `~agent/emailAutomation`의 state.db(messages 1935, SQLite backup API 무결성 보존)·attachments(653) 이관(quiescent 상태) → live wrapper가 `~/.hermes/mailon-runtime/current` 사용 확인(list smoke) → 구 clone 제거(백업 2종: tar.gz + git bundle). 좌초 3커밋은 vendored 소스에 반영됨(byte 동일 확인). 보존 번들: `.omo/mailon-salvage/`.
