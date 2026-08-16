# 플랫폼 운영자 매뉴얼 — 업스트림 릴리스를 컷한다 (W-M3)

**독자**: 업스트림 저장소의 소유자(유지보수자). 이 소프트웨어를 **만들어서 내보내는**
쪽이며, 자기 노드를 쓰는 사용자이기도 하지만 이 문서에서는 그 역할이 아니다.

**이 문서가 소유하는 것**: 두 저장소의 관계, 릴리스 컷 절차, 업데이트 신뢰키의 보관과
회전, 나쁜 릴리스 대응.

**이 문서가 소유하지 **않는** 것** — 링크만 하고 복사하지 않는다. 같은 절차를 두 문서가
설명하면 반드시 한쪽이 낡는다.

| 주제 | 소유 문서 |
|---|---|
| 내 노드 운영(게이트웨이 재시동·운영 제약) | [operations.md](operations.md) |
| 장애 대응 절차 | [incident-response.md](incident-response.md) · [reboot-recovery.md](reboot-recovery.md) |
| 취약점 보고 접수 경로·응답 약속 | [`SECURITY.md`](../../SECURITY.md) |
| 버저닝 규칙·스키마 상승 기준·상태 마이그레이션 | [versioning-support.md](versioning-support.md) |
| 노드 설치 절차 | [install.md](install.md) |
| 그룹 관리자 / 팀원 역할 | [manual-group-admin.md](manual-group-admin.md) · [manual-member.md](manual-member.md) |

> 위 표의 `operations.md`·`incident-response.md`·`reboot-recovery.md`는 **개발 저장소
> 전용**이라 공개 배포본에는 포함되지 않는다(`configs/public-export-manifest.txt`).
> 공개본에서 이 문서를 읽고 있다면 그 세 링크는 열리지 않는다.

---

## 0. 먼저 — 공개 저장소에서는 작업하지 않는다

**개발은 영원히 private 저장소에서만 한다. 공개 저장소는 주기적으로 다시 생성되는
일방향 파생물이며, 아무도 거기서 개발하지 않고 아무도 거기에 직접 커밋하지 않는다.**

이 문서의 첫 절이 이것인 이유는, 소유자가 v1.0.0을 낸 직후 실제로 이렇게 물었기
때문이다 — *"이제 나는 공개 repo에서 작업을 이어가야 하는가, private repo는
불필요해지는가?"* 두 물음 모두 답은 **아니오**다. 한 번 물었다는 것은 다음 사람도
물을 것이라는 뜻이고, 이 오해가 굳으면 두 트리가 갈라져 provenance가 통째로 무의미해진다.

| | private `orientpine/autophagy-agents` | public `orientpine/cytoplasm` |
|---|---|---|
| 성격 | **유일한 개발 origin** (영구) | 파생 배포 아티팩트 |
| 이력 | 전체 이력 | **fresh history** — private 이력을 import하지 않는다 |
| 누가 쓰나 | 사람·에이전트가 커밋·PR·머지 | **아무도 직접 쓰지 않는다** |
| 갱신 경로 | 평소대로 커밋 → 푸시 | `automation/public_export.sh` **단 하나** |
| 배포 provenance 기준 | `origin/main` (「배포 provenance 규칙」) | 해당 없음 |
| 노드의 `origin` | 아님 | **맞음** — 사용자 노드는 여기를 본다 |

"새 릴리스를 낸다"는 곧 **`--version`을 올려 export 스크립트를 다시 실행한다**는
뜻이지, 공개 저장소에 무언가를 밀어 넣는다는 뜻이 아니다.

### 손으로 push하면 정확히 무슨 일이 일어나나

세 가지가 동시에 일어난다. 셋 다 조용하지 않지만, 셋 다 늦게 발견된다.

1. **다음 export가 그 변경을 말없이 덮는다.** 스크립트는 공개 `main`을 클론한 뒤
   `git rm -r --ignore-unmatch .`로 이전 스냅샷을 통째로 지우고 private 스냅샷을
   복사한다(`public_export.sh:264-266`). 손으로 넣은 내용은 private에 없으므로
   그대로 사라진다. 커밋 이력에는 남지만 트리에는 남지 않는다.
2. **그 사이 모든 사용자 노드의 자동 업데이트가 멈춘다.** 노드는 `refs/heads/main`과
   **같은 커밋을 가리키는 annotated 서명 태그**만 후보로 삼는다
   (`git_tag_signature.parse_remote_release_refs`). 손 push로 `main`이 앞서가면 그
   커밋을 가리키는 태그가 없으므로 후보가 0이 되고,
   `UNSIGNED-HEAD: origin/main is not the commit of an annotated release tag`로 끝난다.
   리컨실러는 `[deploy-reconcile] UPDATE-TRUST-BLOCK ... — skipping tick`을 찍고
   **exit 0**으로 넘어간다 — 즉 노드는 실패한 것처럼 보이지 않고 그냥 전진하지 않는다.
3. **누가 무엇을 배포했는지 말할 수 없게 된다.** 이 저장소의 배포 규율은 전부
   "`origin/main`에 있는 것만 나간다"에 걸려 있다. 공개본에 private에 없는 바이트가
   섞이는 순간 그 문장이 거짓이 된다.

