# 그룹 관리자 매뉴얼

**독자**: 새 연구그룹을 열려는 사람. 팀원들에게 공통 스킬을 배포하고 싶지만, 팀원의
설치를 소유하거나 대리 운영할 생각은 없는 쪽이다.

이 문서는 **그룹 관리자 쪽 절차만** 소유한다. 노드 설치 자체(계정·신뢰키·타이머·
healthcheck)는 [install.md](install.md)가 단독으로 소유하므로 여기서 반복하지 않는다.
관리자도 자기 노드를 쓰려면 그 문서를 그대로 따라 설치한다. 팀원이 볼 문서는
[manual-member.md](manual-member.md)다.

---

## 1. 그룹을 여는 데 필요한 것은 정확히 3개다

그룹은 서비스가 아니라 **공급망·정책 경계**다. 중앙 서버도, 멀티테넌트 호스팅도,
공용 계정도 없다. 물리적 구성물은 정확히 셋뿐이다.

1. **관리형 스킬 저장소** — 관리자가 소유한 git repo 하나.
2. **서명키 1쌍** — 개인키는 관리자만, 팀원은 공개키만 갖는다.
3. **roster** — 팀원 명단 YAML 파일 하나.

이 셋 말고 그룹을 표현하는 서버·DB·중앙 서비스는 없고, 앞으로도 만들지 않는다.
아래 절차 어디에도 "그룹 서버를 세운다"는 단계가 없는 것은 빠뜨린 게 아니라 설계다.

한 설치는 v1에서 **하나의 그룹**에만 속한다.

---

## 2. 스킬 repo와 서명키

### 2.1 관리형 스킬 저장소

빈 git repo 하나를 만들고(GitHub든 사내 GitLab이든 상관없다) 관리자 워크스테이션에
클론한다. **이것은 새 repo다** — 소프트웨어 개발 repo도, 사용자가 설치할 때 받아가는
공개 배포본도 아니다. 세 것은 가시성·서명키·검증 코드가 모두 다른 별개 채널이며,
그 구분은 루트 `AGENTS.md`의 「세 저장소 구분 규칙」이 표로 들고 있다.
구조는 발행 도구가 알아서 만든다 — 미리 만들어 둘 디렉터리는 없다.
발행이 진행되면 repo 안에 이렇게 쌓인다.

```
skills/managed-<name>/        # 발행된 스킬 소스 사본
manifests/managed-<name>.json # 릴리스 매니페스트
refs/tags/managed-<name>/v<N> # SSH 서명된 릴리스 태그
refs/heads/roster             # (선택) 서명된 roster 배포 브랜치 — §4.4
```

팀원은 이 repo에 **read-only deploy key**로만 접근한다. write 권한을 주는 사람은 없다.

> 관리형 스킬 이름은 반드시 `managed-` 접두사로 시작한다. 일반 스킬은 이 접두사를
> 쓸 수 없고, 이름이 충돌하면 양방향 fail-closed로 차단된다.

### 2.2 서명키 생성

그룹 서명키는 관리자 워크스테이션에서 만든다.

```bash
ssh-keygen -t ed25519 -C 'managed-skills signing' -f ~/.ssh/managed_skills_signing
ssh-keygen -lf ~/.ssh/managed_skills_signing.pub
```

두 번째 명령이 출력하는 `SHA256:...` 값이 **지문(fingerprint)** 이다. 팀원이 이 값을
대역외로 확인한다(§4.2). 지원되는 키 알고리즘은 `ssh-ed25519`,
`sk-ssh-ed25519@openssh.com`, `ecdsa-sha2-nistp256`, `ssh-rsa`다.

### 2.3 ⚠️ 개인키는 관리자 워크스테이션 밖으로 나가지 않는다

**서명 개인키(`~/.ssh/managed_skills_signing`)를 어떤 노드에도 배포하지 않는다.**
프로덕션 노드에도, agent 계정에도, 백업 서버에도, CI에도 두지 않는다. 노드에 놓이는
것은 언제나 **공개키뿐**이다(`/etc/autophagy/managed-skills-allowed-signers`, root
소유 0644).

