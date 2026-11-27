# 도리 설치는 처음부터 herdr, aoe 도리는 안내서 하나로 이전

## 무엇을

도리 설치기(`python3 -m automation.install.dori`, 마법사·quickstart 「도리」)가 옵션 없이 herdr 도리를 깐다.
herdr 0.9.5 를 sha256 확인 뒤 계정의 `~/.local` 에 두고, herdr 서버와 `dori serve` 를 사용자 유닛으로 켜고,
리드를 herdr pane 에 띄워 `leadPane` 에 적는다. 브라우저용 herdr 웹(ttyd)은 도리 포크에서 없어져(포크 #392)
2026-11-27 고정 커밋 `f7c918c0041c` 부터 깔지 않고, 예전 설치기가 깐 웹 유닛은 다시 실행·업데이트 때 끄고 백업한다. aoe·tmux 로 쓰던 도리는 [전환 안내서](../guide/herdr-전환.md) 하나로 옮긴다.

## 왜

형이 도리를 herdr 로 옮기면서(2026-11-03) 앞으로 herdr 판만 고쳐 나가기로 했다. 그런데 사이토플라즘 설치기는 여전히
aoe 를 기본으로 깔고 herdr 는 손으로 깔아야 했고, 형의 사이토플라즘을 받아 aoe 로 쓰는 사람은 옮기는 길을 알 수 없었다
(형 2026-11-03 15:05 KST: "AOE라 틴박스 쓰고 있는 사람들은 어떻게 전환할 수가 있어? … 앞으로 사이토플라즘을 적용하는
사람들은 바로 허더모드로 설치할 수 있도록").

## 사용 시나리오

- **새로 까는 사람**: `python3 -m automation.install.dori --owner-call 형` → `DORI-HERDR-SERVER … active`,
  `DORI-SERVE-UNIT … active`, `DORI-LEAD-STARTED pane=w1:p1`. 터미널에서 `herdr` 를 열면 리드 탭이 보인다.
  `dori.env` 를 채우고 그 탭에 "Dori mode".
- **herdr 웹을 쓰던 사람(업데이트)**: `DORI-HERDR-WEB-RETIRED herdr-web@8181.service …` — 웹은 꺼지고 유닛 파일은
  `~/.config/systemd/user/<이름>.bak-herdr-web-retired` 로 남는다. 리드는 터미널에서 `herdr` 로 본다.
- **aoe 로 쓰던 사람**: 안내서대로 백업 → 포크를 새 고정 커밋으로 → 설치기 다시 실행(config 는 aoe 그대로) → backend herdr
  → `dori handover`(새 리드를 herdr 탭에 열고 감시를 옮긴 뒤 옛 리드를 24시간 얼림) → `DORI_LEAD_*` 지우기 → aoe 웹 끄기.
  가장 쉬운 길은 리드에게 "herdr 로 옮겨, 안내서 순서대로"라고 쓰는 것이다.
- **거부·실패 경로**: 받은 파일의 sha256 이 다르면 아무것도 설치하지 않고 `DORI-HERDR-SHA-MISMATCH`(exit 5). 리드 omo 입력창이
  60초 안에 뜨지 않으면 `DORI-LEAD-NOT-READY` 와 화면 끝 줄을 보인다. `systemctl --user` 가 없으면(macOS 는 `systemctl` 자체가 없다) 바이너리만 깔고 유닛·리드는
  건너뛴 채 끝까지 간다(`DORI-HERDR-NO-SYSTEMD`). 그 줄과 「다음:」 줄이 `herdr` 탭에서 `omo`, 다른 탭에서 `dori serve` 를 켜라고 안내한다. aoe 를 계속 쓰려면 `--backend aoe`.

## 관련

- 코드: `automation/install/dori_herdr.py`, `automation/install/dori.py`, 고정판 `dori/herdr.toml`, 포크 고정 `dori/pin.toml`
- 시험: `tests/unit/test_install_dori_herdr.py`, `test_install_dori.py`, `test_install_default_dori.py`
- 포크: herdr 백엔드(#287·#288·#290), 교대 전 옛 감시 끝내기(#292), herdr 웹(#291·#293·#298, #392 로 없어짐)
- 승인·게이트: 없음(계정 안 설치. root·외부 발송 없음)
- 릴리스 노트 절: `docs/releases/herdr-default.md`