스크립트가 기계적으로 막는 것도 있다. 대상이 private origin과 같으면
`the private source origin cannot be the public destination` 또는
`destination resolves to the private source origin`으로 멈추고, 대상 디렉터리가
private 워킹트리 안이면 `target must be outside the private source working tree`로
멈춘다. 다만 **사람이 공개 저장소를 클론해 손으로 push하는 것까지 막는 코드는 없다** —
그것은 이 문서가 지키는 규율이다.

---

## 1. 릴리스 절차 — 순서가 전부다

### 1.1 최초 1회: 대상 저장소를 먼저 만든다 (스크립트가 하지 않는다)

`public_export.sh`는 **존재하는 원격**을 전제로 한다. 시작하자마자 `git ls-remote`로
대상을 읽고, 실패하면 `destination remote is unavailable`로 멈춘다. 그래서 새 대상
저장소로 처음 내보내기 전에는 유지보수자가 직접 만든다.

```bash
gh repo create orientpine/cytoplasm --public
```

자동 초기화(`--add-readme` 등)를 **하지 않는다.** 스크립트는 원격에
`refs/heads/main`이 없으면 `--no-checkout`으로 클론하고
`git symbolic-ref HEAD refs/heads/main`으로 브랜치를 열어 **첫 루트 커밋**을 만든다
(`public_export.sh:254-262`). 원격에 이미 커밋이 있으면 그 공개 이력을 이어간다.

이 단계는 대상 저장소당 딱 한 번이다. 이후 릴리스에서는 아무것도 만들지 않는다.

### 1.2 사전 조건 — 스크립트가 검사하고 거부하는 것

전부 `PUBLIC-EXPORT-BLOCK: <사유>` 한 줄로 멈춘다. 미리 알고 가면 시간을 아낀다.

| 조건 | 위반 시 메시지 |
|---|---|
| **private에 이미 랜딩돼 있어야 한다** (기본 `--source-ref origin/main`) | `source ref is not a commit: <ref>` |
| 워킹트리가 untracked 포함 clean | `source worktree is dirty; commit or remove every change first` |
| 얕은 클론이 아니어야 한다 | `source is shallow; full-history secret scanning is impossible` |
| 대상 디렉터리가 아직 없어야 한다 | `target already exists: <dir>` |
| 대상 디렉터리의 부모는 있어야 한다 | `target parent does not exist` |
| 대상은 private 워킹트리 **밖** | `target must be outside the private source working tree` |
| `--version`이 `vX.Y.Z`(선택적 접미사) | `--version must be a v-prefixed semantic version` |
| `--visibility`가 정확히 `public` | `--visibility must be exactly public` |
| `--repository-name`이 `OWNER/REPO` | `--repository-name must be OWNER/REPO` |
| 서명키가 심링크 아닌 정규 파일이고 읽을 수 있어야 한다 | `update-trust signing key must be a regular non-symlink file` / `... is unreadable or unusable` |
| 그 버전 태그가 원격에 아직 없어야 한다 | `destination tag already exists: <version>` |
| 매니페스트가 `.omo/`와 `docs/qa/`를 명시적으로 제외 | `manifest must explicitly exclude both .omo/ and docs/qa/` |
| 도구 전부 존재: `git gitleaks grep python3 pytest ssh-keygen tar cp mktemp realpath` | `required tool is unavailable: <tool>` |

`--remote`와 `--signing-key`는 값이 git 인자·git config 줄에 도달하므로 제어문자·선행
대시·`ext::` 전송이 사용 **전에** 거부된다(감사 C2/C3).

### 1.3 새 파일을 추가했다면 — 공개 결정 원장

`configs/`, `docs/guide/`, `docs/patch/` 아래의 모든 추적 파일은
**`configs/public-export-manifest.txt`(제외) 또는 `configs/public-export-review.txt`(공개)
둘 중 정확히 하나**에 기록되어야 한다. 매니페스트가 제외 목록이라 기록이 없으면
**기본값이 "공개"** 였고, 그 구멍을 닫은 것이 이 짝 원장이다(감사 C1).

```bash
python3 -m pytest tests/unit/test_public_export_manifest_coverage.py -q
```

여기서 실패하면 export도 실패한다 — 스크립트가 **내보낸 트리 안에서**
`python3 -m pytest tests/unit`을 다시 돌리기 때문이다(`public_export.sh:272`). 즉
원장을 잊으면 공개되는 것이 아니라 릴리스가 멈춘다. 원장은 **디렉터리 단위 공개
승인을 금지**한다 — 그것은 방금 닫은 구멍을 다시 여는 일이다.

### 1.4 실행 (2026-08-15 v1.0.0에서 실제로 돌린 형태)

```bash
bash automation/public_export.sh \
  --source-repo <private 체크아웃 경로> --source-ref origin/main \
  --target-dir <체크아웃 밖 임시 디렉터리> \
  --remote https://github.com/orientpine/cytoplasm.git \
  --version v1.0.1 \
  --signing-key ~/.ssh/autophagy_update_trust \
  --repository-name orientpine/cytoplasm --visibility public
```

`--signing-key` 대신 `UPDATE_TRUST_SIGNING_KEY` 환경변수를 써도 된다(같은 검사를 받는다).

### 1.5 한 번의 실행 안에서 벌어지는 일

이 순서가 곧 안전 논증이므로 통째로 읽는다.