이유는 단순하다. 이 개인키를 가진 자는 곧 그 그룹의 발행자다. 팀원 노드는 이 키의
서명만 보고 코드를 받아들이므로, 키가 노드에 있으면 그 노드를 장악한 자가 팀 전체에
코드를 밀어 넣을 수 있다. 노드는 인터넷에 붙어 있고 에이전트가 돌아가는 곳이며,
워크스테이션보다 침해 표면이 훨씬 넓다.

실무 규칙:

- 패스프레이즈를 걸고 `ssh-add ~/.ssh/managed_skills_signing`으로 ssh-agent에 올린다.
  패스프레이즈는 워크스테이션을 벗어나지 않는다.
- 발행은 **에이전트 런타임 환경에서 실행하지 않는다.** 환경에 `DISCORD_BOT_TOKEN`이
  있으면 `publish_cli`가 agent 런타임 오실행으로 간주해 거부한다(SI-5). Discord 토큰이
  필요하면 0600 파일에 담아 `--discord-token-file`로 넘긴다.
- 키를 잃어버리면 새 키를 만들고 §4의 핸드셰이크를 팀원 전원과 다시 한다. 우회로는
  없다 — 그게 이 설계의 요점이다.

### 2.4 발행자 신원 config

발행 도구는 자기가 **누구로서** 발행하는지를 로컬에서 알아야 한다. roster가 아니라
관리자 자신의 config가 그것을 선언한다(roster는 구독자가 읽는 문서이고, 발행 도구는
roster가 존재하기 전에도 자기 신원을 알아야 한다).

```bash
mkdir -p ~/.hermes/managed-skills
cp configs/managed-publisher.default.json ~/.hermes/managed-skills/publisher.json
chmod 600 ~/.hermes/managed-skills/publisher.json
```

그리고 두 값을 자기 것으로 채운다.

```json
{
  "publisher": "gildong",
  "publisher_principal": "publisher-gildong@autophagy"
}
```

`publisher_principal`은 `publisher-<slug>@autophagy` 형식이어야 한다. **기본값은
없다** — 파일이 없거나 형식이 틀리면 발행이 `PUBLISH-BLOCK`으로 거부된다. 경로는
`MANAGED_PUBLISHER_CONFIG`로 덮어쓸 수 있다.

---

## 3. roster 작성과 검증

roster는 "이 그룹이 누구를 신뢰하는가"를 선언하는 **유일한 문서**다. 팀원 노드는 이
파일 하나로 발행자 principal과 서명 공개키를 해석한다.

시드를 복사해서 시작한다.

```bash
cp configs/roster.example.yaml ~/.hermes/roster.yaml
```

채워야 하는 것:

```yaml
schema: 1
revision: 1                                          # 서명 배포할 때마다 올린다(§4.4)
group_id: gildong-lab
admin:
  name: Hong Gildong
  discord_user_id: "실제 Discord 사용자 ID"
  publisher_principal: publisher-gildong@autophagy   # §2.4와 동일해야 한다
  signing_public_key: >-
    ssh-ed25519 AAAA... managed-skills-signing        # §2.2 공개키 한 줄 그대로
members: []
# update_channel은 생략한다 — 소프트웨어 업데이트는 업스트림에서 직접 받는다.
# announce_channel_id도 생략하면 발행 공지를 Discord에 올리지 않는다(§5.3).
```

`revision`은 **서명해서 배포하는 roster마다 반드시 1씩 올리는 단조 카운터**다. 서명은
누가 썼는지를 증명할 뿐 **언제 썼는지는 증명하지 않는다** — 이 값이 없으면 서명 능력이
없는 feed 호스트도 `roster` 브랜치를 되감아 **예전에 정상 서명된 roster를 다시 내보내는
것만으로 멤버 제거를 되돌릴 수 있다**. 팀원 노드는 이미 가진 revision보다 크지 않은
roster를 `ROSTER-ROLLBACK`으로 거부한다(같은 번호의 다른 roster도 거부한다). 손으로
파일을 전달하는 설치는 이 필드를 생략해도 기존과 동일하게 동작하지만, 한 번 revision이
붙은 roster를 받은 설치는 그 뒤로 revision 없는 roster를 다운그레이드로 보고 거부한다.

