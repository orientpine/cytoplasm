# 클러스터 인벤토리 템플릿

> **목적**: 설치자가 자신의 노드 역할, 하드웨어 여유, 포트 예약을 배포 전에 기록한다.
> 실제 호스트명·장치명·용량·리스닝 주소·측정 시각은 공개 repo에 커밋하지 않는다.
> 원시 측정 결과는 접근 제한된 운영 경로에 보관한다.

## 1. 노드 역할

| 역할 | 노드 별칭 | 비고 |
|---|---|---|
| 프로덕션 서비스 | `<production-node>` | Hermes, 모델 게이트웨이, 운영 대시보드 |
| 개인 RAG 서비스 | `<rag-node>` | 임베딩, 벡터 DB, MCP |

## 2. 배포 전 용량 점검

| 항목 | 프로덕션 노드 | RAG 노드 | 요구 조건 |
|---|---|---|---|
| 아키텍처 | `<architecture>` | `<architecture>` | 배포 이미지와 일치 |
| 사용 가능 메모리 | `<available-memory>` | `<available-memory>` | 서비스별 최소치 이상 |
| 사용 가능 디스크 | `<available-disk>` | `<available-disk>` | 데이터·로그 증가분 포함 |
| 스왑 정책 | `<swap-policy>` | `<swap-policy>` | 운영 정책에 맞게 확인 |

## 3. 포트 예약

서비스 기동 직전에 각 노드에서 `ss -tlnp`로 예약 포트가 비어 있는지 다시 확인한다.

### 프로덕션 노드

| 포트 | 용도 | 바인딩 범위 | 확인 결과 |
|---|---|---|---|
| `<model-gateway-port>` | 모델 게이트웨이 | `<loopback-or-tailnet>` | `<free-or-conflict>` |
| `<kanban-port>` | Kanban 대시보드 | `<loopback-or-tailnet>` | `<free-or-conflict>` |
| `<report-hub-port>` | 리포트 허브 | `<loopback-or-tailnet>` | `<free-or-conflict>` |

### RAG 노드

| 포트 | 용도 | 바인딩 범위 | 확인 결과 |
|---|---|---|---|
| `<embedding-port>` | 임베딩 서비스 | `<loopback-or-tailnet>` | `<free-or-conflict>` |
| `<vector-rest-port>` | 벡터 DB REST | `<loopback-or-tailnet>` | `<free-or-conflict>` |
| `<vector-grpc-port>` | 벡터 DB gRPC | `<loopback-or-tailnet>` | `<free-or-conflict>` |
| `<rag-mcp-port>` | RAG MCP | `<loopback-or-tailnet>` | `<free-or-conflict>` |

## 4. 연결성 점검

| 대상 | 목적 | 결과 |
|---|---|---|
| Discord API | 봇 이벤트·승인 | `<reachable-or-blocked>` |
| 모델 provider | 추론 호출 | `<reachable-or-blocked>` |
| 패키지·업데이트 출처 | 설치·업데이트 | `<reachable-or-blocked>` |

TLS 검증, 프록시 요구사항, 방화벽 예외는 실제 주소나 자격증명을 포함하지 않은 요약만 기록한다.

## 5. 완료 조건

- 각 역할에 설치자가 관리하는 노드를 지정했다.
- 아키텍처·메모리·디스크 요구 조건을 충족했다.
- 예약 포트가 기동 직전 비어 있음을 확인했다.
- 필요한 외부 연결의 TCP·TLS 검증을 마쳤다.
- 원시 인벤토리와 민감한 네트워크 정보는 repo 밖에 보관했다.
