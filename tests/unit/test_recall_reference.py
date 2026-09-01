"""참고자료 CLI — 근거를 읽을 수 있게 내놓고, 쓸 수 없으면 사유를 말한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "recall" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import recall_reference  # noqa: E402

from automation import drive_reference  # noqa: E402


def _hit(**overrides: Any) -> drive_reference.ReferenceHit:
    fields: dict[str, Any] = {
        "name": "굴착 오차 관리기준.md",
        "path": "KIMM/2026/굴착 오차 관리기준.md",
        "file_id": "file1",
        "link": "https://drive.google.com/file/d/file1/view",
        "snippet": "굴착 오차는 10 mm 이하로 관리한다.",
        "score": 9,
        "status": drive_reference.OK,
    }
    fields.update(overrides)
    return drive_reference.ReferenceHit(**fields)


def _result(**overrides: Any) -> drive_reference.ReferenceResult:
    fields: dict[str, Any] = {
        "status": drive_reference.OK,
        "root": "KIMM",
        "scanned": 3,
        "hits": (),
        "notes": (),
    }
    fields.update(overrides)
    return drive_reference.ReferenceResult(**fields)


def _stub(monkeypatch: pytest.MonkeyPatch, result: drive_reference.ReferenceResult) -> list[Any]:
    seen: list[Any] = []

    def _search(query: str, limit: int = 3) -> drive_reference.ReferenceResult:
        seen.append((query, limit))
        return result

    monkeypatch.setattr(drive_reference, "search", _search)
    return seen


def test_cli_quotes_the_matching_document(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _stub(monkeypatch, _result(hits=(_hit(),)))

    code = recall_reference.main(["굴착 오차"])
    out = capsys.readouterr().out

    assert code == 0
    assert seen == [("굴착 오차", 3)]
    assert "굴착 오차 관리기준.md" in out
    assert "10 mm 이하로 관리한다" in out
    assert "https://drive.google.com/file/d/file1/view" in out


def test_cli_names_the_reason_when_the_shelf_cannot_be_used(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(
        monkeypatch,
        _result(
            status=drive_reference.ROOT_MISSING,
            scanned=0,
            notes=("참고자료 폴더를 찾지 못했습니다: KIMM",),
        ),
    )

    code = recall_reference.main(["굴착 오차"])
    out = capsys.readouterr().out

    assert code == 0
    assert drive_reference.ROOT_MISSING in out
    assert "참고자료 폴더를 찾지 못했습니다: KIMM" in out


def test_cli_shows_the_read_failure_instead_of_a_snippet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(
        monkeypatch,
        _result(hits=(_hit(name="스캔본.pdf", snippet="", status="읽지 못함: 텍스트 레이어가 없습니다"),)),
    )

    recall_reference.main(["굴착 오차"])
    out = capsys.readouterr().out

    assert "읽지 못함: 텍스트 레이어가 없습니다" in out


def test_cli_json_mode_carries_every_hit_field(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub(monkeypatch, _result(hits=(_hit(),), notes=("폴더 60개까지만 훑었습니다",)))

    recall_reference.main(["굴착 오차", "--json", "--limit", "5"])
    parsed = json.loads(capsys.readouterr().out)

    assert parsed["status"] == drive_reference.OK
    assert parsed["root"] == "KIMM"
    assert parsed["notes"] == ["폴더 60개까지만 훑었습니다"]
    assert parsed["hits"][0]["path"] == "KIMM/2026/굴착 오차 관리기준.md"
    assert parsed["hits"][0]["score"] == 9


def test_cli_refuses_an_empty_query(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _result())

    with pytest.raises(SystemExit) as refused:
        recall_reference.main(["   "])

    assert refused.value.code == 2
