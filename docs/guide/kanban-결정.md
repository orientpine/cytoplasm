# Kanban 보드 결정 — 개인용 Hermes 우선, Vikunja 폴백

> **결정일**: 2026-07-14  
> **범위**: cha 1인 보드. 계정 간 공유 요건은 v2.0에서 삭제되었다.  
> **배포 상태**: 설치·원격 접속·노드 변경을 하지 않은 desk research 결정이다. 실제
> Hermes 검증은 W1-2/W1-4에서 수행한다.

---

## 1. 결정 요약

Hermes의 Kanban은 별도 제품/컨테이너가 아니라 **Hermes Web Dashboard에 번들된
dashboard plugin**이다. 즉 `hermes dashboard` 프로세스의 browser tab으로 제공되는
실제 Web UI이며, 카드 drag/drop·comment thread를 포함한다. 기본은
`127.0.0.1:9119`; `--host`와 `--port` CLI 인자가 있고 비-loopback bind에는
인증 provider가 필수다.[^dashboard-cli][^nonloopback-auth]

**v1 우선안은 Hermes 내장 Kanban**이다. 단, 스마트폰에서의 완전한 UX와 실제
이벤트 전달의 30초 SLO는 문서만으로 끝까지 보장할 수 없으므로 W1-4의
Playwright DOM assertion으로 gate한다. 그 gate가 실패하면 **Vikunja**를 유일한
셀프호스팅 폴백으로 채택한다. Vikunja `2.3.0` 이미지의 `linux/arm64` manifest는
로컬 Docker CLI로 실제 확인했다(§5).

## 2. Hermes Kanban UI 실체 (what it actually is)

| 항목 | 확인 결과 | 근거 |
|---|---|---|
| UI 형태 | Hermes Web Dashboard 안의 bundled Kanban plugin; 별도 Kanban service가 아님 | 기능 문서는 `plugins/kanban/` dashboard plugin이라고 명시하고, manifest는 multi-agent board/card drag-drop을 명시한다.[^kanban-doc][^kanban-manifest] |
| 기본 URL/port | `http://127.0.0.1:9119`; CLI default port `9119` | Web Dashboard 문서 및 CLI source.[^web-dashboard-doc][^dashboard-cli] |
| bind address | CLI `--host` default는 `127.0.0.1`; 문서는 `--host 0.0.0.0` 예시를 제공한다. Tailscale IP 같은 특정 listen address도 `--host` 값으로 W1에서 실측한다. config file/env로 설정 가능한지는 **UNKNOWN**. | 문서/CLI source.[^web-dashboard-doc][^dashboard-cli] |
| 인증 | loopback 외 bind는 OAuth 또는 bundled password provider 등 auth provider가 **필수**이다. `--insecure`는 더 이상 인증을 우회하지 않는다. Kanban plugin API도 session bearer/cookie 및 WebSocket token을 요구한다. | 현재 server/plugin source.[^nonloopback-auth][^plugin-auth] |
| 모바일 | touch device pointer fallback, horizontal column scroll, mobile safe-area CSS가 존재한다. 공식 문서의 명시적 보장은 tablet usability까지이며 phone 전체 UX 보장은 없다. | 문서 및 compiled UI source.[^kanban-doc][^touch-ui] |
| 상태/열 | source의 유효 상태는 `triage`, `todo`, `scheduled`, `ready`, `running`, `blocked`, `review`, `done`, `archived`; dashboard 표시는 archived를 제외한 앞 8열이다. 문서의 더 짧은 상태 목록은 source와 불일치하므로 source를 우선한다. | DB 및 dashboard source.[^statuses][^columns] |

## 3. 개인용 요건 판정

| 개인용 요건 | 판정 | 근거 및 W1 gate |
|---|---|---|
| ① cha가 browser(모바일 포함)로 접근 | **UNKNOWN** | desktop browser UI와 touch/pointer fallback·mobile safe-area CSS는 확인되었다. 그러나 공식 문서가 phone을 명시적으로 지원한다고 보장하지 않고 tablet만 “usable”이라 한다. W1-4에서 Tailscale의 실제 phone browser로 로그인·열 scroll·카드 조회를 assertion한다. |
| ② 내 에이전트 업무의 칼럼/상태 조회 | **PASS** | bundled board는 카드/열 UI이며, source가 8개 비-archived 상태 열을 정의한다.[^kanban-manifest][^columns] 에이전트 task DB의 상태 집합도 확인했다.[^statuses] W1-4에서는 테스트 에이전트 task가 올바른 열에 나타나는 것을 DOM으로 확인한다. |
| ③ 상태 전이가 30초 내 반영 | **PASS (connection healthy 조건)** | Kanban UI는 WebSocket event를 받고 event burst 뒤 board reload를 250 ms debounce한다.[^ws-reload] 이는 30초보다 작다. 단 dispatcher의 ready-task claim tick 기본값은 60초이므로 “다음 실행 시작까지 30초” 보장은 아니다; 이 요건은 **이미 발생한 상태 변경의 UI 반영**으로 한정한다. W1-4에서 API/agent 상태 변경 → 화면 반영의 p95 <30초를 실측한다. |

## 4. 접근 URL 및 네트워크 설계

### 권장 production path

```text
cha의 desktop/mobile browser
  └─ Tailscale tailnet (VPN only)
       └─ http://<production-node-tailnet-name>:9119/
          (or http://<tailscale-100.x.y.z>:9119/)
              └─ hermes dashboard --host <production-node-tailscale-ip> --port 9119
```

- **bind**: `--host <production-node-tailscale-ip>`를 우선한다. 단일 Tailscale
  interface에만 listen하므로 일반 LAN/Wi-Fi와 public interface에 port가 열리지
  않아 `0.0.0.0`보다 최소 노출이다.
