# 수리 PR 자동생성 — 해제 조건 조율안 (H7)

**상태:** 제안(미구현). 이 문서는 코드를 바꾸지 않는다.
**대상 독자:** freeze 소유 계획(`.omo/plans/repair-report-core.md`·`repair-report-rollout.md`)의 소유자, 그리고 cha.
**배경:** `AGENTS.md`「수리 반영 경로 규칙」은 브랜치 push 직후 `gh pr create`까지를 에이전트의 종착점으로 규정하지만, 코드에는 그 호출이 0건이다. `docs/follow-ups.md`「배포 가드 보강(DG-7)」이 그 사실을 기록했고, 그 뒤 조사에서 **구현이 아니라 조율이 선행 조건**임이 확정됐다. 이 문서는 그 조율에 필요한 세 가지 — 결과 계약·자격증명 배선·비대화식 호출 — 을 각각 실행 가능한 결론으로 확정한다.

왜 지금 구현하지 않는가는 한 문장으로 요약된다. **이음새는 비동결인데 그 이음새를 둘러싼 호출자와 유닛이 전부 동결이다.** `automation/repair/repair_ops_work_clone.py`(`push_branch`)는 자유롭게 고칠 수 있지만, 그 결과를 바깥으로 나르는 `repair_ops_cli.py`와 그 실행 환경을 정하는 `automation/repair/systemd/*`는 다른 계획이 불변으로 선언해 기계 검사로 강제한다. 그래서 PR 생성 자체보다 **실패를 보이게 만드는 일**이 먼저 막힌다.

---

## 1. 결과 계약 확장안

### 지금 계약이 무엇을 나르는가

`repair_ops_cli.py:163`의 `_run`이 한 줄 JSON을 stdout에 찍는다. 필드는 여섯 개다: `ticket`·`phase`·`commit`·`patch_doc`·`branch`·`push_error`. 종료 코드는 `BANK_BLOCKED`면 3, `push_error`가 있으면 4, 아니면 0이다. 즉 **push 실패는 계약에도 있고 종료 코드에도 있다.** PR 생성 실패를 여기에 얹으려면 필드와 코드를 둘 다 늘려야 하는데, 이 파일이 동결이다.

### 그런데 진짜 경계는 한 칸 더 안쪽이다

계약을 늘려도 소유자에게 닿지 않는다. 승인 워처의 자식 실행 경계가 그 앞에서 모든 것을 삼키기 때문이다. 확인한 지점은 셋이다.

- `repair_ops_reaction_watch.py:74-86` — `CliRepairApprovalCommands._run`이 `subprocess.run(..., capture_output=True)`로 자식의 stdout/stderr를 파이프로 빨아들이고, 마지막 줄에서 `result.returncode == 0` **하나의 불리언만** 돌려준다. 방금 찍은 여섯 필드 JSON은 이 시점에 이미 사라진다.
- `repair_ops_reaction_watch.py:143-157` — `dispatch`가 그 불리언을 `succeeded`로 받아 참이면 레코드를 회수하고 `_record`로 approvals 로그에 `{"result":{"status":"approved"}}`를 남긴다. 로그에 실을 수 있는 것은 `outcome` 문자열뿐이라, 자식이 무슨 이유로 어떻게 끝났는지는 기록될 자리가 아예 없다.
- `repair_ops_reaction_watch.py:214-230` — `main()`이 `run_once()`를 부르고 **무조건 `return 0`** 한다. 그래서 systemd `Type=oneshot` 유닛도 언제나 성공으로 끝나고, journal에는 실패의 흔적이 남지 않는다.

세 지점을 이으면 결론이 나온다. **`pr_error`를 계약에 실어도 그 값은 워처 경계에서 소실된다.** 불리언 한 개가 통과 대역폭의 전부이고, 그마저 `main()`의 상수 0에 덮인다. 이것은 push 실패에도 이미 해당되는 문제다 — exit 4가 `succeeded=False`가 되어 레코드는 보존되지만, **왜** 실패했는지는 어디에도 남지 않는다. PR 생성을 붙이면 조용한 실패가 하나 더 늘 뿐이다.

### 확장안 diff 스케치

실제 코드가 아니라, 어느 파일의 어느 지점에 무엇을 더할지의 스케치다. 네 곳을 함께 고쳐야 의미가 생긴다.