`update_channel`을 **생략하는 것이 v1 기본값**이다. 이것을 비워 두면 팀원의 소프트웨어
업데이트는 업스트림에서 직접 온다. 그룹 관리자는 스킬 채널의 주인이지 소프트웨어
유지보수자가 아니며, 그 둘을 섞지 않는 것이 의도다.

의도적으로 별도 소프트웨어 fork를 운영할 때만 `update_channel`에 그 Git URL을 적는다.
팀원 리컨실러는 이 값이 있을 때만 해당 원격을 사용하고, 생략하면 기존 `origin` 동작을
그대로 유지한다. 별도 채널도 현재 `main`과 같은 commit을 가리키는 신뢰된 서명 릴리스
태그가 있어야 하며, roster 관리자의 **스킬 서명키가 소프트웨어 업데이트 신뢰키를
대신하지 않는다**. 외부효과 승인은 채널과 무관하게 각 팀원 본인의 owner gate에 남는다.

검증한다.

```bash
python3 -m automation.group_roster validate ~/.hermes/roster.yaml
```

정상이면 rc 0과 함께 한 줄이 나온다(실측):

```
ROSTER-VALID group_id=example-lab members=2 update_channel=upstream announce_channel=none
```

문제가 있으면 rc 2와 `ROSTER-INVALID: <사유>`가 stderr로 나온다. 미지 필드, 중복 YAML
키, 중복 Discord ID, 잘못된 shape가 모두 여기서 걸린다. **rc 0을 받기 전에는 다음
단계로 가지 않는다.**

---

## 4. 멤버 초대 — 3단계 핸드셰이크

새 팀원 한 명을 들이는 절차는 정확히 세 단계다. 팀원 쪽 절차는
[manual-member.md](manual-member.md)에 있고, 여기에는 관리자가 하는 몫만 적는다.

### ① 팀원이 자기 deploy 공개키를 제출한다

팀원이 [install.md](install.md)의 설치를 마치면 설치기가 `check
deploy-key-registration`에서 그 설치의 **읽기 전용 deploy 공개키 한 줄**과 지문을
출력한다(`GROUP-JOIN-DEPLOY-PUBLIC-KEY access=read-only`). 개인키는 출력되지 않고 그
설치에 남는다. 팀원이 관리자에게 보내는 것은 그 공개키 줄이다.

관리자는 그 키를 **관리형 스킬 repo에 read-only deploy key로** 등록한다. write 권한을
주지 않는다. 등록은 git 호스팅 제공자의 관리 화면에서 하는 일이고, CLI가 대신해 주지
않는다 — 그래서 `add-member`도 그것을 성공한 척하지 않고 요구사항만 출력한다.

### ② ⚠️ 관리자가 서명 공개키 지문을 대역외로 알려준다

**서명 공개키의 지문(`SHA256:...`)은 반드시 대역외(out-of-band)로 전달한다. 그룹
Discord 채널로 보내지 않는다.**

이유를 정확히 말한다. 팀원 노드는 그 지문으로 "이 공개키가 진짜 관리자 것인가"를
판정한다. 지문을 그룹 Discord로 보내면, 그 채널을 장악한 자가 **자기 공개키와 자기
지문**을 대신 흘려보낼 수 있고, 그 순간 그 사람이 곧 그 그룹의 발행자가 된다. 팀원은
검증을 정상적으로 통과했다고 믿는다 — 검증 자체가 공격자의 값을 기준으로 이뤄지기
때문이다. 즉 신뢰의 근원을 그것을 검증하려는 채널로 나르면 검증은 아무것도 하지
않는다.

대역외란 **스킬 채널과 독립적인 경로**를 뜻한다. 직접 만나서 읽어 주기, 전화로 불러
주기, 종이에 적어 건네기, 이미 신뢰가 확립된 별개 매체. 코드가 이것을 강제할 수는
없으므로 — 이 한 단계는 사람이 지키는 것이다. 도구는 대신 곳곳에
`GROUP-DISCORD-FORBIDDEN`을 찍어 잊지 않게 한다.