- **auth**: non-loopback bind이므로 Hermes auth provider를 반드시 구성한다.
  URL을 아는 것만으로 접속되면 안 된다.[^nonloopback-auth]
- **routing**: router port-forward, cloudflared/public tunnel, public DNS를 만들지
  않는다. Tailscale ACL은 cha identity/device만 TCP 9119에 허용한다. Wi-Fi DHCP와
  LAN IP 변동은 기존 인프라 문서의 전제이므로 LAN IP URL을 운영 URL로 사용하지
  않는다.[^infra]
- **trade-off**: `0.0.0.0 + host firewall`은 특정 Tailscale IP bind가 Hermes
  runtime에서 실패할 때만 대안이다. 그 경우 firewall은 `tailscale0` inbound
  TCP/9119와 established traffic만 허용하고, 물리 LAN/WAN ingress는 drop해야 한다.
- **provisional port**: `9119`는 Hermes default일 뿐 현재 권고/확정 포트가 아니다.
  `configs/inventory.md`는 아직 존재하지 않는다(W0-3 미실행). W0-3가 만든
  `reserved_autophagy_ports`와 충돌 여부를 확인한 뒤 포트 및 ACL을 확정한다.

## 5. 폴백: Vikunja (단 하나만 선택)

Hermes W1-4 gate에서 §3의 UNKNOWN이 실패하거나 UI를 사용하기 어렵다고 판정될
때만 Vikunja를 배포한다. 지금은 폴백을 설치하지 않는다.

- **선택**: `vikunja/vikunja:2.3.0` (pin된 release tag; `latest` 미사용).
- **선정 이유**: 공식 설치 페이지가 `arm64`를 명시하고 공식 release workflow가
  `linux/arm64/v8` build를 명시한다.[^vikunja-install][^vikunja-release]
  개인 1인 보드의 standard Docker image/documentation이 명확하다.[^vikunja-docker]
- **실제 ARM64 manifest 검증**: 로컬 Docker `29.1.3`에서 아래 명령을 성공적으로
  실행했다. 결과 OCI index에 `architecture: "arm64", os: "linux"` descriptor가
  있다. 원문 전체는 QA 증적에 보관한다.[^manifest]

  ```bash
  docker manifest inspect "vikunja/vikunja:2.3.0"
  ```

- **비선정 대안**: Planka도 current build workflow에서 `linux/arm64`를 선언한다.
  그러나 설치 문서가 older Docker Hub image를 가리키는 반면 current image는 GHCR라
  운영 표면이 덜 일관적이다. 따라서 fallback은 Planka와 병행하지 않고 Vikunja
  하나만 유지한다.[^planka-build][^planka-doc]

## 6. W1-4 live verification checklist

1. `--host <tailscale-ip> --port <reserved port>`로 기동하고, auth 없는 요청이
   거부되는지 확인한다.
2. cha desktop과 phone의 Tailscale browser에서 login, 열 scroll, task card/status를
   Playwright/실기기로 확인한다.
3. agent task를 각 주요 status로 전이하고, UI observation timestamp의 p95가
   30초 미만인지 기록한다. 60초 dispatcher tick은 “claim latency” 별도 지표로
   기록한다.
4. 하나라도 실패하면 Hermes dashboard를 노출하지 않고 Vikunja deployment 설계로
   전환한다.

---

## Evidence / 출처

[^kanban-doc]: Hermes Kanban docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban
[^web-dashboard-doc]: Hermes Web Dashboard docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard
[^dashboard-cli]: Hermes source (`af250d84948179834820a62bfd870c0df6f264a1`), `dashboard.py` lines 26–31: https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/hermes_cli/subcommands/dashboard.py#L26-L31
[^nonloopback-auth]: Hermes source, `web_server.py` lines 17071–17117: https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/hermes_cli/web_server.py#L17071-L17117
[^kanban-manifest]: Hermes source, Kanban dashboard manifest: https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/plugins/kanban/dashboard/manifest.json#L2-L13
[^plugin-auth]: Hermes source, plugin authentication: https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/plugins/kanban/dashboard/plugin_api.py#L16-L33
[^touch-ui]: Hermes source, touch fallback and CSS: https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/plugins/kanban/dashboard/dist/index.js#L392-L460 ; https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/plugins/kanban/dashboard/dist/style.css#L65-L70
[^statuses]: Hermes source, task status validation: https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/hermes_cli/kanban_db.py#L102-L103
[^columns]: Hermes source, dashboard columns: https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/plugins/kanban/dashboard/plugin_api.py#L142-L152
[^ws-reload]: Hermes source, WebSocket reload debounce: https://github.com/NousResearch/hermes-agent/blob/af250d84948179834820a62bfd870c0df6f264a1/plugins/kanban/dashboard/dist/index.js#L609-L667
[^infra]: `docs/hardware-infra-openclaw.md` §1, §2, §5.
[^vikunja-install]: Vikunja install architectures: https://vikunja.io/install/
[^vikunja-release]: Vikunja release workflow: https://github.com/go-vikunja/vikunja/blob/main/.github/workflows/release.yml
[^vikunja-docker]: Vikunja full Docker example: https://vikunja.io/docs/full-docker-example/
[^manifest]: `docs/qa/W0-8/01-vikunja-arm64-manifest.txt`.
[^planka-build]: Planka build workflow: https://github.com/plankanban/planka/blob/master/.github/workflows/build-and-push-docker-image.yml
[^planka-doc]: Planka Docker docs: https://docs.planka.cloud/docs/installation/docker/production-version/
