# 2026-10-01 — 게이트웨이 외부효과 규칙 파일을 릴리스의 것으로 갱신했다 (전후 기록)

## 증상

agent·peer 게이트웨이가 읽던 외부효과 규칙 파일 `~/.hermes/interop/external-effect-tools.yaml`
이 저장소의 `configs/external-effect-tools.yaml` 보다 한참 낡아 있었다(agent 2026-07-24,
peer 2026-07-15 사본). 그 결과 터미널로 직접 하는 Google Tasks 쓰기
(`gws tasks tasks insert …`)가 외부효과로 인식되지 않아 소유자 승인 없이 통과했다.

갱신 전 판정(두 계정 모두, 홈 사본으로 평가):

```text
external_effect=False allowed=True reason=None
```

## 무엇이 갱신을 냈나

손 교체가 아니라 **소유자 승인 릴리스 v1.14.1(`c870819b`)** 의 `deploy_all --apply` 다. 인터롭
배포기(`automation/interop/deploy.sh`)가 매니페스트의 규칙 파일 행(`v2:file`,
`requires=roster` 없음)으로 두 계정의 홈 사본을 릴리스 바이트로 교체했다. 쓰기는
`automation/deploy_push.sh` 의 `push_file`(임시 파일 → `chmod 600` → sha256 확인 → `mv -f`)이다.

## 계정별 전후

| 계정 | 갱신 전 sha256 접두 | 갱신 전 규칙 수 | 갱신 뒤 sha256 접두 | 갱신 뒤 규칙 수 | 모드 |
|---|---|---|---|---|---|
| agent | `c4c4de54e958` | 7 | `069ec72d8a05` | 8 | 600 |
| peer | `602e89368603` | 5 | `069ec72d8a05` | 8 | 600 |

갱신 뒤 두 홈 사본은 릴리스 트리의 `configs/external-effect-tools.yaml`(`069ec72d8a05`, 8규칙)과
바이트가 같다.

갱신 전 규칙 id:

- agent: `drive_archive_batch_upload`, `generic_external_effect_tool`,
  `generic_external_post_command`, `gws_calendar_mutation`, `gws_gmail_send`, `mailon_send`,
  `patent_draft_drive_upload`
- peer: `generic_external_effect_tool`, `generic_external_post_command`,
  `gws_calendar_mutation`, `gws_gmail_send`, `mailon_send`

갱신 뒤 규칙 id(두 계정 동일):

- `generic_external_effect_tool`, `generic_external_post_command`, `gws_calendar_mutation`,
  `gws_gmail_send`, `gws_tasks_mutation`, `mailon_send`, `obsidian_write_note_push`,
  `patent_draft_drive_upload`

차이:

- agent: `gws_tasks_mutation`·`obsidian_write_note_push` 추가, `drive_archive_batch_upload` 제거.
- peer: `gws_tasks_mutation`·`obsidian_write_note_push`·`patent_draft_drive_upload` 추가,
  `mailon_send` 정규식 갱신.

## 이전 바이트는 어디에 있나

- **릴리스 경로는 이전 바이트를 남기지 않았다.** `push_file` 은 이전 파일을 복사하지 않고
  `mv -f` 로 교체한다. 같은 실행의 `~/.hermes/interop/rollback/<stamp>-<pid>/` 복사는 roster
  관문 **뒤**에서 드롭인·플러그인 두 파일·가드만 담고 규칙 파일은 담지 않는다. 그래서 갱신
  직후에는 두 계정 어디에도 규칙 백업이 없었다.
- **소유자 승인 아래 사후 백업을 만들었다.** 두 계정에
  `~/.hermes/interop/external-effect-tools.yaml.bak-20261001` 을 만들었다(noclobber, umask 077 →
  모드 600, 덮어쓰기·삭제 없음). 내용은 홈에서 되찾은 것이 아니라 git 이력에서 꺼낸 갱신 전
  바이트이고, 갱신 전에 실측한 sha256 과 대조했다. 접미 `20261001` 은 복사한 날(UTC)이지 옛 규칙이
  만들어진 날이 아니다.
  - agent: `git show 1dba4adc:configs/external-effect-tools.yaml` — 1260바이트,
    sha256 `c4c4de54e958…`
  - peer: `git show 0e02331a:configs/external-effect-tools.yaml` — 932바이트,
    sha256 `602e89368603…`
- **재발 방지는 다음 릴리스부터다.** 배포기가 규칙 파일을 바꾸기 직전, 홈 사본이 새 바이트와
  다르면 그 바이트를 같은 실행의
  `~/.hermes/interop/rollback/<stamp>-<pid>/.hermes/interop/external-effect-tools.yaml`
  (디렉터리 0700, 파일 600)에 먼저 복사한다. roster 가 없어 HELD 로 끝나는 실행에서도 마찬가지다.
  복사할 수 없으면 교체하지 않고 `INTEROP-DEPLOY-BLOCK` 으로 멈춘다. 이 수정은 별도 커밋으로 만들어졌고
  **v1.14.1 에는 들어 있지 않아 노드에는 아직 반영되지 않았다.** 다음 릴리스가 착지해야 동작한다.