**(a) `automation/repair/repair_ops_cli.py` — `_run`의 JSON 사전.**
`branch` 뒤에 `pr_url`(성공 시 PR URL, 아니면 `None`)과 `pr_error`(실패 사유를 `redact(...)[:180]`으로 잘라낸 문자열, 아니면 `None`) 두 키를 추가한다. `push_error`가 이미 쓰는 마스킹·길이 절단 규칙을 그대로 복제한다 — 새 규칙을 만들면 그쪽만 마스킹을 빠뜨린다. 종료 코드는 `push_error`의 4와 **구분되는 값**(예: 5)을 쓴다. push는 성공했는데 PR만 실패한 상태는 push 자체가 실패한 상태와 복구 절차가 다르기 때문이다(전자는 브랜치가 원격에 있으므로 사람이 PR만 열면 되고, 후자는 재실행이 필요하다). 순서는 `BANK_BLOCKED(3) → push_error(4) → pr_error(5)`로 두어 먼저 일어난 실패가 우선한다.

**(b) `automation/repair/repair_ops_work_clone.py` — `push_branch` 이후의 새 함수.**
비동결 파일이므로 여기에 `open_or_reuse_pull_request(branch) -> str` 정도를 신설한다. `push_branch`는 건드리지 않는다 — 이미 `--force-with-lease`와 ticket-id 정규식 가드로 계약이 좁게 고정돼 있고, PR 생성 실패가 push 성공을 되돌리면 안 되기 때문이다(§3 참조). 호출은 `repair_ops_cli.py:150-160`의 `_push_repair_branch` 옆에 형제 헬퍼(`_open_repair_pr`)로 두고, `_run`이 `try/except RepairOpsError`를 하나 더 감싼다.

**(c) `automation/repair/repair_ops_reaction_watch.py` — `CliRepairApprovalCommands`를 typed 결과로.**
`_run`의 반환 타입을 `bool`에서 작은 frozen dataclass(`ChildOutcome`: `succeeded: bool`·`exit_code: int`·`detail: str | None`)로 바꾼다. `detail`은 자식 stdout의 **마지막 한 줄을 JSON으로 파싱해 `push_error`/`pr_error`만 뽑은** 값이다. 원시 stdout을 그대로 싣지 않는다 — 그 안에는 마스킹되지 않은 텍스트가 섞일 수 있고, 이 리포의 redaction 경계는 자식 쪽에 있다. 파싱에 실패하면 `detail=None`으로 두고 조용히 넘어간다(fail-closed가 아니라 fail-quiet가 맞다 — 여기서 예외를 던지면 승인 처리 전체가 죽는다). `RepairApprovalCommands` Protocol의 시그니처도 함께 바뀌므로 `apply`/`discard` 두 메서드가 동시에 이동한다.

**(d) 같은 파일 — journal 표면화와 pending 보존.**
`dispatch`(143-157)에서 `succeeded`를 `outcome.succeeded`로 읽고, 실패면 지금처럼 레코드를 보존하되 **한 줄을 stderr로 흘린다**: `REPAIR-CHILD-FAILED ticket=<id> exit=<code> detail=<마스킹된 사유>`. `Type=oneshot` 유닛의 stderr는 journal로 간다. 그리고 `main()`(214-230)이 상수 0 대신 **한 건이라도 실패했으면 1**을 돌려주도록 `run_once()`가 실패 건수를 반환하게 한다. 이때 주의점이 하나 있다 — 종료 코드를 non-zero로 바꾸면 유닛이 `failed` 상태가 되어 다음 타이머 틱에 영향을 줄 수 있다. 그래서 **실패 건수 반환은 (c)(d)를 같은 커밋에 넣되 롤아웃에서는 journal 한 줄만 먼저 켜고, 종료 코드 변경은 관찰 후 별도 단계로** 분리하는 것을 권한다.

`pr_error`가 있어도 pending 레코드는 회수하지 않는다. 현재 `dispatch`가 이미 그렇게 동작한다(`succeeded`가 거짓이면 `store.remove`를 부르지 않고 `release`만 한다) — 즉 (c)에서 불리언 의미를 유지하는 한 이 성질은 공짜로 따라온다. 다만 **PR 실패로 레코드를 보존하면 다음 틱에 승인 흐름이 통째로 다시 돈다**는 부작용이 있다(follow-ups에 이미 기록된 "거부 시 TTL까지 매 tick 샌드박스 재실행"과 같은 계열). 그래서 PR 실패는 `succeeded=True` + journal 경고로 다루고, 레코드는 회수하는 편이 낫다 — PR은 사람이 나중에 열 수 있지만 샌드박스 900초는 되돌릴 수 없다. **이 판단은 freeze 소유자가 뒤집을 수 있는 지점이므로 제안서에 명시적 선택지로 올린다.**

