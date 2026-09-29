"""판정 → 사람용 텍스트·기계용 JSON. 줄 머리의 `[FAIL] <key>` 는 설치기 판정 줄과 같은 모양이다."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from automation.doctor.capabilities import Finding
from automation.install.checks import Status

WORD: Final = {Status.PASS: "통과", Status.WARN: "확인", Status.FAIL: "고장"}
SUMMARY_PREFIX: Final = "--- DOCTOR"
_ORDER: Final = {Status.FAIL: 0, Status.WARN: 1, Status.PASS: 2}


def counts(findings: Sequence[Finding]) -> dict[Status, int]:
    return {status: sum(1 for finding in findings if finding.status is status) for status in Status}


def render_text(account: str, role: str, findings: Sequence[Finding]) -> str:
    lines = [f"doctor · {account} ({role}) — 연결·승인·동작 점검"]
    lines += [f"[{f.status}] {f.key} · {f.label} — {f.detail}" for f in findings]
    tally = counts(findings)
    lines.append(
        f"{SUMMARY_PREFIX} {account}: 통과 {tally[Status.PASS]} · 확인 {tally[Status.WARN]} · 고장 {tally[Status.FAIL]}"
    )
    pending = sorted((f for f in findings if f.status is not Status.PASS), key=lambda f: _ORDER[f.status])
    if pending:
        lines.append("")
        lines.append("조치가 필요한 항목 (고장 먼저):")
        for finding in pending:
            lines.append(f"■ [{WORD[finding.status]}] {finding.label} — 멈추는 것: {finding.affects}")
            lines += [f"  {index}. {step}" for index, step in enumerate(finding.steps, 1)]
    lines.append("인프라(서비스 유닛·릴리스·타이머)는 automation/healthcheck.sh 가 따로 본다.")
    return "\n".join(lines)


def to_json(account: str, role: str, findings: Sequence[Finding]) -> dict[str, object]:
    return {
        "account": account,
        "role": role,
        "findings": [
            {"key": f.key, "label": f.label, "status": str(f.status), "detail": f.detail,
             "affects": f.affects, "steps": list(f.steps), "subjects": list(f.subjects)}
            for f in findings
        ],
    }


def from_json(payload: object) -> tuple[str, str, tuple[Finding, ...]] | None:
    """운영자 모드가 계정별 자식 진단의 JSON 을 되읽는다 — 모양이 틀리면 None(판정 불가)."""
    if not isinstance(payload, dict):
        return None
    rows = payload.get("findings")
    if not isinstance(rows, list):
        return None
    findings: list[Finding] = []
    try:
        for row in rows:
            findings.append(Finding(
                str(row["key"]), str(row["label"]), Status(row["status"]), str(row["detail"]),
                str(row["affects"]), tuple(str(step) for step in row["steps"]),
                tuple(str(subject) for subject in row.get("subjects", ())),
            ))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return str(payload.get("account", "?")), str(payload.get("role", "?")), tuple(findings)