1. 인자·전제 검사(§1.2).
2. **private 트리 비밀 스캔** — 워킹트리와 `--all --full-history` 양쪽. 그 직전에
   카나리아 2개(평범한 파일 하나, `.gitignore`가 제외하는 파일 하나)를 심어
   **둘 다 탐지되는지** 확인한다. 0건 스캔이 "스캔했다"의 증거가 되게 만드는 장치다
   (감사 C5). 놓치면 다음 둘 중 하나로 멈춘다 —
   `gitleaks missed a planted canary in <root>; a zero-finding scan proves nothing`
   또는 `gitleaks skipped an ignore-listed canary in <root>; scan coverage is narrower than assumed`.
   두 문구를 나눠 둔 이유는 "스캐너가 트리를 못 읽었다"와 "ignore 규칙을 존중해 일부를
   건너뛰었다"가 서로 다른 전제 붕괴이기 때문이다.
3. 원격 ref 조회 + 태그 중복 확인.
4. `git archive <source_commit>`로 스냅샷 전개 → 매니페스트 항목 삭제 →
   `automation/public_export_redaction.py`로 vendored 트리 비식별화(사후조건 불통과 시
   `vendored public-snapshot de-identification failed`).
5. 대상 클론 → 이전 스냅샷 제거 → 새 스냅샷 복사 → `git add -A`.
6. **내보낸 트리 스캔 + `python3 -m pytest tests/unit`** (여기서도 카나리아 선행).
   실패하면 `exported-tree unit tests failed`.
7. 커밋. 작성자/커미터 시각은 **source 커밋의 시각**을 그대로 쓰고, 메시지 본문에
   `source-sha:<private commit>`을 남긴다 — 공개 커밋에서 private 기준점을 되짚는
   유일한 연결고리다.
8. **그 커밋에 서명 태그**를 만든다(`git -c gpg.format=ssh ... tag -s`). 태그 메시지에
   `repository:` · `visibility:` · `source-sha:`가 들어간다.
9. **자체 검증** — 개인키에서 공개키를 뽑아 임시 allowed-signers를 만들고
   `git verify-tag`를 돌린다. 실패하면 push 전에
   `release tag does not verify as update-trust@autophagy in namespace git`으로 멈춘다.
10. 공개 이력 전체 비밀 스캔.
11. **`git push --atomic`으로 커밋과 태그를 한 번에** 보낸다. 어느 한쪽만 공개되는
    상태를 허용하지 않는다.
12. push한 원격을 **다시 읽어** `main`과 태그가 로컬과 정확히 같은지 확인한다.

성공하면 마지막 줄은 이렇다.

```
[public-export] PUBLIC-EXPORT-OK repository=orientpine/cytoplasm visibility=public source_sha=<private sha> commit=<public sha> tag=v1.0.1 target=<dir>
```

v1.0.0 실측값: `source_sha=f54cf28fdf541631c14c6dd03a3a13c6eac8a86d`,
`commit=4789b2ad73f4d66c3f5f91e13311910e0a2e022c`, 내보낸 트리에서 `3902 passed`,
gitleaks 0건(private 워킹트리·private 전체이력·공개 트리·공개 이력 전부).

실패하면 `trap`이 임시 디렉터리와 (이 실행이 만든) 대상 디렉터리를 지운다. 부분
결과를 남기지 않는다.

### 1.6 ⚠️ 왜 순서를 뒤집을 수 없나 (D8)

**private에서 태그를 먼저 서명하고 그 다음에 export하는 순서가 아니다.**

공개 저장소는 fresh history다. private의 커밋 SHA는 공개 저장소에 **존재하지 않고
앞으로도 존재하지 않는다.** private에서 서명한 태그는 공개 저장소에 없는 객체를
가리키므로, 그 태그를 공개 저장소에 밀어 넣어도 사용자 노드에서는 후보가 되지 않는다 —
`parse_remote_release_refs`는 **peeled commit이 현재 `refs/heads/main`과 같은** 태그만
후보로 올리기 때문이다.

그 결과는 한 노드의 실패가 아니라 **연합 전체의 정지**다. 후보가 0이면
`UNSIGNED-HEAD`, 리컨실러는 매 2분 같은 줄을 찍으며 아무 노드도 전진하지 않는다.
그래서 커밋 생성 · 그 커밋에 대한 서명 · push가 **한 번의 스크립트 실행 안에서**
일어난다. 셋을 사람이 나눠 하는 순간 이 실수가 가능해진다.

### 1.7 릴리스 노트 게시 (스크립트 범위 밖)

스크립트는 태그까지만 만든다. GitHub Release 노트는 유지보수자가 따로 올린다.

```bash
gh release create v1.0.1 --repo orientpine/cytoplasm \
    --title 'v1.0.1' --notes-file <노트 파일>
```

노트에 **반드시** 들어가야 하는 것:

- 업데이트 신뢰키 **지문**(현재 `SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA`).
  신규 설치는 설치기 번들의 키를 **설치기가 아닌 경로로** 대조해야 하는데, 그 대역외
  경로가 바로 README와 이 릴리스 노트다(P0-6 부트스트랩).
- 이 릴리스가 MAJOR라면 무엇이 거부되는지·어떤 오류 문자열이 보이는지·운영자가 무엇을
  하면 되는지(→ [versioning-support.md](versioning-support.md) §2).
- 되돌림 릴리스라면 무엇을 되돌렸고 어떤 버전이 영향을 받았는지(→ §4).

### 1.8 컷 이후 확인