### fake-child 테스트 설계

실제 `repair_ops_cli.py`를 부르지 않고 경계만 검증한다. 설계는 이렇다.

- **가짜 자식 스크립트**를 `tmp_path`에 만든다. 인자를 무시하고 정해진 JSON 한 줄을 stdout에 찍은 뒤 정해진 코드로 종료하는 몇 줄짜리 Python 파일이다. `CliRepairApprovalCommands(repair_cli=<그 경로>, token=...)`에 넘기면 `sys.executable <path> ...` 호출 규약이 그대로 맞는다 — 프로덕션 코드를 몰킹하지 않고 **경계의 계약만** 쓰는 방식이라 리팩터에 강하다.
- **케이스 표**: ① exit 0 + `pr_url` 있음 → `succeeded=True`, `detail=None`, 레코드 회수됨. ② exit 5 + `pr_error` 있음 → 선택된 정책대로(`succeeded=True` + journal 경고 / 또는 보존), `detail`에 마스킹된 사유가 들어감. ③ exit 4 + `push_error` → `succeeded=False`, 레코드 보존, journal 한 줄. ④ stdout이 JSON이 아님(빈 문자열·깨진 바이트) → `detail=None`이고 **예외가 새지 않음**. ⑤ 자식이 타임아웃 → 기존 `timeout=900` 경로가 그대로 예외를 던지는지 확인(이 동작은 바뀌지 않아야 한다).
- **마스킹 회귀**: 가짜 자식이 토큰 모양 문자열을 `pr_error`에 넣어 찍게 하고, 워처가 journal로 흘리는 문자열에 그 값이 **없는지** 단언한다. 이것이 (c)에서 "원시 stdout을 싣지 않는다"를 코드가 아니라 테스트로 고정하는 부분이다.
- **`main()` 종료 코드**: `run_once`를 실패 1건이 나오도록 구성한 워처로 갈아끼우고 `main()`의 반환값을 단언한다. 롤아웃을 2단계로 쪼갤 경우 이 테스트는 2단계에서 켠다.
- 실 SSH·실 Discord·실 `gh`는 전혀 타지 않는다. 전부 `tmp_path`와 가짜 transport로 끝난다.

> **결론 ①**: 결과 계약은 `pr_url`·`pr_error` 두 필드와 전용 종료 코드 5를 추가하는 것으로 충분하지만, **그 값이 소유자에게 닿으려면 `repair_ops_reaction_watch.py`의 불리언 경계와 상수 `return 0`을 함께 고쳐야 한다.** 두 파일 모두 동결이므로, 위 (a)~(d) 스케치와 fake-child 테스트 설계를 **`.omo/plans/repair-report-core.md`의 소유자에게 하나의 묶음으로 제출해 freeze 해제 여부를 받는다.** 부분 해제(계약만 열고 워처는 닫아둠)는 받지 않는다 — 그 조합은 실패를 계약에 적어 두고 아무도 읽지 않는 상태를 만들어, 지금보다 나쁘다.

---

## 2. 자격증명 배선안

### 지금 무엇을 읽고 있는가

수리 계열 유닛은 전부 같은 파일 하나를 읽는다.

- `automation/repair/systemd/autophagy-repair-agent.service:9` — `EnvironmentFile=-/etc/autophagy/repair-approval.env`(선택적, `-` 접두).
- `automation/repair/systemd/autophagy-repair-approval-watch.service:11` — `EnvironmentFile=/etc/autophagy/repair-approval.env`(필수).
- `automation/systemd/autophagy-deploy-reconcile.service:14` — **같은 파일**. 유닛 주석이 그 선택을 명시적으로 정당화한다: "Deliberately the SAME file the repair watcher already reads (root:ops 0640)".
- `automation/owner_notice.py:10` — 모듈 독스트링이 파일 경로와 권한(`root:ops 0640`), 그리고 "새 시크릿·새 파일·새 토큰·새 sudoers 가 없다"는 설계 의도를 기록한다.