- 같은 날 roster 배치 뒤의 인터롭 묶음 수렴이 두 계정에 rollback 디렉터리 하나를 만들었다.
  그 안에는 드롭인·플러그인 `__init__.py`·`plugin.yaml`·가드만 있고 규칙 파일은 없다.

되돌리는 방법(어느 것도 실행하지 않았다):

- `.bak-20261001` 을 같은 디렉터리의 임시 파일로 복사한 뒤 `chmod 600`, `mv -f` 로
  `external-effect-tools.yaml` 위에 놓으면 **폴백 홈 사본만** 되돌아간다. 아래 「게이트가 지금 읽는
  파일」대로 수렴 뒤 게이트는 홈 사본을 읽지 않으므로, 이것만으로는 게이트가 집행하는 규칙이 바뀌지
  않는다. 다음 인터롭 배포도 홈 사본을 다시 릴리스 바이트로 밀어 넣는다.
- 집행을 실제로 바꾸는 길은 둘이다.
  - 옛 바이트를 담은 파일을 가리키는 `EXTERNAL_EFFECT_DENYLIST_PATH` 를 **두 게이트웨이의 환경 모두**에
    넣고 agent·peer 쌍을 함께 재시동한다(`docs/guide/operations.md` §2). 플러그인은 규칙 파일 경로를
    import 때 한 번(`EXTERNAL_EFFECT_DENYLIST = policy_paths.denylist_path()`) 정하고, 호출마다 그 경로의
    파일만 다시 읽는다. 그래서 재시동 없이는 새 경로가 반영되지 않는다.
  - 릴리스를 되돌린다.

## 게이트가 지금 읽는 파일

갱신 직후 게이트는 **홈 사본**을 읽었다. 설치돼 있던 옛 플러그인
(`~/.hermes/plugins/interop-protocol/__init__.py`)은 `EXTERNAL_EFFECT_DENYLIST_PATH` 가 없으면
`~/.hermes/interop/external-effect-tools.yaml` 을 하드코딩해 읽었고, 두 게이트웨이 프로세스의
환경에 그 override 는 없었다.

roster 배치 뒤 같은 릴리스로 인터롭 게이트웨이 묶음이 수렴하고 쌍이 재시동된 뒤로는 다르다.

- 두 게이트웨이의 `PYTHONPATH`·`INTEROP_RUNTIME` 은 `~/.hermes/autophagy-import` 다.
- 그 경로는 릴리스 세대 `~/.hermes/autophagy-import.d/<release-sha>` 로 고정돼 있다.
- 두 계정 모두 `GATEWAY-GENERATION ok` 이다.
- 이 import root 에서 `policy_paths.denylist_path()` 를 부르면 릴리스 세대 아래 파일을 돌려준다:

```text
denylist_path /srv/autophagy-agent-releases/<release-sha>/configs/external-effect-tools.yaml
```

`policy_paths` 의 순서는 `EXTERNAL_EFFECT_DENYLIST_PATH` → 이 모듈이 실행되는 세대의
`configs/external-effect-tools.yaml` → 계정 홈 사본이다. 릴리스 트리는 root 소유 읽기 전용이라,
게이트를 받는 에이전트가 자기 규칙을 고칠 수 없다. 이것이 세대 파일을 먼저 고르는 이유다.

홈 사본은 그대로 남아 있고 릴리스와 바이트가 같다. 세대 파일을 찾지 못하면 그 사본이 폴백이
된다. 배포기와 전량 반영 판정도 그 사본을 계속 선언·대조한다(배포 불변식).

한계: 수렴 뒤 판정은 게이트웨이와 같은 import root·인터프리터로 재현한 것이다. 실행 중인
프로세스에 `EXTERNAL_EFFECT_DENYLIST_PATH` 가 없다는 것은 수렴 **전**에만 읽었다.

## 갱신 뒤 게이트 판정

호출은 `terminal` / `gws tasks tasks insert --params {}` 이고, 승인 로그가 비어 있으며 소유자 id 는
자리표시자다. 실제 gws 는 호출하지 않았다.

- 수렴 뒤, 게이트웨이 import root(`~/.hermes/autophagy-import`)와 게이트웨이 인터프리터,
  `policy_paths.denylist_path()` 가 고른 파일:

  ```text
  external_effect=True allowed=False
  ```

- 홈 사본, 두 계정 모두(릴리스 평가기와 설치된 런타임 평가기 둘 다):

  ```text
  external_effect=True allowed=False reason=approval_required
  ```

- 대조, `.bak-20261001`(갱신 전 바이트):

  ```text
  external_effect=False allowed=True reason=None
  ```

판정을 가르는 것은 실제로 고른 규칙 바이트다.

## 저장소 변경

- 이 문서와 공개 반출 원장 두 곳(`configs/public-export-baseline-additions.txt`,
  `configs/public-export-review.txt`)의 등록.
- 재발 방지 코드(규칙 파일을 바꾸기 전 이전 바이트를 rollback 에 남긴다)는 별도 커밋이다.
