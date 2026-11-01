# 드라이브 PARA 구조

## 무엇을

노드가 드라이브에 자동으로 저장하는 회의록·전사본·녹음 원본·메일 첨부·공유 파일·주간동향·문서·제안서·용어집이 형 내 드라이브 맨 위의 PARA 다섯 칸(`0_Inbox`·`1_Project`·`2_Area`·`3_Resource`·`4_Archive`)에 들어간다. 과제 자료는 `1_Project/<시작연도>_<과제>/` 한 폴더에 모이고, 도리별 폴더는 없다. 스위치 `DRIVE_LAYOUT=para` 하나로 켠다.

## 왜

형(2026-11-01): "누가 일하던 간에 동일한 폴더에 작성해놔야 직관적인데, 지금은 파일 찾기도 힘들어." 예전에는 종류별 폴더(`회의록/<과제>`, `전사본/<과제>`, `문서/2026` …)와 도리별 폴더가 섞여서, 한 과제 자료가 네다섯 곳에 흩어져 있었다. 형이 카드로 자리를 골랐다(Q427 내 드라이브 맨 위, Q428 형 옛 폴더도 함께, Q429 전사본은 `4_Archive/전사본/연도`에 과제명을 이름 앞에, 회의록은 과제 폴더).

## 사용 시나리오

**회의 하나가 끝났을 때 (스위치를 켠 뒤)**

```
형: 녹음 파일을 0_Inbox/회의녹음 에 놓는다
→ 전사본: 4_Archive/전사본/2026/2026-11-03_데모과제_정기회의.md
→ 회의록: 1_Project/2025_데모과제/회의록/2026-11-03_회의록-정기회의.md
→ 형은 폰에서 1_Project → 2025_데모과제 두 번 눌러 회의록·문서·용어집을 본다
```

과제 폴더가 이미 있으면 그 시작연도를 그대로 쓴다. 2025년에 시작한 과제의 2026년 회의록도 `2025_데모과제`에 들어가고, 폴더가 없을 때만 그해 연도로 새로 만든다. 과제를 모르는 문서는 `0_Inbox/분류대기/<연도>/`에 두고 유돌이 옮긴다.

**옮기는 동안 (한 릴리스)**

스위치를 켜기 전에는 예전 자리에 그대로 쓴다. 그래서 코드를 먼저 배포하고, 유돌이 폴더를 옮긴 뒤 스위치를 켠다. 켠 뒤에도 옛 자리에 남은 용어집·회의록 목록·과제 양식·코드표·참고자료(`KIMM`)는 읽기만 한다. 옛 이름 폴더를 다시 만들지 않는다.

**거부되는 경우**

- 옛 공유 폴더가 묶인 미결 공유 메일 카드는 ✅ 전에 닫히고, 같은 스레드에서 새 카드를 요청하라고 알린다. 이미 ✅된 카드는 발송 단계에서 멈추고 메일은 나가지 않는다.
- 옛 단일 루트로 옮기는 1회성 이관 도구는 PARA 노드에서 거절한다(exit 2).

## 관련

- 규약 정본: [drive-publish.md 「PARA 구조」](../guide/drive-publish.md#para-구조-drive_layoutpara)
- 코드: `automation/drive_taxonomy.py`(자리), `automation/drive_outputs.py`(`resolve_project`, 읽기 전용 폴백), `skills/meeting/scripts/meeting_project.py`, `automation/project_board/minutes.py`, `automation/term_glossary.py`, `automation/drive_reference.py`, `skills/mail/scripts/mail_attachment_archive.py`, `skills/proposal/scripts/proposal_publish.py`
- 시험: `tests/unit/test_drive_para_layout.py`
- 승인: 스위치를 담은 코드는 사이토플라즘 릴리스 카드 ✅로 들어간다. 드라이브에서 폴더를 옮기는 일은 유돌 문지기 한 길이다. 형 계정 안 쓰기라 카드는 없다.