팀원 쪽에서는 이렇게 쓰인다(팀원이 실행하는 명령이며, 관리자가 대신 실행하지 않는다):

```bash
sudo python3 -m automation.install --config <node.toml> \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:<업스트림-공지-지문>' \
    --group-roster <받은 roster.yaml> \
    --expect-group-skill-fingerprint 'SHA256:<대역외로 받은 관리자 지문>'
```

두 그룹 옵션은 **함께**여야 한다. 지문이 없으면
`GROUP-TRUST-FINGERPRINT-REQUIRED`, roster의 키와 다르면
`GROUP-TRUST-FINGERPRINT-MISMATCH`로 **파일을 쓰기 전에** 차단된다. 통과하면
`/etc/autophagy/managed-skills-allowed-signers`(root:root 0644)에 한 줄이 설치된다:

```
<admin.publisher_principal> namespaces="git,autophagy-roster" <admin.signing_public_key>
```

`git`은 관리형 스킬 릴리스 태그를, `autophagy-roster`는 서명된 roster 스냅샷을
검증한다. 업데이트 신뢰키는 계속 별도 파일
(`/etc/autophagy/update-allowed-signers`, principal `update-trust@autophagy`)이며 두
파일을 섞지 않는다.

### ③ 관리자가 roster에 등록한다

```bash
python3 -m automation.group_roster add-member ~/.hermes/roster.yaml \
    --name 'Hong Gildong' \
    --discord-user-id 1004 \
    --node-label gildong-node
```

실측 출력:

```
ROSTER-MEMBER-ADDED group_id=example-lab discord_user_id=1004 node_label=gildong-node status=active
DEPLOY-KEY-REGISTRATION-REQUIRED node_label=gildong-node access=read-only
```

두 번째 줄은 ①의 deploy key 등록이 **아직 관리자의 손일**을 상기시키는 것이다. CLI는
원격 repo의 키 등록을 하지 않는다. 이미 있는 Discord ID를 다시 넣으면 rc 2와
`ROSTER-EDIT-REFUSED: MEMBER-EXISTS: ...`로 거부된다.

등록 후 다시 검증한다.

```bash
python3 -m automation.group_roster validate ~/.hermes/roster.yaml
```

`node_label`은 단순한 표시명이 아니다 — 그것이 그 팀원 설치의 **보고 신원**이다.
팀원 에이전트가 보내는 봉투의 `sender_id`·보고의 `agent_id`가 이 값과 정확히 일치할
때만 다른 팀원의 RAG에 적재된다. 아무 값이나 넣지 말고 그 설치가 쓸 값을 팀원과
합의해서 넣는다.

### ④ (선택) roster를 서명해서 배포한다

roster를 갱신할 때마다 팀원에게 파일을 손으로 다시 보내는 게 싫다면, 스킬 repo의
`refs/heads/roster` 브랜치에 서명해서 올린다. 팀원 노드의 sync 틱이 그것을 가져와
검증한 뒤에만 로컬 roster를 교체한다.

브랜치에는 정확히 두 파일이 있어야 한다.

```
roster/roster.yaml
roster/roster.yaml.sig
```

서명은 namespace `autophagy-roster`로 만든다(실측 검증한 왕복):

```bash
ssh-keygen -Y sign -f ~/.ssh/managed_skills_signing -n autophagy-roster roster/roster.yaml
# → roster/roster.yaml.sig 생성
```

확인하고 싶으면 같은 키로 되짚어 본다.

```bash
printf '%s namespaces="git,autophagy-roster" %s\n' \
    publisher-gildong@autophagy "$(cat ~/.ssh/managed_skills_signing.pub)" > /tmp/signers
ssh-keygen -Y verify -f /tmp/signers -I publisher-gildong@autophagy \
    -n autophagy-roster -s roster/roster.yaml.sig < roster/roster.yaml
# → Good "autophagy-roster" signature for publisher-gildong@autophagy ...
```

두 파일을 `roster` 브랜치에 커밋·push한다. 검증에 실패한 roster는 팀원 노드에서
`ROSTER-REJECTED`로 버려지고 **기존 파일은 손대지 않는다**(last-known-good). roster
브랜치가 아예 없어도 스킬 배달은 정상 동작한다 — 두 fetch는 분리되어 있다.

