# 버전·호환성·지원 정책

**대상 독자:** 이 소프트웨어를 설치해 운영하는 사람, 그룹 관리자, 업스트림 유지보수자.
**소유 범위:** 릴리스 버전과 스키마 버전의 관계, 런타임 상태 파일이 바뀌었을 때의
거동, 지원 범위. 취약점 보고 경로는 [`SECURITY.md`](../../SECURITY.md)가 소유한다.

---

## 1. 버전이 하나가 아니다

이 시스템에는 **서로 독립적으로 움직이는 버전이 여러 개** 있다. 하나로 묶으려는
시도가 반복적으로 나오는데, 묶으면 관련 없는 변경 때문에 남의 파일이 거부된다.

| 버전 | 어디에 있나 | 현재 값 | 누가 올리나 |
|---|---|---|---|
| **코드 릴리스 버전** | 공개 저장소의 서명된 릴리스 태그(`v1.0.0` …) | 최초 릴리스 미배포 | 업스트림 유지보수자 |
| **roster 스키마** | `automation/group_roster/schema.py`의 `SCHEMA_VERSION` | `1` | 업스트림 |
| **관리형 스킬 매니페스트 스키마** | `automation/managed_skills/manifest.py`의 `SCHEMA_VERSION` | `1` | 업스트림 |
| **managed-sync 런타임 상태 스키마** | `automation/managed_sync/state.py`의 `_SCHEMA_VERSION` | `1` | 업스트림 |
| **메모리 큐레이터 상태 버전** | `automation/memory_curator/state.py`의 `_VERSION` | `3` | 업스트림 |
| **노드 설정 필드 집합** | `automation/node_config.py`의 `_FIELD_NAMES` | 정수 버전 없음 — 필드 집합 자체가 계약 | 업스트림 |
| **관리형 스킬 릴리스 순번** | 매니페스트의 `release_sequence` | 스킬마다 다름 | **그룹 관리자** |
| **스킬 내용 정체성** | 매니페스트의 `skill_sha256` | 릴리스마다 다름 | 그룹 관리자 |

핵심 두 가지:

- **코드 릴리스 버전이 올라가도 스키마 버전은 대개 그대로다.** `v1.0.0`에서
  `v1.4.0`까지 roster 스키마가 계속 `1`인 것이 정상이다.
- **스킬 채널은 코드 채널과 소유자가 다르다.** `release_sequence`는 그룹 관리자가
  올리고, 업스트림 코드 버전과 아무 관계가 없다. 매니페스트에서 진짜 정체성은
  순번이 아니라 `skill_sha256`이며, 순번은 순서·재생 방지 용도다.

### 소비자가 호환성을 판단하는 법

세 가지를 각각 따로 본다. 하나로 합쳐 묻지 않는다.

1. **"내 노드가 이 릴리스를 받을 수 있나?"** → 서명 태그 검증
   (`automation/update_trust.py`)이 답한다. 실패하면 수렴하지 않고 healthcheck에
   사유가 남는다.
2. **"내 roster / 매니페스트 파일이 이 코드에서 읽히나?"** → 파일의 `schema` 또는
   `schema_version` 값이 그 코드의 `SCHEMA_VERSION`과 **정확히 같아야 한다.** 크거나
   작으면 거부다(아래 §3).
3. **"이 관리형 스킬을 활성화해도 되나?"** → 매니페스트의 `breaking` 플래그와
   `compatibility`·`changelog`·`migration` 필드를 읽는다. 이것은 그룹 관리자가
   채우는 사람용 정보이며, 코드가 자동으로 해석해 결정하지 않는다. 판단과 ✅는
   설치 소유자의 것이다.

### 스키마 버전을 올려야 하는 때

`SCHEMA_VERSION`을 올리는 것은 **기존 파일 전부를 거부하겠다는 선언**이다. 그러니
다음일 때만 올린다.

- 필수 필드를 추가·제거·이름 변경했을 때
- 기존 필드의 **의미**나 타입이 바뀌었을 때(값 형식 변경 포함)
- 기존 값의 해석이 달라져 구버전 파일을 그대로 읽으면 **틀린 결과**가 나올 때

반대로 다음은 올리지 않는다.

- 선택 필드 추가 — roster의 `update_channel`, 매니페스트의 `migration`이 그 예다.
  roster는 `validator.py`의 필드 집합이, 매니페스트는 `manifest.py`의
  `_ALLOWED_FIELDS`가 선택 필드를 이미 허용한다.
