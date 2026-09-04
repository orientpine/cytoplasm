"""GLM(glm-main) 가용성 장애 시 다이제스트가 비-GLM 티어로 강등되는 계약.

2026-09-03 다이제스트 49회차 사고: glm-main 뒤의 공급자가 모든 호출에
HTTP 429("Insufficient balance")를 돌려주어 비민감 15/19건이
``(요약 실패)`` + ``⚠️ 분류 실패``로 끝났고, 소유자 DM에는 원인 한 줄도
없었다. 여기서 고정하는 계약은 네 가지다.

1. ``call_glm``은 가용성 실패(429/5xx, 연결·타임아웃)를
   ``LlmUnavailableError``로 구분해서 올린다 — 그 밖의 HTTP 오류는 그대로
   ``LlmCallError``.
2. 비민감 메일의 GLM 단계가 재시도까지 실패하면 같은 프롬프트를 codex
   티어에서 실행하고, 감사 로그에 ``fallback_from`` 표시를 남긴다.
3. 런 단위 래치: 한 번 사용 불가로 판정되면 남은 비민감 메일은 GLM 재시도
   없이 바로 codex로 간다.
4. 소유자 메시지에는 마스킹된 원인 한 줄이 정확히 한 번만 붙는다.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_confirm  # noqa: E402
import triage_digest  # noqa: E402
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
        "reason": "codex fallback",
        "summary": "코덱스 요약",
    },
    ensure_ascii=False,
)
_GLM_RAW = json.dumps(
    {
        "category": "normal",
        "reply_needed": False,
        "schedule_needed": False,
        "budget": False,
        "schedule_text": "",
        "reason": "glm ok",
        "summary": "GLM 요약",
    },
    ensure_ascii=False,
)
_UNAVAILABLE = "glm-main 호출 실패: HTTP Error 429: Insufficient balance or no resource package"


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


def _notice_lines(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith("⚠️ glm-main")]


def _records(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    uids: tuple[str, ...],
    sensitive: bool = False,
) -> str:
    """Run one full digest tick against synthetic mail and return the owner body."""
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

    assert triage_digest.run_digest(limit=10, sync=False, dry_run=False) == 0

    assert len(sent) == 1
    return sent[0]


# --- ⓐ triage_llm: 가용성 실패의 타입 구분 ---------------------------------------


def _http_error(code: int, message: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:4000/v1/chat/completions", code, message, {}, None  # type: ignore[arg-type]
    )


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.delenv("TRIAGE_GLM_BIN", raising=False)
    monkeypatch.setattr(triage_llm, "_litellm_key", lambda: "dummy-key")

    def urlopen(request, *, timeout: float):  # noqa: ARG001
        raise error

    monkeypatch.setattr(triage_llm.urllib.request, "urlopen", urlopen)


@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_call_glm_maps_429_and_5xx_to_unavailable(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    # Given: LiteLLM answers with the incident's rate/limit or server status.
    _patch_urlopen(monkeypatch, _http_error(code, "Insufficient balance or no resource package"))

    # When/Then: the caller can tell "GLM is down" from "this call was bad".
    with pytest.raises(triage_llm.LlmUnavailableError):
        triage_llm.call_glm("프롬프트", sensitive=False, timeout=1.0)


def test_call_glm_maps_connection_and_timeout_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_urlopen(monkeypatch, urllib.error.URLError(TimeoutError("timed out")))

    with pytest.raises(triage_llm.LlmUnavailableError):
        triage_llm.call_glm("프롬프트", sensitive=False, timeout=1.0)


@pytest.mark.parametrize("code", [400, 403, 404, 422])
def test_call_glm_keeps_other_http_errors_as_plain_call_error(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    # A blocked/invalid request is NOT an outage — degrading on it would route
    # mail to codex for a bug that codex cannot fix either.
    _patch_urlopen(monkeypatch, _http_error(code, "blocked"))

    with pytest.raises(triage_llm.LlmCallError) as failure:
        triage_llm.call_glm("프롬프트", sensitive=False, timeout=1.0)
    assert not isinstance(failure.value, triage_llm.LlmUnavailableError)


def test_call_glm_unavailable_message_stays_masked_and_clipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the upstream message carries an address and a long id.
    noisy = "quota for admin@inst.example id 123456789012 " + "x" * 400
    _patch_urlopen(monkeypatch, _http_error(429, noisy))

    # When: the failure surfaces.
    with pytest.raises(triage_llm.LlmUnavailableError) as failure:
        triage_llm.call_glm("프롬프트", sensitive=False, timeout=1.0)

    # Then: masking and the 200-char clip are unchanged by the new type.
    text = str(failure.value)
    assert "admin@inst.example" not in text
    assert "123456789012" not in text
    assert len(text) <= len("glm-main 호출 실패: ") + 200


# --- ⓑ 강등: 비민감 메일이 codex 티어로 처리된다 ---------------------------------


def test_digest_degrades_to_codex_when_glm_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: every glm-main call fails the way the 2026-09-03 run did, while the
    # non-GLM tier answers normally.
    glm_calls: list[str] = []
    codex_calls: list[str] = []

    def call_glm(prompt: str, *, sensitive: bool, timeout: float = 180.0):  # noqa: ARG001
        glm_calls.append(prompt)
        raise triage_llm.LlmUnavailableError(_UNAVAILABLE)

    monkeypatch.setattr(triage_llm, "call_glm", call_glm)
    monkeypatch.setattr(
        triage_llm, "call_codex",
        lambda prompt, **kwargs: codex_calls.append(prompt) or _CODEX_RAW,  # noqa: ARG005
    )

    # When: the digest runs for one non-sensitive mail.
    body = _run_digest(monkeypatch, tmp_path, uids=("uid-degrade",))

    # Then: the owner gets the codex result, not the incident's fallbacks.
    assert "코덱스 요약" in body
    assert "(요약 실패)" not in body
    assert "⚠️ 분류 실패" not in body
    assert len(codex_calls) == 2  # classify + digest_summary on the non-GLM tier
    assert glm_calls  # GLM was tried first — degrading is a reaction, not a default

    # And: the audit log marks both fallback calls, and only those.
    records = _records(tmp_path / "llm-calls.jsonl")
    fallback = {
        record["purpose"]: record
        for record in records
        if record.get("fallback_from") == triage_llm.GLM_MODEL
    }
    assert sorted(fallback) == ["classify", "digest_summary"]
    assert all(record["provider"] == triage_llm.NON_GLM_PROVIDER for record in fallback.values())
    assert all(record["sensitive"] is False for record in fallback.values())

    # And: exactly one masked cause line rides the owner message.
    notices = _notice_lines(body)
    assert len(notices) == 1
    assert notices[0].startswith("⚠️ glm-main 사용 불가 — ")
    assert "비민감 1건을 비-GLM 티어로 처리" in notices[0]


def test_healthy_glm_run_carries_no_notice_and_no_fallback_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: glm-main is healthy.
    monkeypatch.setattr(
        triage_llm, "call_glm", lambda prompt, **kwargs: _GLM_RAW  # noqa: ARG005
    )
    monkeypatch.setattr(
        triage_llm, "call_codex",
        lambda prompt, **kwargs: pytest.fail("healthy GLM must not touch the codex tier"),
    )

    # When: the digest runs.
    body = _run_digest(monkeypatch, tmp_path, uids=("uid-healthy",))

    # Then: zero extra lines and zero fallback markers.
    assert "GLM 요약" in body
    assert _notice_lines(body) == []
    assert all("fallback_from" not in record for record in _records(tmp_path / "llm-calls.jsonl"))


# --- ⓒ 런 단위 래치 ---------------------------------------------------------------


def test_latch_stops_retrying_glm_after_the_first_outage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: three non-sensitive mails and a dead glm-main. Today each mail burns
    # its own retries against the same outage.
    glm_calls: list[str] = []
    codex_calls: list[str] = []

    def call_glm(prompt: str, *, sensitive: bool, timeout: float = 180.0):  # noqa: ARG001
        glm_calls.append(prompt)
        raise triage_llm.LlmUnavailableError(_UNAVAILABLE)

    monkeypatch.setattr(triage_llm, "call_glm", call_glm)
    monkeypatch.setattr(
        triage_llm, "call_codex",
        lambda prompt, **kwargs: codex_calls.append(prompt) or _CODEX_RAW,  # noqa: ARG005
    )

    # When: the run processes all three.
    body = _run_digest(monkeypatch, tmp_path, uids=("uid-a", "uid-b", "uid-c"))

    # Then: GLM is probed once with one retry, then never again this run.
    assert len(glm_calls) <= 2
    assert len(codex_calls) == 6  # 3 mails × (classify + summary)
    assert body.count("코덱스 요약") == 3
    notices = _notice_lines(body)
    assert len(notices) == 1
    assert "비민감 3건을 비-GLM 티어로 처리" in notices[0]


def test_latch_does_not_leak_between_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a first run that trips the latch.
    monkeypatch.setattr(
        triage_llm, "call_glm",
        lambda prompt, **kwargs: (_ for _ in ()).throw(  # noqa: ARG005
            triage_llm.LlmUnavailableError(_UNAVAILABLE)
        ),
    )
    monkeypatch.setattr(triage_llm, "call_codex", lambda prompt, **kwargs: _CODEX_RAW)  # noqa: ARG005
    assert _notice_lines(_run_digest(monkeypatch, tmp_path, uids=("uid-first",)))

    # When: glm-main recovers and a second run starts.
    monkeypatch.setattr(triage_llm, "call_glm", lambda prompt, **kwargs: _GLM_RAW)  # noqa: ARG005
    monkeypatch.setattr(
        triage_llm, "call_codex",
        lambda prompt, **kwargs: pytest.fail("a recovered run must not stay latched"),
    )
    body = _run_digest(monkeypatch, tmp_path / "second", uids=("uid-second",))

    # Then: the new run starts from a healthy latch — no state leaks across runs.
    assert "GLM 요약" in body
    assert _notice_lines(body) == []


# --- ⓓ 민감 메일과 codex 동시 실패 -------------------------------------------------


def test_sensitive_mail_never_touches_glm_and_needs_no_notice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a sensitivity-gate hit (patent routing) — GLM is forbidden.
    monkeypatch.setattr(
        triage_llm, "call_glm",
        lambda prompt, **kwargs: pytest.fail("sensitive mail must never reach the GLM tier"),
    )
    codex_calls: list[str] = []
    monkeypatch.setattr(
        triage_llm, "call_codex",
        lambda prompt, **kwargs: codex_calls.append(prompt) or _CODEX_RAW,  # noqa: ARG005
    )

    # When: the digest runs for that mail.
    body = _run_digest(monkeypatch, tmp_path, uids=("uid-sensitive",), sensitive=True)

    # Then: codex handled it as before and no availability notice is invented.
    assert "코덱스 요약" in body
    assert len(codex_calls) == 2
    assert _notice_lines(body) == []
    records = _records(tmp_path / "llm-calls.jsonl")
    assert all("fallback_from" not in record for record in records)
    assert all(record["sensitive"] is True for record in records)


def test_codex_failure_after_glm_outage_keeps_todays_fallbacks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: both tiers are down.
    monkeypatch.setattr(
        triage_llm, "call_glm",
        lambda prompt, **kwargs: (_ for _ in ()).throw(  # noqa: ARG005
            triage_llm.LlmUnavailableError(_UNAVAILABLE)
        ),
    )
    monkeypatch.setattr(
        triage_llm, "call_codex",
        lambda prompt, **kwargs: (_ for _ in ()).throw(  # noqa: ARG005
            triage_llm.LlmCallError("codex one-shot failed rc=1")
        ),
    )

    # When: the digest runs.
    body = _run_digest(monkeypatch, tmp_path, uids=("uid-both-down",))

    # Then: the mail is still listed with today's fail-open markers …
    assert "(요약 실패)" in body
    assert "⚠️ 분류 실패" in body
    # … the forensic failure lines survive …
    purposes = {record["purpose"] for record in _records(tmp_path / "llm-calls.jsonl")}
    assert {"classify_failed", "digest_summary_failed"} <= purposes
    # … and the cause is still stated exactly once.
    assert len(_notice_lines(body)) == 1


def test_notice_line_hides_addresses_and_stays_short(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: an outage message carrying an address and a long id.
    monkeypatch.setattr(
        triage_llm, "call_glm",
        lambda prompt, **kwargs: (_ for _ in ()).throw(  # noqa: ARG005
            triage_llm.LlmUnavailableError(
                "glm-main 호출 실패: quota for admin@inst.example key 987654321012 " + "x" * 300
            )
        ),
    )
    monkeypatch.setattr(triage_llm, "call_codex", lambda prompt, **kwargs: _CODEX_RAW)  # noqa: ARG005

    # When: the notice is composed.
    body = _run_digest(monkeypatch, tmp_path, uids=("uid-mask",))

    # Then: no address, no long id, no mail subject, and a bounded reason.
    notice = _notice_lines(body)[0]
    assert "admin@inst.example" not in notice
    assert "987654321012" not in notice
    assert "Synthetic subject" not in notice
    assert len(notice) <= 220