**서명 전에 `revision`을 올렸는지 확인한다.** 이전에 배포한 값보다 크지 않으면 팀원
노드는 서명이 완벽해도 `ROSTER-ROLLBACK`으로 거부하고 기존 파일을 유지한다. 이것이
roster 되감기(replay) 방어다 — 공급망 서명은 **작성자**를 증명하고 revision은
**신선도**를 증명하며, 둘 중 하나만으로는 제거된 멤버가 되살아나는 것을 막지 못한다.
같은 이유로 §6의 멤버 제거(`status: removed`) 배포는 revision 증가를 반드시 동반한다.

---

## 5. 스킬 발행과 취소

### 5.1 사전 준비 (워크스테이션, 세션당 1회)

```bash
ssh-add ~/.ssh/managed_skills_signing                       # 패스프레이즈 1회
export APPROVAL_LOG_PATH=~/.hermes/skill-gate/approvals.jsonl
```

`~/.hermes/interop/config.json`(owner_id 등)이 필요하다. 없으면 게이트가 `FATAL:
interop config unreadable`로 중단한다. 비대화형 셸에서 ssh-agent를 못 찾으면
`export SSH_AUTH_SOCK=/run/user/$(id -u)/keyring/ssh`.

### 5.2 릴리스 changelog 파일

발행마다 JSON 파일 하나가 필요하다. 필수 키는 `changelog`, `breaking`,
`compatibility`이고 선택 키는 `migration`, `revoked_digests`다. 그 밖의 키가 있으면
거부된다.

```json
{
  "changelog": "첫 릴리스",
  "breaking": false,
  "compatibility": "schema v1",
  "migration": null,
  "revoked_digests": []
}
```

### 5.3 3단계 발행

발행은 **배포 게이트 증거 → 발행 요청 게시 → 발행 확정**의 3단계이며, 각 단계가
소유자 ✅를 요구한다. 정확한 명령과 옵션은 이 절차를 단독으로 소유하는
[managed-skill-channel.md §4 발행자](managed-skill-channel.md)에 있다. 요약하면:

```bash
# 1단계: 배포 게이트 증거 (<message_id>:<deploy_nonce> 확보)
SKILL_SRC_DIR=<소스경로> automation/deploy-skill.sh managed-<name> --approve-only --fresh

# 2단계: 발행 요청 게시 — 이 시점까지 스킬 repo는 전혀 바뀌지 않는다
python3 -m automation.managed_skills.publish_cli \
    --skill managed-<name> --managed-repo <스킬 repo 체크아웃> \
    --skills-src <소스경로> --changelog-file <release.json> \
    --signing-key ~/.ssh/managed_skills_signing \
    --approve-evidence <message_id>:<deploy_nonce> \
    --discord-token-file <0600 토큰파일> \
    --stage-publish-request
# → PUBLISH-STAGED message_id=... publish_nonce=... tag=managed-<name>/v<N>

# 3단계: ✅ 이후 확정 — 커밋·SSH 서명 태그·push·공지
python3 -m automation.managed_skills.publish_cli \
    ... (2단계와 동일 인자) ... \
    --publish-evidence <publish_message_id>:<publish_nonce>
# → PUBLISHED skill=managed-<name> tag=managed-<name>/v<N>
```

발행 전 스킬 repo 워크트리가 clean이어야 하고, 소스 트리는 repo **바깥**에 있어야
하며 symlink를 포함할 수 없다. 승인이 실패하면 워크트리는 변경 없이 그대로다.

