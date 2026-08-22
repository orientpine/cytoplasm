from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "mail" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from automation.knowledge.pack import EvidenceItem, EvidencePack, KnowledgeQuery, Verdict  # noqa: E402
import mail_evidence  # noqa: E402
import mail_knowledge  # noqa: E402
import triage_cli  # noqa: E402
import triage_core  # noqa: E402
import triage_pipeline  # noqa: E402
import triage_sensitivity  # noqa: E402


def _item(*, content: str = "상대와 지난번 합의한 일정", sensitivity: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        "E1", "rag", "note", "contacts/peer.md", "상대 관련 노트", "2026-08-18",
        "path", 0.8, True, None, None, sensitivity, content, "a" * 64,
    )


def _pack(verdict: Verdict = "hit", *, item: EvidenceItem | None = None) -> EvidencePack:
    query = KnowledgeQuery("peer@example.invalid 일정", "synthesize", caller="mail")
    items = (_item() if item is None else item,) if verdict == "hit" else ()
    state = "hit" if verdict == "hit" else "no_memory" if verdict == "no_evidence" else "unavailable"
    return EvidencePack(
        "knowledge-v1", query, verdict, items,
        {"rag": state, "wiki": "none" if verdict != "unavailable" else state,
         "twin": "none" if verdict != "unavailable" else state},
    )


def test_adapter_builds_counterparty_subject_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[KnowledgeQuery] = []

    class Facade:
        KnowledgeQuery = KnowledgeQuery

        @staticmethod
        def collect_evidence(query: KnowledgeQuery) -> EvidencePack:
            captured.append(query)
            return _pack()

    monkeypatch.setattr(mail_knowledge, "module", lambda name: Facade)
    mail_knowledge.collect("peer@example.invalid", "일정 확인", "참석 가능")
    assert captured == [KnowledgeQuery(
        "peer@example.invalid\n일정 확인\n참석 가능", "synthesize", limit=8, caller="mail"
    )]


def test_mail_draft_body_never_contains_private_evidence_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv(
        "TRIAGE_RULES_FILE", str(ROOT / "skills" / "mail" / "configs" / "sensitivity-rules.yaml")
    )
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    draft = triage_pipeline.compose_and_post(
        "peer@example.invalid", "일정", "일정을 확인했습니다 [E1]. 허위 [E9].",
        post=False, evidence_pack=_pack(),
    )
    assert "[E" not in draft["body"]
    assert "일정을 확인했습니다" in draft["body"]


@pytest.mark.parametrize(("verdict", "message"), (("no_evidence", "근거 없음"), ("unavailable", "근거 수집 불가")))
def test_non_hit_pack_owner_notice_is_deterministic_and_generation_continues(
    verdict: Verdict, message: str,
) -> None:
    pack = _pack(verdict)
    assert message in mail_evidence.owner_notice(pack)
    assert mail_evidence.sanitize_draft_body("생성된 초안", pack) == "생성된 초안"


def test_sensitive_evidence_is_added_to_pre_llm_mail_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[bool] = []
    rules = triage_sensitivity.load_rules(ROOT / "skills" / "mail" / "configs" / "sensitivity-rules.yaml")
    monkeypatch.setattr(
        triage_cli.triage_pipeline.triage_llm, "classify",
        lambda **kwargs: captured.append(bool(kwargs["sensitive"])) or (
            triage_core.Classification("important", True, False, False, "", "test"), "codex"
        ),
    )
    evidence = "[[PATENT-SENSITIVE-RECALL]] patent filing"
    triage_cli.triage_pipeline._gate_and_classify(
        "u-1", {"subject": "일정", "sender": "peer@example.invalid", "body": "확인"},
        rules, evidence_text=evidence,
    )
    assert captured == [True]


def test_evidence_preview_degrades_when_entity_preflight_module_is_absent(
    tmp_path: Path,
) -> None:
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    result = subprocess.run(
        [
            sys.executable, str(SCRIPTS / "triage_cli.py"), "evidence",
            "--counterparty", "peer@example.invalid", "--subject", "일정",
        ],
        cwd=tmp_path,
        env={**os.environ, "AUTOPHAGY_REPO_ROOT": str(empty_repo)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "근거 수집 불가" in result.stdout
    assert "Traceback" not in result.stderr


def test_unavailable_preflight_skips_evidence_collection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        triage_cli.mail_preflight, "ensure_cli_evidence_query", lambda draft: False,
    )
    monkeypatch.setattr(
        triage_cli.mail_knowledge, "collect",
        lambda *args: (_ for _ in ()).throw(AssertionError("evidence collection must be skipped")),
    )

    assert triage_cli.cmd_evidence(argparse.Namespace(
        counterparty="peer@example.invalid", subject="일정", material="",
        json=False,
    )) == 0
    assert "근거 수집 불가" in capsys.readouterr().out


def test_entity_clarify_preflight_blocks_evidence_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(triage_cli.triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_cli.triage_gate, "has_draft_for", lambda uid: False)
    monkeypatch.setattr(triage_cli, "_get_mail", lambda uid: {
        "uid": uid, "subject": "일정", "sender": "Peer <peer@example.invalid>", "body": "확인",
    })
    monkeypatch.setattr(
        triage_cli.mail_preflight, "ensure_cli_evidence_query",
        lambda draft: (_ for _ in ()).throw(triage_cli.triage_gate.GateError("ENTITY-CLARIFY 누구인지 확인", 2)),
    )
    calls = 0

    def collect(*args: Any) -> EvidencePack:
        nonlocal calls
        calls += 1
        return _pack()

    monkeypatch.setattr(triage_cli.mail_knowledge, "collect", collect)
    args = argparse.Namespace(
        uid="u-1", instruction="회신", no_post=True, attachment=[], with_evidence=True,
    )
    with pytest.raises(triage_cli.triage_gate.GateError, match="ENTITY-CLARIFY"):
        triage_cli.cmd_draft(args)
    assert calls == 0


def test_private_sidecar_and_json_preview_expose_owner_only_sources(
    tmp_path: Path,
) -> None:
    sidecar = mail_evidence.write_sidecar(tmp_path, "draft1", _pack())
    assert json.loads(sidecar.read_text(encoding="utf-8"))["verdict"] == "hit"
    assert sidecar.stat().st_mode & 0o777 == 0o600
    preview = json.loads(mail_evidence.preview(_pack(), as_json=True))
    assert preview == {"evidence_count": 1, "layers": _pack().layers}