- 검증 메시지·내부 리팩터·성능 변경

⚠️ **필드 집합에 정수 버전이 없는 곳이 하나 있다.** `node_config.py`는
`_FIELD_NAMES` 자체가 계약이고 별도 버전 숫자가 없다. 여기에 필드를 더하면
`configs/node.example.toml` 시드를 같은 커밋에서 함께 고쳐야 한다 —
`_load_complete`가 시드에 **모든 필드가 정확히 한 번씩** 있기를 요구하기 때문이다.

---

## 2. 릴리스 버저닝 규칙

코드 릴리스는 `vMAJOR.MINOR.PATCH` 형태의 **서명된 annotated 태그**로만 나간다.
`automation/public_export.sh`가 공개 트리 커밋을 만든 직후 같은 실행 안에서 태그를
생성·서명·push한다. 서명 없는 태그나 손으로 만든 태그는 사용자 노드에서 검증에
실패해 수렴하지 않는다.

| 자리 | 올리는 기준 |
|---|---|
| PATCH | 버그 수정·보안 수정. 설정 파일도 상태 파일도 손대지 않아도 되는 변경. |
| MINOR | 기능 추가. 기존 설정·상태와 호환되며, 새 설정은 선택이거나 시드에 기본값이 있다. |
| MAJOR | **운영자 조치가 필요한 변경.** 스키마 버전 상승, 필수 설정 필드 추가, 승인·권한 경계 변경. |

즉 **운영자가 손을 대야 하면 MAJOR**다. 스키마 상승이 MINOR로 조용히 들어오면
자동 업데이트가 노드를 멈춰 세우는데, 릴리스 번호만 보고는 그것을 예상할 수 없다.

MAJOR 릴리스 노트에는 다음을 반드시 적는다: 무엇이 거부되는가, 어떤 오류 문자열이
보이는가, 운영자가 무엇을 하면 되는가.

---

## 3. 런타임 상태 스키마 마이그레이션 정책

런타임 상태는 체크아웃 밖 `~/.hermes/**`(그리고 `/srv/autophagy-private/**`)에만
있다. 코드가 업데이트되면 **새 코드가 옛 파일을 읽게 된다.** 이때의 정책은 하나다.

> **알아볼 수 없는 상태는 fail-closed로 거부한다. 조용히 재해석하지 않고, 조용히
> 기본값으로 갈아치우지도 않는다.**

이것은 새로 만든 규칙이 아니라 **이미 코드에 있는 거동을 정책으로 명문화**한 것이다.

### 3.1 실제로 랜딩된 거동

| 파일 | 로더 | 거부 조건 | 결과 |
|---|---|---|---|
| `~/.hermes/node.toml` | `node_config.load_node_config` | 알 수 없는 필드가 하나라도 있음 | `NodeConfigError: unknown node configuration fields: …` |
| 〃 | 〃 | 타입 불일치·빈 `origin_url`·상대경로·잘못된 계정/노드/유닛 이름 | `NodeConfigError` (필드명 포함) |
| `configs/node.example.toml`(시드) | `node_config._load_complete` | 필드 집합이 정확히 일치하지 않음 | `NodeConfigError: node seed must define every known field exactly once` |
| roster YAML | `group_roster.validator.validate_roster` | `schema`가 `1`이 아님(부울 포함) | `RosterError: schema must be the int 1` |
| 〃 | 〃 | 알 수 없는 키·필수 키 누락·중복 `discord_user_id` | `RosterError` |
| 관리형 스킬 매니페스트 | `managed_skills.manifest.parse_manifest` | `schema_version`이 `1`이 아님 | `ManifestError: schema_version must be the int 1` |
| `~/.hermes/managed-sync/…` 상태 | `managed_sync.state` | `schema_version != 1`, 키 집합 불일치 | `StateError: unsupported managed-sync state schema: …` |
| 메모리 큐레이터 상태 | `memory_curator.state` | 알 수 없는 버전 값 | `StateError: unsupported curator state version: …` |

중요한 세부 하나: **버전이 "미래"여도 거부된다.** 비교가 `>=`가 아니라 `==`다.
구버전 코드로 롤백한 노드가 신버전 상태 파일을 절반만 이해한 채 계속 쓰는 것이
가장 조용하고 가장 나쁜 실패이기 때문이다.

### 3.2 인플레이스 마이그레이션은 예외이고, 명시적일 때만이다

`memory_curator/state.py`가 유일한 선례다. 버전 키가 없는 v1 payload와 v2 payload를
읽어 v3로 올린다(`_migrate_v1`, `_require_version`). 이 방식이 허용되는 조건:

