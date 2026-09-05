"""Codex OAuth 티어가 유일한 모델 경로일 때의 fail-closed 계약.

2026-09-03 다이제스트 49회차 사고: 은퇴한 2차 티어 뒤의 공급자가 모든 호출에
HTTP 429("Insufficient balance")를 돌려주어 비민감 15/19건이 ``(요약 실패)``
+ ``⚠️ 분류 실패``로 끝났다. 당시 수리는 비민감 메일을 그 시절의 다른 티어로 **강등**
하는 것이었다. 2026-09-04 공급자 이관으로 은퇴한 2차 티어 자체가 사라졌고,
강등할 곳이 없으므로 계약이 뒤집힌다 — 내려갈 티어가 없으면 내려가지 않는다.

여기서 고정하는 계약은 다섯 가지다.

1. 공유 클라이언트(automation.codex_llm)의 가용성 실패는
   ``LlmUnavailableError``로, 그 밖의 요청 실패는 ``LlmCallError``로 올라온다.
2. 공유 클라이언트를 임포트할 수 없으면 로컬 HTTP 호출로 내려가지 않고 거부한다.
3. 티어 자체가 불가하면 다이제스트는 **fail closed** — 구조화 마커 한 줄로
   실패를 알리고, DM도 저장도 하지 않는다(강등된 산출물 없음). 재시도로
   장애를 두들기지도 않는다(사고 당시 래치의 원래 의도).
4. 개별 요청 실패(파싱 불가·rc≠0 1회성)는 기존 항목 단위 fail-open을 유지한다.
5. 감사 로그에는 codex 경로만 남고 ``fallback_from`` 표식은 존재하지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_confirm  # noqa: E402
import triage_digest  # noqa: E402
import triage_gate  # noqa: E402
import triage_llm  # noqa: E402
import triage_mode  # noqa: E402
import triage_sensitivity  # noqa: E402

# codex 한 방 응답: 분류 JSON과 요약 JSON을 동시에 만족하는 합성 페이로드.
_CODEX_RAW = json.dumps(
    {
        "category": "normal",
        "reply_needed": False,
        "schedule_needed": False,
        "budget": False,
        "schedule_text": "",
        "reason": "codex ok",
        "summary": "코덱스 요약",
    },
    ensure_ascii=False,
)
# 측정된 fail-closed 신호(2026-09-04): 자격 증명이 없는 홈에서의 rc 1 + stderr.
_NO_CREDENTIALS = (
    "hermes -z: agent failed: No Codex credentials stored. "
    "Run `hermes auth` to authenticate."
)


def _write_stub(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _failing_hermes(tmp_path: Path, stderr: str, *, name: str = "hermes-stub") -> Path:
    """rc 1 + stderr 만 내는 hermes 대역 — Codex 티어 불가의 재현."""
    return _write_stub(
        tmp_path / name,
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"print({stderr!r}, file=sys.stderr)\n"
        "sys.exit(1)\n",
    )


def _gate_stub(*, sensitive: bool):
    def evaluate(text, rules):  # noqa: ARG001 — signature parity with triage_sensitivity
        return triage_sensitivity.GateResult(
            sensitive=sensitive,
            tags=("patent-sensitive",) if sensitive else (),
            matched=(),
        )

    return evaluate


def _detail(uid: str) -> dict:
    return {
        "uid": uid,
        "subject": f"Synthetic subject {uid}",
        "sender": "발신자 <p@inst.example>",
        "body": "Synthetic body",
        "date": "2026-07-18T09:01:00Z",
    }


def _records(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _prepare_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    uids: tuple[str, ...],
    sensitive: bool = False,
) -> list[str]:
    """합성 메일 한 틱을 준비하고, 전달된 소유자 메시지를 모으는 리스트를 준다."""
    monkeypatch.setenv("MAILON_ID", "owner@inst.example")
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(tmp_path / "llm-calls.jsonl"))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_digest.triage_sensitivity, "load_rules", lambda _path: ())
    monkeypatch.setattr(triage_sensitivity, "evaluate", _gate_stub(sensitive=sensitive))
    mails = [
        {"uid": uid, "date": f"2026-07-18T09:0{index}:00Z"}
        for index, uid in enumerate(uids, start=1)
    ]
    monkeypatch.setattr(triage_digest.triage_transport, "_list_mails", lambda _limit, _sync: mails)
    monkeypatch.setattr(triage_digest.triage_transport, "_get_mail", lambda uid: _detail(uid))
    sent: list[str] = []
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: sent.append(body) or "dm-1")
    return sent


def _run_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    uids: tuple[str, ...],
    sensitive: bool = False,
) -> str:
    """성공하는 한 틱을 돌리고 소유자 메시지 본문을 돌려준다."""
    sent = _prepare_digest(monkeypatch, tmp_path, uids=uids, sensitive=sensitive)
    assert triage_digest.run_digest(limit=10, sync=False, dry_run=False) == 0
    assert len(sent) == 1
    return sent[0]


# --- ⓐ 공유 클라이언트 오류의 타입 구분 -------------------------------------------


def test_codex_unavailable_credentials_map_to_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: OAuth 자격 증명이 없는 노드 — 측정된 fail-closed 신호 그대로.
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(_failing_hermes(tmp_path, _NO_CREDENTIALS)))

    # When/Then: 호출자는 "티어가 죽었다"를 "이 호출이 나빴다"와 구분할 수 있다.
    with pytest.raises(triage_llm.LlmUnavailableError):
        triage_llm.call_codex("프롬프트", sensitive=False, timeout=10.0)


def test_codex_empty_answer_stays_a_plain_call_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # rc 0 인데 빈 응답은 장애가 아니라 이 호출의 문제다 — 티어 불가로 승격하면
    # 멀쩡한 티어를 두고 다이제스트 전체를 죽인다.
    monkeypatch.setenv(
        "AUTOPHAGY_HERMES_BIN",
        str(_write_stub(tmp_path / "empty-stub", "#!/usr/bin/env python3\nprint('')\n")),
    )

    with pytest.raises(triage_llm.LlmCallError) as failure:
        triage_llm.call_codex("프롬프트", sensitive=False, timeout=10.0)
    assert not isinstance(failure.value, triage_llm.LlmUnavailableError)


def test_codex_failure_message_stays_masked_and_clipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: 상류 메시지가 주소와 긴 식별자를 달고 온다.
    noisy = "quota for admin@inst.example id 123456789012 " + "x" * 400
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(_failing_hermes(tmp_path, noisy)))

    # When: 실패가 표면화된다.
    with pytest.raises(triage_llm.LlmUnavailableError) as failure:
        triage_llm.call_codex("프롬프트", sensitive=False, timeout=10.0)

    # Then: 마스킹과 200자 클립은 공급자가 바뀌어도 그대로다.
    text = str(failure.value)
    assert "admin@inst.example" not in text
    assert "123456789012" not in text
    assert len(text) <= len("codex 호출 실패: ") + 200


def test_missing_shared_client_refuses_instead_of_calling_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: 공유 Codex 클라이언트를 임포트할 수 없는 배포본.
    monkeypatch.setitem(sys.modules, "automation.codex_llm", None)

    # When/Then: 로컬 HTTP 폴백이 아니라 거부다(skills/AGENTS.md fail-closed 규칙).
    with pytest.raises(triage_llm.LlmUnavailableError):
        triage_llm.call_codex("프롬프트", sensitive=False, timeout=10.0)


def test_no_second_tier_survives_the_migration() -> None:
    # 강등 대상이 존재하지 않는다는 것 자체가 계약이다.
    assert not hasattr(triage_llm, "call_glm")
    assert not hasattr(triage_llm, "GLM_MODEL")
    assert triage_llm.CODEX_PROVIDER == "openai-codex"


# --- ⓑ 티어 불가 = 다이제스트 fail closed -----------------------------------------


@pytest.mark.parametrize("sensitive", [False, True])
def test_digest_fails_closed_when_codex_tier_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sensitive: bool
) -> None:
    # Given: 유일한 티어가 사고 당시 은퇴한 2차 티어처럼 전량 실패한다.
    calls: list[str] = []

    def call_codex(prompt: str, **kwargs: object) -> str:  # noqa: ARG001
        calls.append(prompt)
        raise triage_llm.LlmUnavailableError("codex 호출 실패: no credentials")

    monkeypatch.setattr(triage_llm, "call_codex", call_codex)
    sent = _prepare_digest(monkeypatch, tmp_path, uids=("uid-down",), sensitive=sensitive)

    # When/Then: 강등된 다이제스트를 내보내는 대신 구조화 마커 한 줄로 죽는다.
    with pytest.raises(triage_gate.GateError) as error_info:
        triage_digest.run_digest(limit=10, sync=False, dry_run=False)
    marker = str(error_info.value)
    assert marker.splitlines() == [marker]  # 정확히 한 줄
    assert "DIGEST-FAIL stage=build retry_safe=false code=codex_unavailable" in marker
    assert error_info.value.exit_code == 4

    # And: 강등된 산출물이 없다 — DM 0건, 저장 0건.
    assert sent == []
    with sqlite3.connect(tmp_path / "triage.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'digest_runs'"
        ).fetchone() == (0,)

    # And: 장애를 재시도로 두들기지 않는다(사고 당시 래치의 의도).
    assert len(calls) == 1

    # And: 원인은 마스킹된 포렌식 한 줄로 남는다 — cron 은 stderr 를 버린다.
    records = _records(tmp_path / "llm-calls.jsonl")
    assert [record["purpose"] for record in records] == ["classify_failed"]
    assert records[0]["error"].startswith("LlmUnavailableError")
    assert records[0]["sensitive"] is sensitive


def test_failclosed_marker_hides_addresses_and_long_digits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: 티어 실패 메시지가 주소와 긴 식별자를 달고 있다.
    monkeypatch.setattr(
        triage_llm,
        "call_codex",
        lambda prompt, **kwargs: (_ for _ in ()).throw(  # noqa: ARG005
            triage_llm.LlmUnavailableError(
                "codex 호출 실패: quota for admin@inst.example key 987654321012"
            )
        ),
    )
    _prepare_digest(monkeypatch, tmp_path, uids=("uid-mask",))

    # When: 마커가 만들어진다.
    with pytest.raises(triage_gate.GateError) as error_info:
        triage_digest.run_digest(limit=10, sync=False, dry_run=False)

    # Then: 주소도, 긴 번호도, 메일 제목도 나가지 않는다.
    marker = str(error_info.value)
    assert "admin@inst.example" not in marker
    assert "987654321012" not in marker
    assert "Synthetic subject" not in marker


def test_unavailable_run_does_not_poison_the_next_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: 티어 불가로 죽은 첫 틱.
    monkeypatch.setattr(
        triage_llm,
        "call_codex",
        lambda prompt, **kwargs: (_ for _ in ()).throw(  # noqa: ARG005
            triage_llm.LlmUnavailableError("codex 호출 실패: no credentials")
        ),
    )
    _prepare_digest(monkeypatch, tmp_path, uids=("uid-first",))
    with pytest.raises(triage_gate.GateError):
        triage_digest.run_digest(limit=10, sync=False, dry_run=False)

    # When: 티어가 복구되고 다음 틱이 돈다(모듈 전역 상태가 없어야 한다).
    monkeypatch.setattr(triage_llm, "call_codex", lambda prompt, **kwargs: _CODEX_RAW)  # noqa: ARG005
    body = _run_digest(monkeypatch, tmp_path / "second", uids=("uid-second",))

    # Then: 앞 틱의 장애가 남아 있지 않다.
    assert "코덱스 요약" in body


# --- ⓒ 정상 런과 개별 요청 실패 ----------------------------------------------------


def test_healthy_run_records_only_the_codex_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: 유일한 티어가 정상 응답한다.
    monkeypatch.setattr(triage_llm, "call_codex", lambda prompt, **kwargs: _CODEX_RAW)  # noqa: ARG005

    # When: 다이제스트가 돈다.
    body = _run_digest(monkeypatch, tmp_path, uids=("uid-healthy",))

    # Then: 강등 경고도, 강등 표식도, 다른 공급자도 없다.
    assert "코덱스 요약" in body
    assert "사용 불가" not in body
    records = _records(tmp_path / "llm-calls.jsonl")
    assert records and all(record["provider"] == triage_llm.CODEX_PROVIDER for record in records)
    assert all("fallback_from" not in record for record in records)


def test_single_request_failure_keeps_the_item_level_fallbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: 티어는 살아 있지만 이 메일의 두 호출이 모두 실패한다(재시도 포함).
    monkeypatch.setattr(
        triage_llm,
        "call_codex",
        lambda prompt, **kwargs: (_ for _ in ()).throw(  # noqa: ARG005
            triage_llm.LlmCallError("codex 호출 실패: rc=1")
        ),
    )

    # When: 다이제스트가 돈다.
    body = _run_digest(monkeypatch, tmp_path, uids=("uid-one-bad",))

    # Then: 항목은 기존 fail-open 표식과 함께 살아남고 …
    assert "(요약 실패)" in body
    assert "⚠️ 분류 실패" in body
    # … 포렌식 실패 줄도 그대로다.
    purposes = {record["purpose"] for record in _records(tmp_path / "llm-calls.jsonl")}
    assert {"classify_failed", "digest_summary_failed"} <= purposes