팀원이 제출한 개인 스킬은 임의로 복사하지 않는다. 기존 공급망 승인 표면의 제출
메시지에서 tarball·매니페스트와 sha를 검토하고 ✅한 뒤, `publish_cli`의
`--submission-tarball`·`--submission-manifest`·`--submission-evidence` 입력을 사용한다.
이 입력도 위 3단계를 생략하지 않으며 `--skills-src`·`--changelog-file`과 함께 쓸 수 없다.
정확한 명령은 [managed-skill-channel.md §4 발행자](managed-skill-channel.md#발행자-publisher)가
단독 소유한다.

3단계 끝의 **공지**는 roster의 `announce_channel_id`가 가리키는 그룹 채널로 나간다.
그 필드를 비워 두면 공지를 하지 않는다(발행 자체는 정상이다 — 공지는 알림일 뿐
공급망 단계가 아니다). 같은 릴리스로 3단계를 다시 실행해도 **공지는 늘어나지 않는다** —
릴리스 내용에 바인딩된 원장이 이미 게시된 메시지를 그대로 돌려주며, 이것은 승인
메시지에 쓰는 단일성 기법을 그대로 재사용한 것이다. 상세:
[기능소개](../기능소개/그룹-발행-공지.md).

### 5.4 취소 (revocation)

취소는 별도 명령이 아니라 **다음 발행의 changelog**로 한다. 회수할 릴리스의
`skill_sha256`을 `revoked_digests`에 넣고 새 릴리스를 발행한다.

```json
{
  "changelog": "v3의 결함 수정, v3 회수",
  "breaking": false,
  "compatibility": "schema v1",
  "revoked_digests": ["<회수할 v3의 skill_sha256>"]
}
```

이번 릴리스 자신의 digest를 넣으면 승인 요청 전에 `SELF-DIGEST-RECLAIM`으로 거부된다.

취소가 팀원 노드에서 하는 일과 하지 않는 일:

- **한다**: 회수된 digest의 **활성화를 차단**한다. 아직 활성화 전이면 그것으로 끝이다.
- **한다**: 이미 활성화돼 있으면 소유자에게 **제거 요청**을 낸다 — 정확한 명령
  (`automation/deploy-skill.sh managed-<name> --remove`)을 적어서 보여 준다.
- **하지 않는다**: 자동으로 떼어내지 않는다(SI-7). 실제 제거는 그 설치의 소유자가
  자기 승인 게이트를 거쳐 실행한다.

---

## 6. 멤버 제거 — 그리고 그 한계

```bash
python3 -m automation.group_roster remove-member ~/.hermes/roster.yaml \
    --discord-user-id 1004
```

실측 출력:

```
ROSTER-MEMBER-REMOVED group_id=example-lab discord_user_id=1004 node_label=gildong-node status=removed
DEPLOY-KEY-REVOCATION-REQUIRED node_label=gildong-node access=read-only
ROSTER-REVOCATION-READY status=removed publish=signed-roster
REMOTE-RECALL-LIMIT mounted-skills=unchanged owner-removal-required=true
```

엔트리는 삭제되지 않고 `status: removed`로 바뀐다. 그 상태가 정본이다.

출력의 세 토큰이 관리자가 이어서 해야 할 일과 알아야 할 사실이다.

1. `DEPLOY-KEY-REVOCATION-REQUIRED` — **git 호스팅에서 그 설치의 deploy key를 직접
   폐기한다.** CLI는 원격 repo를 건드리지 않으므로 이걸 했다고 가장하지 않는다. 폐기
   후 그 노드의 새 fetch는 `Permission denied (publickey)`로 실패한다.
2. `ROSTER-REVOCATION-READY publish=signed-roster` — 갱신된 roster를 §4.4대로 서명해
   배포하면 남은 팀원들이 그 사람의 보고를 더 이상 받아들이지 않는다(제거된 멤버는
   principal이 해석되지 않아 봉투·보고가 적재 전에 거부된다).
3. `REMOTE-RECALL-LIMIT mounted-skills=unchanged owner-removal-required=true` — 아래.

### ⚠️ 이미 마운트된 스킬은 원격으로 회수할 수 없다 (D1.3)

**멤버를 제거해도, deploy key를 폐기해도, 그 노드에 이미 마운트된 관리형 스킬은 그대로
동작한다.** 관리자는 그것을 원격에서 떼어낼 수 없다. 지연되는 것도, 어려운 것도
아니고 — 그런 기능이 없고 앞으로도 만들지 않는다.

관리자가 할 수 있는 것의 정확한 경계는 이렇다.

| 관리자가 할 수 있는 것 | 효과 |
|---|---|
| deploy key 폐기 | 그 시점부터 **새** 릴리스 수신 차단 |
| roster에서 `status: removed` + 서명 배포 | 그 사람의 보고를 남은 팀원이 거부 |
| `revoked_digests` 발행 | 그 digest의 **활성화** 차단 + 소유자에게 제거 요청 표시 |
| 이미 마운트된 스킬 제거 | **불가능** — 그 설치의 소유자만 실행한다 |

이것은 결함이 아니라 "각자 자기 설치를 소유한다"의 직접적인 귀결이다. 원격 회수가
가능하다는 것은 곧 관리자가 팀원 노드에서 임의로 코드를 조작할 수 있다는 뜻이고, 그
권한이 존재하는 순간 그것을 탈취한 자도 같은 일을 할 수 있다. 회수 대신 남는 정직한
경로는 **연락해서 그 사람이 직접 제거하게 하는 것**이다.

---

## 7. 관리자가 할 수 **없는** 것

이 목록은 제약 사항 안내가 아니라 **설계 선언**이다. 팀원에게도 같은 내용을 알려
주는 것이 좋다.

- **팀원 설치의 외부효과를 대리 승인할 수 없다.** 메일 발송·캘린더 등록·문서 저장·
  스킬 마운트는 전부 그 설치 소유자 본인의 ✅를 요구한다. 승인 게이트는 팀원 것이고
  관리자에게는 그 표면 자체가 없다. 관리형 스킬이 도착해도 팀원의 ✅ 없이는 절대
  마운트되지 않는다 — 자동 활성화는 공급망 침해가 조용히 팀 전체로 퍼지는 경로이므로
  영구 비목표다.
- **팀원의 데이터를 볼 수 없다.** 자격증명·메일·노트·대화·RAG 색인은 모두 그 사람의
  호스트에 있다. 관리자가 조회할 API도, 중앙 저장소도 없다. 팀원이 명시적으로 보고를
  보내는 경우에만, 그 사람이 보내기로 한 내용만 보인다.
- **팀원의 개인 스킬 repo나 live 마운트를 볼 수도 지울 수도 없다.** 팀원이 명시적으로
  제출한 sha 고정 snapshot만 검토할 수 있다. 팀원이
  `deploy-skill.sh --personal`로 만든 스킬은 그 설치에만 존재하고, 관리형 스킬 릴리스나
  업스트림 업데이트로 덮이지 않는다.
- **이미 마운트된 스킬을 원격으로 회수할 수 없다** (§6).
- **소프트웨어 업데이트를 통제하지 않는다.** 업데이트는 업스트림에서 직접 오고 별도
  신뢰키(`update-trust@autophagy`)로 검증된다. 그룹 관리자는 스킬 채널의 주인이지
  소프트웨어 유지보수자가 아니다.

---

## 8. 관리자 체크리스트

새 그룹을 여는 순서를 한 화면으로:

- [ ] 관리형 스킬 repo 생성 (빈 repo 하나)
- [ ] 서명키 생성, 지문 기록 (`ssh-keygen -lf ...pub`) — **개인키는 워크스테이션에만**
- [ ] `~/.hermes/managed-skills/publisher.json` 작성 (§2.4)
- [ ] `~/.hermes/roster.yaml` 작성 → `group_roster validate` rc 0
- [ ] 팀원마다: 공개키 수령 → repo에 read-only deploy key 등록 → **지문 대역외 전달** →
      `add-member` → `validate`
- [ ] (선택) roster 서명 후 `roster` 브랜치 push
- [ ] 첫 스킬 발행 (§5.3) → 팀원 노드 quarantine 도착 확인 → 팀원이 자기 ✅로 활성화

---

## 관련

- 노드 설치 (관리자·팀원 공통): [install.md](install.md)
- 팀원용 매뉴얼: [manual-member.md](manual-member.md)
- 발행/구독 상세 런북과 안전 불변식: [managed-skill-channel.md](managed-skill-channel.md)
- roster 시드: [`configs/roster.example.yaml`](../../configs/roster.example.yaml)
- 발행자 config 시드: [`configs/managed-publisher.default.json`](../../configs/managed-publisher.default.json)