1. 변환이 **순수 함수**이고 손실이 없다.
2. 알 수 없는 버전은 여전히 예외다 — 마이그레이션 경로가 "아무거나 받아준다"가 되면
   안 된다.
3. 쓰기는 원자적이다(같은 디렉터리 임시 파일 → fsync → `os.replace`, 0600).

**마이그레이션 코드가 없으면 거부가 기본값이다.** 마이그레이터를 쓰지 않기로 하는
것은 게으름이 아니라 선택지다. 상태 파일 대부분은 재생성 가능하기 때문이다.

### 3.3 운영자 대응 — 이 오류를 봤을 때

노드가 위 오류 중 하나로 멈췄다면 순서는 이렇다.

1. **멈춘 것을 반가워한다.** fail-closed는 설계대로 동작한 것이다. 오류 메시지를
   지우려고 상태 파일을 임의 편집하지 않는다.
2. **오류가 가리키는 파일과 필드명을 읽는다.** 로더가 어떤 필드가 문제인지 이름을
   찍어준다.
3. **릴리스 노트를 확인한다.** MAJOR 릴리스면 필요한 조치가 거기 적혀 있다.
4. **파일 종류에 따라 조치한다.**
   - `node.toml` — `configs/node.example.toml` 시드와 대조해 알 수 없는 필드를
     제거하거나 새 필수 필드를 채운다. 이 파일은 설치별 설정이므로 손으로 고친다.
   - roster — 그룹 관리자에게 새 스키마로 재발행을 요청한다. 팀원이 손으로 고칠
     대상이 아니다(서명이 함께 깨진다). 그동안 **직전에 성공한 roster는 그대로
     남아 있다** — 검증 실패한 배달은 destination 바이트를 건드리지 않는다.
   - `managed-sync` 상태 — 재생성 가능하다. 상태 파일을 옮겨두면(삭제 대신 이름
     변경) 다음 틱이 새로 만든다. ⚠️ 다만 이미 활성화한 스킬의 `activated_digest`
     기억이 사라지므로, 다음 릴리스에 대해 다시 ✅를 요구받을 수 있다. 이것은
     안전한 방향의 실패다.
   - 메모리 큐레이터 상태 — 마이그레이션이 있으므로 대개 그냥 올라온다. 그래도
     거부되면 옮겨두고 재생성한다(제안 이력만 잃는다).
5. **되돌리기가 필요하면 릴리스를 되돌린다.** 상태 파일을 신버전에 맞춰 손으로
   조립하지 않는다. 롤백 절차는 `docs/guide/reboot-recovery.md`와
   `docs/guide/incident-response.md`가 소유한다.

⚠️ **하지 말 것**: 추적 파일(체크아웃 안의 시드)을 런타임 상태로 쓰는 것. 그러면
ops 체크아웃이 dirty해져 모든 `git pull --ff-only`가 막힌다. 런타임 상태는 언제나
`~/.hermes/**` 아래다.

---

## 4. 지원 범위 요약

- 지원되는 버전은 **최신 릴리스 태그 하나**다. 백포트 브랜치는 없다.
- 보안 수정은 다음 릴리스 태그로 나가고, 각 노드의 리컨실러가 서명을 검증한 뒤
  수렴한다. 상세는 [`SECURITY.md`](../../SECURITY.md).
- 지원하지 않는 것: 개별 설치의 운영 대행, 타인 노드 원격 진단, 그룹 관리자의 스킬
  저장소·서명키 운영, 데이터 백업·복구, 포크·개조본.
- **무보증**: [Apache-2.0](../../LICENSE) 7조·8조가 그대로 적용된다. 이 문서는 그
  법적 문구를 다시 쓰지 않는다.

## 5. 관련 문서

- [`SECURITY.md`](../../SECURITY.md) — 취약점 보고 경로·응답 약속
- `docs/guide/manual-maintainer.md` (W-M3, 미작성) — 릴리스 절차, **업데이트 신뢰키
  회전**, 나쁜 릴리스 대응. 키 회전은 이 문서가 아니라 그쪽이 소유한다.
- [`docs/guide/manual-group-admin.md`](manual-group-admin.md) — 스킬 발행·취소,
  `release_sequence` 운영
- [`docs/guide/manual-member.md`](manual-member.md) — 관리형 스킬 도착 시 판단과 ✅
- [`docs/guide/install.md`](install.md) — 설치 절차의 단일 진실