즉 **이 파일은 이미 세 유닛이 공유하는 공용 환경 파일이고, 그 공유는 의도된 결정이다.** 여기에 PR-write 토큰을 얹으면 그 결정이 조용히 뒤집힌다 — 배포 재조정 타이머가 자기 일과 무관한 write 자격증명을 상속받게 되고, 0640이므로 ops 그룹 전체가 읽는다. 필요 최소 권한이 아니다.

### 배선안

**(a) 전용 drop-in을 신설한다.** 승인 워처 유닛에만 `/etc/systemd/system/autophagy-repair-approval-watch.service.d/10-pr-token.conf`를 두고 그 안에 `EnvironmentFile=/etc/autophagy/repair-pr.env` 한 줄을 넣는다. drop-in인 이유는 명확하다 — 본 유닛 파일이 동결이므로 tracked 파일을 건드리지 않고 노드에서만 얹을 수 있고, 나중에 파일 하나를 지우는 것으로 되돌릴 수 있다. `systemd`의 `EnvironmentFile`은 **누적**이므로 기존 `repair-approval.env`를 대체하지 않고 더한다.

**(b) 새 파일의 권한은 0600, 소유자는 ops다.** 공유 파일의 0640(root:ops)과 달리 그룹 읽기를 주지 않는다. 이 파일을 읽어야 하는 주체는 승인 워처 하나뿐이고, 그 유닛은 `User=ops`로 돈다. `repair-approval.env`가 0640인 것은 root가 쓰고 ops가 읽는 구조라서인데, PR 토큰에는 그 구조가 필요 없다.

**(c) `ProtectHome=yes` 때문에 홈에 두면 안 된다.** 승인 워처 유닛 12행이 `ProtectHome=yes`이므로 `/home`이 이 서비스에게 빈 디렉터리로 보인다. 파일이 디스크에 멀쩡히 있어도 런타임에만 사라진다 — `AGENTS.md`「수리 반영 경로 규칙」이 `repair_push_key`를 `~/.ssh`가 아니라 `/srv/autophagy-private/`에 둔 이유가 정확히 이것이고, `repair_known_hosts`를 고정한 이유도 같다(ssh가 `~/.ssh/known_hosts`를 passwd 엔트리로 해석하는데 `ProtectHome`이 그것을 가린다). **같은 논리가 `gh`에도 그대로 적용된다** — `gh`의 기본 자격증명 저장소는 `~/.config/gh/hosts.yml`이고, 그 경로 역시 `ProtectHome=yes` 아래에서는 존재하지 않는다. 그래서 `gh auth login`으로 배선하는 방식은 이 유닛에서 **구조적으로 불가능**하며, 환경변수 주입이 유일한 경로다. `/etc/autophagy/`는 `ProtectHome`의 영향을 받지 않으므로 안전하다.

**(d) 토큰 사양.** 저장소 한정 fine-grained 토큰이며, 권한은 **Pull requests: read & write 단 하나**다. Contents는 read조차 주지 않는다 — push는 이미 `repair_push_key`(저장소 한정 write deploy key)가 담당하고, 두 자격증명을 섞지 않는 것이 「수리 반영 경로 규칙」이 이미 확정한 원칙이다. 변수명은 `GH_TOKEN`으로 한다(§3). 파일 안에는 `GH_TOKEN=<값>` 한 줄뿐이며, tracked 파일에는 절대 넣지 않는다 — 토큰 모양 리터럴은 secret-scan이 정당하게 막는다.

**(e) 검증 방법.** 배선이 실제로 붙었는지는 파일 존재가 아니라 systemd가 무엇을 읽는지로 판정한다.

```
systemctl show autophagy-repair-approval-watch.service -p EnvironmentFiles
```

출력에 `/etc/autophagy/repair-approval.env`와 `/etc/autophagy/repair-pr.env`가 **둘 다** 나와야 한다. 하나만 나오면 drop-in이 `daemon-reload`를 못 받았거나 경로 오타다. 인접 유닛으로도 확인한다 — 같은 명령을 `autophagy-deploy-reconcile.service`에 돌렸을 때 `repair-pr.env`가 **나오지 않아야** 한다(격리가 지켜졌다는 증거). 파일 권한은 `stat -c '%U:%G %a' /etc/autophagy/repair-pr.env`가 `ops:ops 600`을 내야 한다.

