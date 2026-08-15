# gitleaks pre-commit 훅 설치 (W0-1)

## 환경
- 로컬 워크스테이션: linux `x86_64` (`uname -m`으로 확인)
- gitleaks 버전: **8.30.1** (공식 release 바이너리)

## 설치 방법 (실제 사용한 절차)

패키지 매니저 없이 GitHub 공식 release 바이너리를 직접 다운로드해 설치했다.

```bash
# 1. 최신 버전 확인
curl -sL https://api.github.com/repos/gitleaks/gitleaks/releases/latest | grep tag_name
# -> v8.30.1

# 2. linux x64 tarball 다운로드 및 압축 해제
curl -sL -o /tmp/gitleaks.tar.gz \
  "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz"
tar -xzf /tmp/gitleaks.tar.gz -C /tmp

# 3. PATH에 있는 ~/.local/bin 에 설치 (sudo 불필요)
install -m 755 /tmp/gitleaks <your-home-directory>/.local/bin/gitleaks

# 4. 확인
gitleaks version   # -> 8.30.1
```

## pre-commit 훅 배선

추적 정본 `automation/hooks/gitleaks-pre-commit`을 커밋이 허용되는 clone의
`.git/hooks/pre-commit`에 mode 0755로 설치한다. 단일 노드 설치기는 ops deploy checkout에는
커밋 거부 정본 `automation/hooks/deploy-checkout-pre-commit`을, repair 작업 clone에는 이
gitleaks 정본을 설치한다.

- `gitleaks git --pre-commit --staged --redact --verbose` 를 실행 (v8.19+ 신규 CLI 문법)
- 비정상 종료 시 커밋을 차단 (exit 1)
- gitleaks 바이너리가 PATH에 없으면 fail-closed로 커밋 차단

검증: 가짜 AWS 키(`AKIAIOSFODNN7EXAMPLE`)를 스테이징 후 커밋 시도 → 훅이 커밋을 차단함을 확인
(증적: `docs/qa/W0-1/03-blocked-commit.txt`).

## 주의: `.git/hooks/`는 git에 추적되지 않음

`.git/hooks/pre-commit` 설치본 자체는 repo에 커밋되지 않는다. clone을 새로 받으면 추적 정본을
다시 설치해야 하며, `python3 -m automation.install` 재실행은 이를 멱등하게 복구한다.

## Spark aarch64 노드 (W0-4에서 처리 예정)

원격 노드(`<primary-node>` / `<rag-node>`)가 **aarch64**이면 x64 바이너리가 동작하지 않는다.
W0-4에서 아래 자산을 사용할 것:

```bash
# aarch64용 tarball (동일 릴리즈)
https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_arm64.tar.gz
```

설치 절차는 위와 동일 (tarball 경로만 `linux_arm64`로 교체). 본 태스크(W0-1)에서는
원격 노드에 아무것도 설치하지 않았다 — 로컬 x86_64 + GitHub만 대상.
