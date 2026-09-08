# systemd 컨테이너에서 실제 설치 실행

폐기 가능한 Linux Docker 호스트의 저장소 루트에서 실행한다.

```bash
tests/e2e/install/systemd_container/run.sh --evidence-dir /tmp/install-systemd-qa
```

호스트에 Docker, Bash, Git, Python 3, OpenSSH `ssh-keygen`이 필요하다.
특권 컨테이너와 쓰기 가능한 호스트 cgroup을 사용하므로 **보안 샌드박스가 아니다**.
Ubuntu 24.04 이미지에는 설치기와 systemd에 필요한 패키지만 설치한다.
패키지 버전은 Ubuntu apt에서 결정하며 이미지의 콘텐츠 SHA256을 요약에 기록한다.
부팅 전에 알림 소켓을 열고 systemd의 `READY=1`을 받은 뒤
`systemctl is-system-running`이 `running` 또는 `degraded`인지 확인한다.
공유 제한 시간은 60초이며 고정 대기나 준비 상태 폴링은 하지 않는다.

## 기본 실행

기본 명령은 dry-run이 아니라 **실제 apply**다.

```bash
python3 -m automation.install --config /root/node.toml --update-trust-key /root/trust.pub
```

계정·linger·디렉터리·peer 증명 키 생성까지 진행하고 계정 및 키 상태를 별도로 검증한다.
기본값은 `--stub-hermes` 비활성화다. Hermes 실행 파일이나 게이트웨이를 제공하지
않으므로 깨끗한 컨테이너에서는 `check hermes-gateway`의 외부 전제 실패로 멈춘다.
설치기는 Hermes를 설치하지 않는다.

## Hermes 스텁으로 다음 경계 확인

```bash
tests/e2e/install/systemd_container/run.sh --stub-hermes --evidence-dir /tmp/harness-stub-qa -- \
  python3 -m automation.install.wizard --profile core \
  --origin-url https://github.com/orientpine/cytoplasm.git --node-name qa-node \
  --operator root --config /root/wizard-node.toml --update-trust-key /root/trust.pub --yes
```

1차 실행이 `hermes-gateway`에서 실패한 뒤에만 `/root/node.toml`의 agent·peer 계정,
홈, 게이트웨이 유닛 이름을 읽는다. 각 계정 소유의 `~/.local/bin/hermes`(0755)는
`--version`에 `hermes 0.0.0-harness-stub`를 출력하고 다른 요청은 rc=2로 거부한다.
`~/.config/systemd/user/hermes-gateway.service`는 `Type=simple`,
`ExecStart=/bin/sleep infinity`, `WantedBy=default.target`인 시험용 유닛이다.
`user@<uid>.service` 시작 완료 뒤 해당 계정과 `XDG_RUNTIME_DIR=/run/user/<uid>`로
`systemctl --user daemon-reload`와 `enable --now`를 실행한다.
그다음 **동일한 argv로 2차 apply**를 실행한다. 마법사 예시는 기본 계정·홈을 사용한다.
사용자 지정 명령도 `/root/node.toml`과 같은 계정·홈·유닛 설정을 사용해야 한다.

2차 실행은 실제 `check hermes-gateway`를 통과하고 `check discord-readiness`에서
멈춘다. `DISCORD_BOT_TOKEN`을 주입하지 않으므로 `discord_check.py`는 rc=2를 반환한다.
이 지점이 비밀정보 없는 컨테이너 검증의 상한이다. 스텁은 Hermes 런타임이나
Discord 인증·인텐트·권한·채널 접근을 검증하지 않는다. 토큰을 넣어 경계를 넘기지 않는다.
clone·배포 키 등록·타이머·최종 healthcheck·서명 업데이트 성공도 이 실행으로 증명하지 않는다.

## 증적과 정리

명령의 종료 코드를 그대로 반환한다. 외부 전제 실패의 **rc=1은 예상 결과**이며,
하네스 부팅·증적·변경 검증 실패에는 125를 쓴다.
`install-transcript.txt`는 전체 stdout/stderr를 보존한다. 스텁 모드에서는 두 실행 사이에
`===== HARNESS-PASS-2: Hermes 스텁 적용 후 재실행 =====` 구분자가 들어간다.
`summary.txt`는 이미지 ID, 고정 HEAD, 명령, rc, 부팅 상태, 소요 시간,
`stub_hermes=true|false`, 첫 실패, 실제 결과로 입증한 마지막 계획 항목을 기록한다.
스텁 모드의 `first_boundary`와 `highest_action_reached`는 **2차 실행만** 반영한다.
미리 출력된 전체 계획의 마지막 항목을 실행 성공으로 세지 않는다.
빌드·부팅·설정 메모·기본 명령의 변경 검증은 별도 파일에 남긴다.
같은 증적 디렉터리를 재사용하면 기존 실행 파일을 덮어쓴다.

저장소는 시작 시 고정한 **커밋된 HEAD만** 복사한다. 로컬 편집, `.git`, `.omo`,
`.venv`, `.env.secrets`는 복사하지 않으며 이미지 빌드 입력도 Dockerfile뿐이다.
시험용 신뢰 개인키는 출력하거나 컨테이너에 복사하지 않고 공개키 복사 직후 삭제한다.
호스트의 비밀 환경변수는 전달하지 않는다. 설정은 컨테이너 호스트명과 root 운영자를 쓴다.
빈 `deploy_ssh_host`를 파서가 거부하면 컨테이너 호스트명으로 대체하고
`config-notes.txt`에 기록한다. 설치기 코드는 변경하지 않는다.

사용자 지정 명령은 기본 명령의 사후 변경 검증을 생략한다. argv와 출력이 그대로
증적에 남으므로 **둘 다 비밀정보를 포함하면 안 된다**.
`--keep`이 없으면 실패·신호 종료에도 자신이 시작한 컨테이너와 부팅 알림 디렉터리를
삭제한다. `--keep`이면 출력된 명령으로 직접 정리한다. 개인키 디렉터리는 항상 삭제한다.
이미지 빌드의 apt 통신은 허용하며 Docker와 설치기의 자체 조회 외 통신은 추가하지 않는다.
