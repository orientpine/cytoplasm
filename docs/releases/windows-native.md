# 윈도우에서 WSL 없이 PowerShell 한 줄로 설치하고, 같은 줄로 업데이트합니다

## 바뀐 것

- 윈도우(네이티브, WSL 없음): `powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/orientpine/cytoplasm/main/install.ps1 | iex"` 한 줄이 git·Python·Bun·PowerShell 7 을 찾거나 깔고, 공지 지문과 같은 업데이트 신뢰키로 최신 릴리스 태그의 서명을 확인한 뒤 도리 기본 설치를 합니다. 관리자 권한은 필요 없습니다.
- 상시 실행: systemd 대신 작업 스케줄러 두 항목(「Dori herdr server」, 「Dori serve」)이 로그온마다 뜹니다. 설치 직후에도 바로 켜집니다.
- 업데이트: 윈도우는 같은 줄을 다시 실행하고, Linux·macOS 는 체크아웃에서 `python3 -m automation.install.update` 입니다. 서명 없는 태그·더 낮은 판·손댄 체크아웃은 옮기지 않고, 포크는 설치기가 받은 체크아웃만 새 고정 커밋으로 옮깁니다.
- CI 에 `windows-install` 잡이 생겨 깨끗한 windows-latest 에서 설치·작업 등록·업데이트를 실제로 돌립니다.
- 도리 포크 23cf0fd: 윈도우 「Dori serve」 작업이 창을 숨겨 뜸(포크 #368), 그 사이 디스코드 글 밑줄 이스케이프(#358·#366), Anthropic 계정 로그인 공유(#363), 모델 기본값 지침(#361·#362), herdr 웹 휴대폰 스크롤·복사(#359·#360), 무돌 업무 PC VPN 유지(#357), 인수인계·레인 감시·문지기 카드 스레드 손질(#319~#353).

## 사용자에게 보이는 변화

- README 배지·요구 사항·FAQ 가 「Linux + systemd 전용」 대신 실제 지원 범위(Linux·macOS·윈도우 도리 기본 설치, 레거시 노드는 Linux)를 말합니다.
- 윈도우 사용자는 설치 뒤 `%USERPROFILE%\.dori\dori.env` 에 봇 토큰·채널을 넣고 `herdr` 에서 리드 탭을 엽니다.

## 깨지는 변경·운영자 할 일

- 없음. Linux 설치·노드 동작은 그대로입니다. 레거시 `--profile` 노드 인프라, herdr 웹, herdr-guard, 레인 체크포인트, 분리 인수인계, `dori heavy`, 송신 게이트, 음성 설정은 Linux 전용으로 남습니다.