```bash
# 공개 저장소에서 태그가 실제로 검증되는가 (신뢰키 파일이 설치된 노드에서)
git -c gpg.format=ssh \
    -c gpg.ssh.allowedSignersFile=/etc/autophagy/update-allowed-signers \
    verify-tag v1.0.1

# 원격 상태
git ls-remote https://github.com/orientpine/cytoplasm.git refs/heads/main 'refs/tags/v1.0.1*'

# 내 노드가 전진했는가 (2분 이내)
readlink /srv/autophagy-agent-current
```

`ls-remote`에서 태그가 `refs/tags/v1.0.1`과 `refs/tags/v1.0.1^{}` 두 줄로 보이면
annotated 태그가 맞다. `^{}` 줄이 없으면 lightweight 태그이고, 그것은 후보조차 되지
않는다.

---

## 2. 업데이트 신뢰키 — 보관

### 2.1 이 키가 무엇이고, 무엇이 **아닌가**

**서명키가 둘이다. 혼동하면 안 된다**(D8). 소유자도, 파일도, 검증자도 다르다.

| | 업데이트 신뢰키 | 그룹 스킬 서명키 |
|---|---|---|
| 무엇에 서명하나 | **공개 저장소의 릴리스 태그** | 관리형 스킬 태그·roster 스냅샷 |
| principal | `update-trust@autophagy` | `publisher-<slug>@autophagy` |
| namespace | `git` | `git,autophagy-roster` |
| 노드의 신뢰 파일 | `/etc/autophagy/update-allowed-signers` | `/etc/autophagy/managed-skills-allowed-signers` |
| 검증하는 코드 | `automation/update_trust.py` | `automation/managed_sync/verify.py` |
| 개인키 소유자 | **업스트림 유지보수자 (나)** | 각 그룹 관리자 |
| 이 문서 | 소유한다 | [manual-group-admin.md](manual-group-admin.md)가 소유한다 |

설치 계획 코드가 두 파일을 섞는 것을 거부한다 —
`WRONG-FILE: 업데이트 신뢰키와 그룹 스킬 서명키는 서로 다른 파일에 설치해야 한다(D8)`.