> **결론 ②**: PR 토큰을 공유 `repair-approval.env`에 넣지 않는다. **승인 워처 유닛 전용 drop-in `10-pr-token.conf`로 `/etc/autophagy/repair-pr.env`(ops:ops 0600)를 추가 로드하고, 그 파일에는 저장소 한정·Pull requests write 전용 `GH_TOKEN` 한 줄만 둔다.** `ProtectHome=yes` 때문에 홈 기반 `gh auth login`은 이 유닛에서 원천적으로 동작하지 않으므로 환경변수 주입이 유일한 배선이다. 배선 판정은 `systemctl show <unit> -p EnvironmentFiles`로 하며, 승인 워처에는 두 파일이·재조정 유닛에는 한 파일만 보이는 것을 함께 확인한다. 이 배선은 노드 작업이므로 **cha가 수행**하고, 에이전트는 이 문서 이상으로 나아가지 않는다.

---

## 3. 비대화식 호출 스케치

### 세 가지 불변식

**(a) `GH_TOKEN` env만 쓴다.** `gh`는 `GH_TOKEN`/`GITHUB_TOKEN` 환경변수를 `hosts.yml`보다 우선해서 읽는다. §2(c)에서 봤듯 `ProtectHome=yes` 아래에서는 `hosts.yml`이 애초에 도달 불가이므로 이것은 선택이 아니라 유일한 길이다. 자식 프로세스에 넘길 때는 `CliRepairApprovalCommands._run`이 `DISCORD_BOT_TOKEN`을 명시 전파하는 것과 **똑같은 방식**으로 `environment["GH_TOKEN"] = ...`를 쓴다 — 자식의 폴백에 기대지 않는 것이 이 리포의 no-agent cron 규약이다.

**(b) `GH_PROMPT_DISABLED=1`을 함께 넣는다.** `gh`는 TTY가 없으면 대부분 프롬프트를 생략하지만, 인증 만료·리포 추론 실패 같은 경계에서 대화형 흐름으로 빠질 수 있다. `Type=oneshot` 유닛에서 그 일이 생기면 900초 타임아웃까지 조용히 매달린다. 명시적으로 꺼서 **즉시 실패하게** 만든다. 함께 `--repo <owner>/<name>`을 항상 명시해 cwd 기반 추론을 배제한다 — 작업 클론의 remote 상태에 의존하지 않게 하기 위해서다.

**(c) 기존 PR을 재사용한다.** `gh pr create`는 같은 head 브랜치에 열린 PR이 있으면 실패한다. 그런데 수리 재실행은 정상 흐름이다 — 같은 티켓이 다시 돌면 같은 브랜치에 force-with-lease로 push되고, 그것만으로 이미 열린 PR의 head가 갱신된다. 그래서 순서는 **조회 먼저**다: `gh pr list --head repair/t_<ticket> --state open --json url,headRefOid`로 확인하고, 결과가 있으면 그 URL을 `pr_url`로 그대로 돌려준다(생성하지 않는다). 없을 때만 `gh pr create --base main --head repair/t_<ticket>`를 부른다. `headRefOid`가 방금 push한 sha와 일치하는지도 함께 본다 — 일치하면 완전 무동작, 불일치면 push가 아직 반영 안 된 것이므로 재조회 없이 그 PR을 그대로 반환하고 journal에 한 줄만 남긴다(GitHub 쪽 반영 지연으로 새 PR을 만드는 것이 최악의 결과다).

**(d) push 성공은 보존한다.** PR 생성이 실패해도 이미 push된 브랜치는 그대로 둔다. 되돌리려면 원격 브랜치를 삭제해야 하는데, 그것은 공유 영향이고 「원격 브랜치는 자동으로 지우지 않는다」가 이미 확정한 금지 사항이다. 코드 구조로는 §1(b)에서 `push_branch`와 PR 함수를 분리한 것이 이 불변식을 표현한다 — 두 함수는 별개 `try`에 들어가고, PR 쪽 예외가 push 쪽 성공을 삼키지 않는다.

### fake gh 테스트 설계

실제 `gh`도 실제 GitHub도 타지 않는다. 실행 파일 하나를 갈아끼우는 방식이다.

