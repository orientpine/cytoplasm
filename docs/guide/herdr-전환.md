# aoe·tmux 도리를 herdr로 옮기기

사이토플라즘 설치기로 도리를 깔아 **aoe + tmux** 로 쓰고 있다면 이 안내만 보고 herdr 로 옮길 수 있다.
옮길지는 쓰는 사람이 정한다. 그대로 쓰려면 아무것도 하지 않아도 되고, 다시 깔 때도
`python3 -m automation.install.dori --backend aoe` 로 aoe 를 고를 수 있다. 다만 앞으로 도리는 herdr 판만
고쳐 나간다(2026-11-03부터).

**바뀌는 것** — 레인이 aoe 세션 대신 herdr 탭에서 돈다. 도리 화면은 터미널에서 `herdr` 로 연다. 예전 aoe 웹을 잇던
herdr 웹은 도리 포크에서 없어졌다(포크 #392, 2026-11-08). 브라우저로 보던 사람은 8 에서 aoe 웹을 끄기 전에 이 점을 정한다.
**리드가 멈추는 시간** — 교대(handover)가 새 리드를 확인하는 몇 분. 옛 리드는 지우지 않고 24시간 얼려 두므로
그 안에는 한 줄로 되돌릴 수 있다.

새로 까는 사람은 이 안내가 필요 없다. 설치기가 처음부터 herdr 도리를 깐다([설치 안내](install.md) 「도리 프로필」).
포크의 `install.sh` 로 깔아 포크 main 을 따라가는 도리는 포크 README 「Running on herdr instead of aoe/tmux」를 본다.

## 쉬운 길: 리드에게 맡기기

리드(도리 본인) 채널에 이렇게 쓰면 된다.

> herdr 로 옮겨. 사이토플라즘 docs/guide/herdr-전환.md 순서대로 하고, 이 글 링크를 교대 승인으로 써.

리드가 아래 1~9 를 차례로 하고, 5 단계 교대 승인에는 방금 쓴 글의 링크를 쓴다. 직접 하려면 아래를 그대로 따라 한다.
명령은 모두 도리 계정의 셸에서 한다. `<...>` 는 자기 값으로 바꾼다.

## 0. 지금 값 적어 두기

```bash
dori lead                                   # 리드: <aoe id> <tmux 이름>
```

## 1. 백업

```bash
S=$(date -u +%Y%m%dT%H%M%SZ)
cp -p ~/.dori/config.json ~/.dori/config.json.bak-herdr-$S
cp -p ~/.dori/dori.env ~/.dori/dori.env.bak-herdr-$S
grep -l DORI_LEAD_ ~/.config/systemd/user/*.d/*.conf 2>/dev/null | while read f; do cp -p "$f" "$f.bak-herdr-$S"; done
echo $S                                     # 되돌릴 때 쓴다
```

## 2. 사이토플라즘을 새로 받고 포크를 새 고정 커밋으로

```bash
cd <사이토플라즘 폴더> && git pull --ff-only
PIN=$(python3 -c 'import tomllib; print(tomllib.load(open("dori/pin.toml", "rb"))["commit"])')
git -C ~/.dori/src fetch --depth 1 origin "$PIN" && git -C ~/.dori/src checkout --detach "$PIN"
python3 -m automation.dori_pin verify --checkout ~/.dori/src     # DORI-PIN-OK <폴더> <커밋> 이 나와야 한다
```

`checkout` 이 로컬 변경 때문에 멈추면 `git -C ~/.dori/src status` 로 무엇인지 보고 정리한 뒤 다시 한다.

## 3. 설치기를 다시 돌려 herdr 준비

처음 깔 때 쓴 `--role`·`--owner-call` 을 그대로 붙인다.

```bash
python3 -m automation.install.dori --owner-call <부르는 말> [--role ...]
```

기대하는 줄:

- `DORI-HERDR-BIN … installed` — herdr 0.9.5 가 sha256 확인을 거쳐 깔림
- `DORI-HERDR-SERVER dori-herdr.service active` — 빈 herdr 서버가 뜸(아직 아무 일도 하지 않는다)
  탭 상태 `<포트>+1200`)가 나오면 같은 PC 의 다른 도리 계정이 그 번호를 쓴다는 뜻이다 — 그 계정에 물어 포트를 나눠 쓴다
- `DORI-LEAD-KEEP … backend=aoe` — config 는 그대로. 리드는 아직 aoe 에 있다

## 4. backend 를 herdr 로

```bash
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home() / ".dori" / "config.json"
d = json.loads(p.read_text()); d["backend"] = "herdr"
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
EOF
```

`leadPane` 은 아직 옛 aoe 리드다. 5 의 교대가 바꾼다.

## 5. 리드 교대

승인은 내가 도리에게 "herdr 로 옮겨"라고 쓴 디스코드 글 링크다(도리 inbox 에 있어야 한다).

