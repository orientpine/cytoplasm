# drive-publish — 스킬 최종 산출물 Drive 발행

스킬이 생성한 **최종 산출물(문서)** 을 소유자(cha) 본인 Google Drive의
`Autophagy 산출물/` 폴더로 즉시 업로드한다. git에 들어가지 않는 산출물(procurement·
report·proposal·doctype·patent)을 소유자가 Drive에서 바로 리뷰할 수 있게 하는 것이
목적이다. 기존 `gws drive` CLI로 수행하며 새 Python SDK·서비스계정·의존성이 없다.

> 이 문서는 git 미추적 **산출물** 발행 시스템만 다룬다. git 추적 문서(.omo/plans·
> notepads, docs/features·qa·patch)를 Drive로 미러링하던 drive-archive(E11) 시스템은
> 2026-07-31 폐기됐다 — 대상이 전부 GitHub에 백업되어 Drive 미러가 중복이었기 때문.

## 두 갈래

| | 경로 | 방식 | 게이트 |
|---|---|---|---|
| **공통 헬퍼 발행** | `Autophagy 산출물/<유형>/<YYYY-MM>/<파일>` | `DRIVE_PUBLISH_ENABLED=1`일 때 생성 즉시 업로드(초안 제외, 리뷰용) | 없음 (owner-only Drive) |
| **doctype 저장** | `Autophagy 산출물/doctype/<파일>` | 이름+부모 기준 upsert 후 owner-only 권한 + 재다운로드 SHA-256 검증 | 없음 (owner-only Drive) |

## 공통 헬퍼 `drive_publish.py`

procurement·report·proposal·patent 스킬이 공유하는 **무수정 vendored** 헬퍼
(`skills/<skill>/scripts/drive_publish.py`, byte-identical). `automation.*`를 import하지
않는 자립 모듈이며, `publish_best_effort`는 **옵트인**(`DRIVE_PUBLISH_ENABLED=1`)이라
테스트와 미설정 환경에서는 조용히 no-op이고 예외를 던지지 않는다.

폴더 구조는 4단계: `<root>/<doc_type>/<YYYY-MM>/<file>`.

## doctype 저장 `doctype_save.py`

`automation.drive_client.DriveClient`(공유 Drive 클라이언트)를 써서
`ensure_folder_path → upsert_file → verify_owner_only → download_and_verify`를 순서대로
수행한다 — owner-only 권한과 재다운로드 SHA-256이 모두 맞아야 성공이다(fail-closed).

## 환경변수

| 변수 | 기본 | 용도 | 사용처 |
|------|------|------|--------|
| `DRIVE_PUBLISH_ENABLED` | (미설정) | `1`일 때만 발행 | 공통 헬퍼 |
| `DRIVE_OUTPUTS_ROOT` | `Autophagy 산출물` | Drive 루트 폴더명 | 공통 헬퍼 |
| `DRIVE_PUBLISH_PERIOD` | 현재 `YYYY-MM` | 기간 하위폴더 | 공통 헬퍼 |
| `DRIVE_PUBLISH_GWS_BIN` / `PROCURE_GWS_BIN` | `gws` | gws 바이너리 | 공통 헬퍼 |
| `DRIVE_DOCTYPE_ROOT` | `Autophagy 산출물` | Drive 루트 폴더명 | doctype |
| `DRIVE_GWS_BIN` | `gws` | gws 바이너리 | doctype |
| `DRIVE_DOCTYPE_CACHE` | `~/.hermes/doctype/drive-folders.json` | 폴더 id 캐시 | doctype |

## 관련
- 공통 헬퍼: `skills/{procurement,report,proposal,patent-prep}/scripts/drive_publish.py`
- doctype 저장: `skills/doctype/scripts/doctype_save.py`, 공유 클라이언트 `automation/drive_client.py`
- doctype 저장 목적지 라우팅: `skills/doctype/scripts/doctype_routing.py`