- **가짜 `gh` 실행 파일**을 `tmp_path/bin/gh`에 만든다(실행 비트 부여). 인자를 파일에 기록하고, 미리 넣어둔 시나리오에 따라 stdout/exit code를 내는 몇 줄짜리 스크립트다. 테스트는 `PATH`를 `tmp_path/bin` 우선으로 바꿔 호출을 가로챈다. 프로덕션 코드가 `gh`를 절대경로가 아닌 이름으로 부르도록 설계하면 이 방식이 그대로 성립한다.
- **인자 단언이 본체다.** 기록된 인자에 대해 ① `--repo`가 항상 있음, ② `pr create` 앞에 반드시 `pr list` 호출이 선행함, ③ `--base main`·`--head repair/t_<ticket>`이 정확함, ④ 어떤 인자에도 토큰 값이 실려 있지 않음(env로만 전달)을 단언한다. ④는 process table 노출 회귀를 고정한다.
- **환경 단언**: 가짜 `gh`가 자기 `os.environ`을 덤프하게 하고, `GH_TOKEN`이 있고 `GH_PROMPT_DISABLED=1`이 있는지 확인한다. 부모 셸에 `GH_TOKEN`을 넣지 않은 상태에서도 자식에 도달하는지를 함께 봐서, 명시 전파(폴백 아님)를 고정한다.
- **시나리오 표**: ① PR 없음 → `create` 1회, 반환 URL이 stdout과 일치. ② PR 있고 `headRefOid` 일치 → `create` **0회**, 기존 URL 반환. ③ PR 있고 sha 불일치 → 역시 `create` 0회, 기존 URL 반환 + 경고. ④ `gh` 미설치(PATH에서 제거) → `RepairOpsError`로 즉시 실패하고 **push 결과는 그대로 유지**됨. ⑤ `gh`가 인증 오류로 exit 4 → 같음. ⑥ `gh`가 대화형으로 매달리는 상황(가짜가 sleep) → 타임아웃이 걸려 있어 유한 시간에 끝남.
- ④⑤가 §3(d)를 직접 검증하는 자리다: 실패 후에도 `push_branch`가 반환한 브랜치 이름이 계약 JSON의 `branch` 필드에 살아 있어야 하고, 원격 삭제를 시도하는 인자가 기록에 **없어야** 한다.

> **결론 ③**: PR 생성 호출은 `GH_TOKEN` 환경변수 주입 + `GH_PROMPT_DISABLED=1` + `--repo` 명시로 완전 비대화식으로 고정하고, **항상 `gh pr list --head`로 먼저 조회해 열린 PR이 있으면 재사용하며, PR 실패는 push 성공을 절대 되돌리지 않는다.** 검증은 `PATH` 앞에 가짜 `gh` 실행 파일을 놓아 인자·환경·호출 횟수를 단언하는 방식으로 하며, 실 GitHub 접근은 테스트에 포함하지 않는다. 이 스케치는 §1의 freeze가 풀린 뒤에 구현한다 — 그 전에 구현하면 실패를 표면화할 곳이 없어 §3(d)의 보존 계약을 관측할 수 없다.

---

## 실행 순서

1. 이 문서를 `.omo/plans/repair-report-core.md` 소유자에게 제출한다 — 결정 대상은 §1의 (a)~(d) 묶음 freeze 해제, 그리고 "PR 실패 시 pending을 회수할 것인가"의 선택지 하나다.
2. 해제되면 cha가 §2의 노드 배선(drop-in + `repair-pr.env` + 토큰 발급)을 수행하고 `systemctl show`로 확인한다.
3. 그 다음에야 §3의 구현과 fake-`gh`/fake-child 테스트를 같은 사이클에 넣는다.

세 단계는 순서를 바꿀 수 없다. 배선이 먼저면 쓰이지 않는 토큰이 노드에 남고, 구현이 먼저면 실패가 보이지 않는 자동화가 프로덕션에 들어간다.

## 관련

- `AGENTS.md`「수리 반영 경로 규칙」(2026-07-29, PR 생성 종착점은 2026-07-31 추가)
- `docs/follow-ups.md`「배포 가드 보강(DG-7) 작업 중 발견한 후속 과제」
- 읽기 전용 확인 지점: `automation/repair/repair_ops_cli.py:163`, `automation/repair/repair_ops_reaction_watch.py:74-86,143-157,214-230`, `automation/repair/repair_ops_work_clone.py:38,64-65`, `automation/repair/systemd/autophagy-repair-{agent,approval-watch}.service`, `automation/systemd/autophagy-deploy-reconcile.service:14`, `automation/owner_notice.py:10`
- 이 문서는 코드·유닛·시크릿을 하나도 만들지 않았다. 외부효과 없음.