### 2.2 키 생성과 지문

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/autophagy_update_trust -C "update-trust@autophagy"
ssh-keygen -lf ~/.ssh/autophagy_update_trust.pub
```

개인키 0600, 공개키 0644. 지문(`SHA256:...`)이 대역외 공지값이다. 설치기의
`python3 automation/install/trust_key_bootstrap.py fingerprint --key <pub>`도 같은 값을
낸다(같은 계산 — `sha256(base64-decoded key material)`).

지원 알고리즘은 `ssh-ed25519` · `sk-ssh-ed25519@openssh.com` · `ecdsa-sha2-nistp256` ·
`ssh-rsa`다. 그 밖은 `BUNDLED-KEY-ALGORITHM`으로 거부된다.

### 2.3 ⚠️ 개인키는 어떤 저장소에도 들어가지 않는다

**`~/.ssh/autophagy_update_trust`(개인키)를 public이든 private이든 **어떤 git
저장소에도 커밋하지 않는다.** 노드에도 배포하지 않는다. 노드에 놓이는 것은 언제나
공개키 한 줄뿐이다.**

이 원칙은 그룹 스킬 서명키에 대해 이미 확립된 것과 같다
([manual-group-admin.md §2.3](manual-group-admin.md) — "개인키는 관리자 워크스테이션
밖으로 나가지 않는다 — 노드에 배포 금지"). **다른 키에 같은 규율**을 적용하는 것이지,
같은 키를 두 번 말하는 것이 아니다.

왜 이 키가 특별한지 구체적으로.

- **이 키는 모든 설치의 유일한 신뢰 근원이다.** 내 노드도, 연합의 제3자 설치도, 새
  릴리스를 받을지 말지를 오직 이 키의 서명으로 판단한다. 그리고 수렴은 **root 권한**의
  헬퍼가 수행한다.
- **유출되면**, 공격자가 임의의 코드에 "정상" 서명을 붙일 수 있다. 노드는 서명이
  맞으므로 정상 릴리스로 받아들이고 실행한다. W-F1-C의 자동 롤백은 스모크 실패를
  되돌릴 뿐 **이미 실행된 악성 코드를 되돌리지 못한다.**
- **분실되면**, 앞으로 어떤 릴리스도 컷할 수 없다. 회전(§3)밖에 길이 없는데, 회전은
  이미 배포된 **모든** 노드의 `/etc/autophagy/update-allowed-signers`를 손대야 하고
  그 작업에는 자동 경로가 없다(§3.2).

그래서 **백업이 필수다.** 이 문서는 백업 방식을 규정하지 않는다 — 패스워드 매니저,
오프라인 매체, 봉인 보관 무엇이든 소유자가 통제하는 곳이면 된다. 규정하는 것은
불변식 하나뿐이다: **지금 이 워크스테이션이 통째로 사라져도 개인키는 살아남아야 한다.**
그리고 그 백업 사본도 저장소가 아니어야 한다.

발행 실무는 그룹 키와 같다: 패스프레이즈를 걸고 ssh-agent로 올린다. 에이전트 런타임
환경에서 릴리스를 컷하지 않는다.

---

## 3. 업데이트 신뢰키 — 회전

> **이 저장소는 아직 한 번도 회전한 적이 없다.** 아래는 랜딩된 코드의 실제 거동에서
> 역산한 절차이며, 첫 실수행 때 관측값으로 갱신한다.

### 3.1 불변식 — 이것 하나만 지키면 된다

**새 키로 서명한 릴리스를 컷하기 전에, 이미 배포된 모든 노드의
`/etc/autophagy/update-allowed-signers`에 새 지문이 들어가 있어야 한다.**

순서를 뒤집으면 어떻게 되는지 정확히 말한다. 노드는 새 태그를 후보로 올리고
(`main`과 같은 커밋이므로), `git verify-tag`가 실패해
`BAD-SIGNATURE: git returned <n>: ...`가 되거나, 키는 통과하지만 principal이 다르면
`WRONG-PRINCIPAL: verified principal is not update-trust@autophagy`가 된다.
`resolve_signed_update`는 그 후보를 버리고 다음 후보로 넘어가며, 후보가 다 떨어지면
마지막 오류를 올린다. 리컨실러는 그것을 잡아
`[deploy-reconcile] UPDATE-TRUST-BLOCK <오류> — skipping tick`을 찍고 **rc 0으로
정상 종료**한다.

즉 **아무도 알람을 받지 않는다.** 노드는 조용히 옛 릴리스에 머물고, 매 2분 같은
저널 줄만 쌓인다. 노드 소유자 쪽에서는 healthcheck의 signed-update probe가 기존 repair
티켓 경로로 사유를 올려 주지만, **유지보수자는 남의 노드를 관측할 수 없다.**

### 3.2 왜 자동화할 수 없나

`/etc/autophagy/*-allowed-signers`는 **root 소유이고 업데이트 채널이 쓰지 않는다.**
릴리스 수렴은 `/srv/autophagy-agent-releases/<sha>` 아래에 코드를 놓고 `current`
심링크를 옮길 뿐 `/etc`를 건드리지 않는다. 그리고 그것이 옳다 — 업데이트 채널이 자기
신뢰 근원을 갱신할 수 있다면, 그 채널을 한 번 장악한 자가 **영구히** 발행자가 된다.

닭-달걀이므로 **회전 공지는 대역외**다. 설치 부트스트랩에서 지문을 대역외로 대조하게
한 것과 정확히 같은 이유이며, 같은 이유로 그룹 채널·업데이트 채널로 지문을 보내면
안 된다.

### 3.3 전환기에 두 키를 병기할 수 있는가 → 있다

신뢰 파일 형식이 이미 여러 줄을 지원한다.

- `render_allowed_signers(entries, target)`는 `Sequence[SignerEntry]`를 받아 줄마다
  하나씩 렌더한다.
- `parse_allowed_signers(text)`는 주석·빈 줄을 건너뛰고 줄마다 엔트리를 만든다.
- 실제 검증은 `git verify-tag`의 `gpg.ssh.allowedSignersFile` 의미 그대로이므로
  **어느 한 줄이라도 맞으면 통과**한다.
- 설치 검증(`trust_key_bootstrap verify --expect-fingerprint`)도
  `any(fingerprints_match(...) for entry in entries)`이므로, 여러 엔트리 중 하나만
  기대 지문과 같으면 PASS다.

같은 principal의 서로 다른 키 두 줄도 정상이다 — 엔트리는 `(principal, namespaces, key)`
이고 principal은 `update-trust@autophagy`로 고정이기 때문이다.

⚠️ **다만 `trust_key_bootstrap install`은 병기 수단이 아니다.** `plan_signer_install`이
**단일 엔트리**로 파일 전체를 렌더해 교체하므로, 그것으로 새 키를 넣으면 구 키가
사라진다 — 정확히 §3.1이 금지하는 상태다. 전환기 파일은 손으로 조립한다
(`docs/follow-ups.md`에 `--add` 서브커맨드를 후속으로 등록해 두었다).

전환기 파일의 정확한 형태(헤더는 `UPDATE_TRUST_TARGET.header` 그대로):

```
# autophagy UPDATE TRUST key — verifies public-repo release tags (plan D8).
# NOT the group skill signing key (/etc/autophagy/managed-skills-allowed-signers).
# Installed by automation/install/trust_key_bootstrap.py; root:root 0644.
update-trust@autophagy namespaces="git" ssh-ed25519 AAAA...<구키> update-trust@autophagy
update-trust@autophagy namespaces="git" ssh-ed25519 AAAA...<신키> update-trust@autophagy
```

`root:root`, 모드 정확히 `0644`, 부모 디렉터리도 root 소유이고 group/other 쓰기 비트가
없어야 한다. 하나라도 어긋나면 `TRUST-KEY-WRONG-OWNER` · `TRUST-KEY-WRITABLE` ·
`TRUST-KEY-WRONG-MODE`로 FAIL이다. 파일이 아예 없으면
`TRUST-KEY-MISSING: <path>가 없어 새 릴리스 검증을 fail-closed 거부한다`.

### 3.4 절차 (순서 고정)

1. **새 키페어 생성** (워크스테이션에서만).
   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/autophagy_update_trust_2 -C "update-trust@autophagy"
   ssh-keygen -lf ~/.ssh/autophagy_update_trust_2.pub     # 새 지문 확보
   ```
2. **병기 파일을 만든다** (§3.3 형식). 구키 줄을 **지우지 않는다.**
3. **모든 배포된 노드에 병기 파일을 배포**하고, 노드마다 **두 지문 각각**으로 확인한다.
   ```bash
   python3 automation/install/trust_key_bootstrap.py verify --expect-fingerprint 'SHA256:<구키>'
   python3 automation/install/trust_key_bootstrap.py verify --expect-fingerprint 'SHA256:<신키>'
   ```
   정상이면 마지막 줄이 `--- TRUSTED: 2건 중 실패 0 / 경고 0`이고 rc 0이다. 지문이 하나도
   맞지 않으면 `[FAIL] trust-key.fingerprint: TRUST-KEY-FINGERPRINT-MISMATCH: 설치본 ... != 공지 ...`과
   `--- NOT-TRUSTED: ...`, rc 1이다. **둘 다 통과해야** 병기가 확인된 것이다 — 하나만
   확인하면 다른 하나가 지워진 것을 못 본다(검증은 `any(...)`라 한 줄만 맞아도 PASS다).
   제3자 설치는 **대역외 공지 + 각 소유자의 수동 실행**이다(§3.2).
4. **전 노드에서 3이 끝났음을 확인한다.** 여기가 유일한 게이트다. 확인되지 않은 노드가
   하나라도 있으면 그 노드는 5 이후 조용히 멈춘다.
5. **새 키로 다음 릴리스를 컷한다.** §1.4의 명령에서 `--signing-key`만 바꾼다.
   스크립트가 push **전에** 자기 공개키로 태그를 자체 검증하므로, 키를 잘못 지정했으면
   `release tag does not verify as update-trust@autophagy in namespace git`에서 멈춘다.
6. **노드들이 그 릴리스로 전진한 것을 확인한 뒤**(`readlink /srv/autophagy-agent-current`),
   구키 줄을 뺀 파일을 배포한다. 이 단계는 단일 엔트리 교체가 곧 원하는 동작이므로
   설치기 CLI를 그대로 쓸 수 있다.
   ```bash
   sudo python3 automation/install/trust_key_bootstrap.py install \
       --key ~/.ssh/autophagy_update_trust_2.pub \
       --expect-fingerprint 'SHA256:<신키>'
   ```
   `--dry-run`을 먼저 붙여 계획(경로·소유·모드·principal·지문·내용)을 눈으로 본다.
7. **구 개인키를 폐기한다.** 백업 사본까지 포함해서.

⚠️ 3을 건너뛰고 5를 먼저 하면 **그 순간 모든 노드가 조용히 멈추고, 업데이트 채널로는
고칠 수 없다** — 각 노드에 수동으로 들어가야 한다. 연합 규모에 비례해 비용이 커지는
유일한 실수다.

⚠️ 회전 중에도 **릴리스 버전은 앞으로만 간다**(§5.1의 floor). 회전이 다운그레이드 예외를
만들지 않는다.

### 3.5 유출로 인한 긴급 회전

순서를 줄일 수 없다는 사실이 여기서 가장 아프다. 공격자가 이미 서명 능력을 가졌다면,
**구키 줄을 각 노드에서 제거하는 것**만이 즉효 조치인데 그것도 수동이다. 절차는 §3.4와
같되 6을 앞당겨 — 병기 없이 **신키만 담은 파일로 곧장 교체**하고, 교체가 끝난 노드부터
새 릴리스를 받게 한다. 교체 전 노드는 그동안 전진하지 않는다(그것이 원하는 상태다).

그래서 §2.3의 보관 규율이 이 절차보다 중요하다. 회전은 사고를 수습하지 못하고, 사고를
겪은 뒤에 하는 일일 뿐이다.

---

## 4. push 후 사용자 노드에서 실제로 일어나는 일

타이머는 `automation/systemd/autophagy-deploy-reconcile.timer` —
`OnBootSec=2min`, `OnUnitActiveSec=2min`, 실행 주체는 `ops`.

한 틱(`automation/deploy_reconcile_cli.py::main`)은 이렇게 흐른다.

1. roster의 `update_channel`을 읽는다. 없으면(v1 기본값) 노드의 `origin`을 쓴다.
2. **대상 SHA 해석** — `require_signed_updates`면 `resolve_signed_update`:
   - `git ls-remote <remote> refs/heads/main refs/tags/*`
   - `parse_remote_release_refs`: **peeled commit이 `main`과 같은** annotated 태그만
     후보, 이름 역순 정렬. 후보 0이면 `UNSIGNED-HEAD`.
   - 후보마다: 임시 bare repo에 `--depth=1`로 그 태그만 fetch → 태그 객체 SHA 재대조
     (`TAG-RACE: release tag changed while verifying: <tag>`) →
     `git verify-tag`(allowedSignersFile=`/etc/autophagy/update-allowed-signers`) →
     principal exact match → peeled commit 재대조.
   - 성공 즉시 **롤백 방지 floor를 전진**시킨다(§4.1). 순서가 중요하다 — 검증되지 않은
     태그 이름이 floor를 움직일 수 있으면, 서명 없이 `v99.0.0`을 밀어 넣는 것만으로
     채널을 영구히 닫을 수 있다.
3. 해석 실패는 **드리프트가 아니다.** `UPDATE-TRUST-BLOCK ... — skipping tick`을 찍고
   rc 0으로 끝난다. 잠깐의 네트워크 장애로 사건을 만들지 않기 위한 선택이다.
4. 전진이 필요하면 `apply_release_update`(`automation/release_rollback.py`):
   - `current`가 예상 prior가 아니면 `LOCK_CONTENTION_RC` — 다른 수렴이 진행 중이다.
   - **converge** — root 헬퍼가 서명을 **독립적으로 다시 검증**한다(ops pre-gate만으로는
     검증 직후 mutable `main`이 바뀌는 TOCTOU가 남는다).
   - `current`가 목표 SHA로 실제 전환됐는지 확인.
   - **agent + peer 게이트웨이를 함께 재시작**한다(`autophagy-gateway-pair restart`,
     상한 120초). 한쪽만 재시동하지 않는다 —
     [operations.md](operations.md)의 게이트웨이 재시동 규칙.
   - `automation/deploy-smoke.sh` 실행(상한 900초).
   - 성공 → 실패 지문 파일 삭제, rc 0.
5. 재시작 또는 스모크가 실패하면 **롤백 트랜잭션**:
   - 실패 지문을 `<private_root>/deploy-reconcile/failed-release.json`(0600)에 원자
     기록: `failed_sha` · `prior_sha` · `reason`(`gateway-restart` 또는 `deploy-smoke`) ·
     `phase`(`rollback-pending`/`failed`) · `notice_sent`.
   - `autophagy-install-release rollback --failed-sha <..> --sha <prior>`로 `current`
     원자 복귀.
   - **양 게이트웨이 재시작 + health 재확인** — 포인터만 되돌리면 새 릴리스로 기동된
     프로세스가 그대로 남는다.
   - 기존 owner notice 표면으로 **정확히 1회** 알림. 실제 문구:
     `업데이트 검증 실패로 이전 릴리스 자동 롤백을 시작했습니다.` + 실패 릴리스 · 복귀
     릴리스 · 실패 단계 · 게이트웨이 복구 여부.
   - 복구가 미완이면 `rollback-pending`으로 남아 **다음 틱이 이어서 시도**한다.
6. 같은 `failed_sha`는 다시 시도하지 않는다. **그 릴리스에 대해서만** 멈춘 것이며,
   **다른 SHA의 다음 릴리스가 오면 정상 진행**한다. 이것이 §5.2가 성립하는 이유다.

> **유지보수자가 보는 것은 아무것도 없다.** 이 알림은 그 설치의 소유자에게 간다.
> 연합에는 유지보수자가 남의 노드를 관측하거나 조작하는 경로가 없다 — 설계상 그렇다.
> 그래서 나쁜 릴리스에 대한 나의 조치는 "노드를 고친다"가 아니라 "빨리 다음 릴리스를
> 낸다"이다.

---

## 5. 나쁜 릴리스 대응

### 5.1 ⚠️ 옛 태그를 다시 올려 되돌릴 수 **없다**

`automation/update_trust_state.py`가 검증 성공 시점마다 **롤백 방지 floor**를
`<private_root>/deploy-reconcile/release-floor.json`(0600)에 기록한다 —
`tag` · `commit_sha` · `major.minor.patch` 삼중항.

`refuse_release_rollback`의 판정:

| 상황 | 결과 |
|---|---|
| 후보 ordering < floor ordering | `RELEASE-ROLLBACK: release <새> does not advance verified release <옛>` |
| ordering은 같은데 tag나 commit이 다름 | `RELEASE-ROLLBACK: release <새> reuses the version of <옛> at another commit` |
| tag·commit 모두 동일 | 통과 (같은 틱에서 두 검증 경로가 같은 릴리스를 해석하므로 필요하다) |

즉 **v1.0.5가 나빴다고 v1.0.4를 다시 공개 `main`에 올려도, 이미 v1.0.5를 검증한 노드는
거부한다.** 태그를 지우고 같은 번호를 다른 커밋에 다시 붙여도 두 번째 규칙에 걸린다.

**이 비대칭은 의도된 것이다.** floor는 서명 능력이 없는 공격자가 origin의 `main`을
과거의 (진짜로 서명됐던) 커밋으로 force-push해 알려진 취약 버전으로 "업그레이드"시키는
공격을 막는 장치다(감사 C1, TUF rollback / CWE-345). 유지보수자의 취소 편의를 위한
장치가 아니고, 그 둘을 동시에 만족시킬 수는 없다.

부수적으로: **pre-release 접미사는 순서를 만들지 않는다.** ordering은
`major.minor.patch` 삼중항만 본다. `v1.0.1-rc1`과 `v1.0.1`은 같은 삼중항이라 서로를
전진시키지 못하며, 이때의 복구는 다음 patch 번호를 컷하는 것이다.

### 5.2 그래서 실제로 하는 일 — **앞으로 나가서 되돌린다**

"내리는 것(un-ship)"은 언제나 "앞으로 내보내는 것"으로 한다. 뒤로 가는 경로는 없다.

1. **private에서 고친다.** 문제 커밋을 `git revert`하거나 수정 커밋을 만든다. 평소대로
   커밋 → 푸시 → `origin/main` 랜딩(「작업 종결 규칙」).
2. **버전을 올려 새 릴리스를 컷한다.** v1.0.5가 나빴으면 **v1.0.6**이다. v1.0.5를
   재사용하면 `destination tag already exists: v1.0.5`로 막히고, 설령 원격 태그를 지워도
   §5.1의 두 번째 규칙에 걸린다.
3. **릴리스 노트에 명시한다** — 무엇을 되돌렸는지, 어떤 버전 범위가 영향을 받는지,
   노드 소유자가 확인할 것이 있는지. 연합에는 유지보수자가 밀어 보낼 수 있는 알림
   채널이 없다. **공개 릴리스 노트가 유일한 방송 수단이다.**
4. **수렴을 기다린다.** v1.0.5로 이미 전진한 노드는 v1.0.6으로 전진한다. v1.0.5의
   스모크 실패로 **자동 롤백돼 v1.0.4에 머문 노드도** v1.0.6으로 전진한다 — 실패 지문은
   그 `failed_sha`만 막기 때문이다(§4-6).

### 5.3 순서

**롤백(노드가 자동으로 한다) → 원인 확정 → 상위 버전 릴리스 → 공지.**

- 공지를 수정보다 **먼저 하지 않는다.** 조율된 공개(coordinated disclosure)가 기본이며
  보고자가 있으면 공개 시점을 합의한다([`SECURITY.md`](../../SECURITY.md)).
- 원인 확정 전에 재시작·설정변경·키 재발급을 하지 않는다
  ([incident-response.md](incident-response.md) — 내 노드에 대한 규율이며 이 문서가
  다시 쓰지 않는다).

### 5.4 공개 저장소에서 태그를 지우는 것

**지우지 않는다.** 이미 검증한 노드의 floor는 로컬에 남아 있어 태그 삭제로 되돌아가지
않고, 삭제는 감사 추적만 잃는다. 관리형 스킬 채널이 "upstream 태그 삭제는 취소가
아니다"(decision 16)로 같은 결론을 이미 채택하고 있다. 회수는 삭제가 아니라 **상위
버전 발행**이다.

---

## 6. 보안 신고 접수·대응

접수 경로·응답 약속·심각도 기준·지원 범위는 [`SECURITY.md`](../../SECURITY.md)가
단독으로 소유한다. 여기서 더하는 것은 유지보수자 쪽 순서 한 줄뿐이다.

**접수 → 판정 → 수정은 §1의 릴리스 절차로 → 되돌림이 필요하면 §5로 → 공지는 마지막.**

기억할 점 둘:

- **지원 대상은 최신 릴리스 태그 하나뿐이고 백포트는 없다.** 그래서 보안 수정도 §5.2와
  같은 "앞으로 나가기"다. 옛 태그에 패치를 얹는 선택지는 존재하지 않는다.
- 신고자에게 진행 상황을 계속 공유하고, 목표(접수 확인 5영업일 / 초기 판정 10일)를
  못 지킬 상황이면 침묵하지 않고 알린다.

---

## 7. 스키마 버전을 올려야 할 때

판단 기준·마이그레이션 정책·운영자 대응 순서는
[versioning-support.md](versioning-support.md)가 단독으로 소유한다. 여기서 더하는 것은
릴리스를 컷하는 사람의 의무 하나다.

**스키마 상승이나 필수 설정 필드 추가는 MAJOR다.** "운영자 조치가 필요하면 MAJOR"가
기준인데, 그것이 MINOR로 조용히 들어오면 자동 업데이트가 노드를 멈춰 세우고 노드
소유자는 그것을 릴리스 번호로 예측할 수 없다.

**MAJOR 릴리스 노트에는 반드시 셋을 적는다**: ① 무엇이 거부되는가 ② 어떤 오류 문자열이
보이는가 ③ 운영자가 무엇을 하면 되는가. 노드 소유자가 가진 정보원은 릴리스 노트뿐이다.

---

## 8. 체크리스트

한 번의 릴리스를 한 화면으로:

- [ ] 변경이 private `origin/main`에 랜딩됐다 (커밋 → 푸시)
- [ ] `configs/` · `docs/guide/` · `docs/patch/`에 새 파일이 있으면 매니페스트 또는
      공개 원장에 기록했다 → `pytest tests/unit/test_public_export_manifest_coverage.py`
- [ ] `pytest tests/unit` 전체 GREEN (export가 내보낸 트리에서 다시 돌린다)
- [ ] 대상 저장소가 존재한다 (최초 1회만 `gh repo create ... --public`)
- [ ] 워킹트리 clean, 대상 디렉터리는 체크아웃 **밖**의 새 경로
- [ ] `automation/public_export.sh` 1회 실행 → `PUBLIC-EXPORT-OK`
- [ ] GitHub Release 노트 게시 (**지문 재게시** + MAJOR면 조치 안내)
- [ ] 공개 저장소에서 `git verify-tag <version>` 통과
- [ ] 내 노드 `readlink /srv/autophagy-agent-current`가 2분 내 전진
- [ ] 공개 저장소에 손으로 push한 것이 없다

---

## 관련

- [`SECURITY.md`](../../SECURITY.md) — 취약점 보고·응답·지원 범위
- [versioning-support.md](versioning-support.md) — 버저닝 규칙·스키마·마이그레이션
- [install.md](install.md) — 설치 절차의 단일 진실 (지문 대조 포함)
- [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md) — 제3자 설치 전제
- [manual-group-admin.md](manual-group-admin.md) · [manual-member.md](manual-member.md)
- [operations.md](operations.md) · [incident-response.md](incident-response.md) ·
  [reboot-recovery.md](reboot-recovery.md) — 내 노드 운영 *(개발 저장소 전용 — 공개본에
  포함되지 않는다)*
- 핵심 코드: `automation/public_export.sh` · `automation/update_trust.py` ·
  `automation/update_trust_state.py` · `automation/deploy_reconcile_cli.py` ·
  `automation/release_rollback.py` · `automation/install/trust_key_bootstrap.py` ·
  `automation/install/allowed_signers.py`