```bash
dori handover --dry-run --approved-by "<그 글 링크>"   # HANDOVER_APPROVAL_OK, "would open herdr session"
dori handover --approved-by "<그 글 링크>"             # HANDOVER_MONITORS_OK … HANDOVER_DONE
```

교대는 새 리드를 herdr 탭에 열고, 옛 리드가 걸어 둔 감시를 새 리드에 다시 걸게 한 뒤 확인하고, 옛 리드의 감시를 끝낸 다음
옛 리드를 얼린다. 확인이 하나라도 안 되면 옛 리드가 그대로 리드로 남는다(`HANDOVER_VERIFY_FAIL`).

## 6. `DORI_LEAD_*` 지우기

교대는 `dori.env` 의 이 값을 새 pane 으로 바꿔 두지만, herdr 리드는 config 의 `leadPane` 만으로 찾는다. 유닛 drop-in 에도 있으면
`dori lead` 와 감시·자가 재시작이 config 보다 먼저 읽어 옛 aoe 리드를 다시 깨우므로 모두 지운다.

```bash
sed -i -E '/^(export )?DORI_LEAD_(AOE_ID|TMUX|PANE)=/d' ~/.dori/dori.env
grep -l DORI_LEAD_ ~/.config/systemd/user/*.d/*.conf 2>/dev/null | xargs -r sed -i -E '/DORI_LEAD_(AOE_ID|TMUX|PANE)=/d'
systemctl --user daemon-reload
dori lead                                   # <omo 세션 id> w1:pN — herdr pane 이면 맞다
```

## 7. 감시 확인

5 의 `HANDOVER_MONITORS_OK` 가 감시가 같은 명령으로 다시 걸렸다는 뜻이다. 따로 건 감시(카톡·슬랙 등)가 있으면
새 리드에서 다시 돌고 있는지 본다: `ps -u $USER -o pid,args | grep 'tail -n 0 -F'`.

## 8. aoe 웹 끄고 herdr 화면 정리

```bash
systemctl --user disable --now aoe-serve        # 지우지 않고 끈다(되돌리기용). 쿠키 프록시 유닛이 따로 있으면 그것도
herdr tab list                                  # 이름이 숫자뿐인 첫 탭(교대가 만든 빈 셸)과 리드 탭을 본다
herdr tab close <빈 첫 탭 id>                 # 예: w1:t1 — 에이전트가 없는 탭인지 확인하고
herdr tab focus <리드 탭 id>                   # herdr 를 열면 리드가 앞에 보인다
```

터미널에서 `herdr` 를 열면 리드 탭이 보인다.

- 다른 탭(레인)은 왼쪽 목록에서 고른다. 키보드면 `ctrl+b` 다음 `w`.
- 글을 넣으면 그 탭의 에이전트에 들어간다. herdr 창을 닫아도 도리는 계속 일한다.
- 메뉴의 `+ new tab` 은 누르지 않는다. 빈 창이 하나 더 생긴다.
- PC 에 herdr 를 깔아 `herdr --remote <계정>@<호스트>` 로 볼 때, 닫으며 **"서버를 멈출까요?" 에는 꼭 No**. Yes 면 도리 세션이 다 꺼진다.

## 9. 재시작 확인

```bash
systemctl --user restart dori-lanes dori-inbound dori-session-watch   # 깔려 있는 것만
systemctl --user is-active dori-lanes dori-inbound dori-session-watch
dori lead                                   # 6 과 같은 pane
```

## 남은 aoe 레인과 정리

열려 있던 aoe 레인은 그대로 돌고, 다음 `dori handover --lane <key>` 때 herdr 탭으로 옮겨진다. aoe 레인이 하나도 남지 않고
옛 리드 얼림(24시간)이 지나면 tmux 서버 유닛(`<계정>-tmux` 같은)을 `systemctl --user disable --now` 로 끈다. 유닛 파일과
`aoe` 는 지우지 않는다 — 되돌리는 길이다.

## 되돌리기 (옛 리드 얼림 24시간 안, 한 줄)

`<S>` 는 1 에서 적은 값, `<옛 리드 tmux>` 는 0 의 `dori lead` 둘째 칸(예: `aoe_Dori_3bb7ec1f`)이다.

```bash
cp ~/.dori/config.json.bak-herdr-<S> ~/.dori/config.json && cp ~/.dori/dori.env.bak-herdr-<S> ~/.dori/dori.env && dori handover thaw <옛 리드 tmux> && systemctl --user restart dori-lanes dori-inbound dori-session-watch && systemctl --user disable --now dori-herdr && systemctl --user enable --now aoe-serve
```

1 에서 백업한 유닛 drop-in 이 있으면 같은 방식으로 되돌리고 `systemctl --user daemon-reload` 한다. 옛 감시는 5 에서
끝냈으므로 되돌린 뒤 리드가 다시 건다. 도리 코드까지 aoe 마지막 판으로 돌리려면 포크 태그 `aoe-last-2026-11-03` 이다.
