"""RC-4: 릴리스 영수증 프로브 — 없음·불일치·판독 불가가 전부 FAIL 임을 고정한다.

영수증은 "이 릴리스가 한 번 전량 반영되었다"의 증명이고, 이 프로브는 그 증명이 현재
릴리스에 대해 존재하는지를 상시 조회로 만든다. 어느 실패든 조용히 PASS 로 접히면
"아마 다 배포됐겠지"라는 추측이 되살아난다 — 그 추측이 중복 개발의 원인이었다.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_PROBE: Final = _REPO / "automation" / "release_receipt_probe.sh"


def _run(tmp_path: Path, *, receipt: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            "bash",
            "-c",
            f'source "{_PROBE}"; probe_release_fully_deployed node ops "{receipt}"',
        ),
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin",
            "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(tmp_path / "current"),
        },
    )


def _release(tmp_path: Path, sha: str) -> None:
    (tmp_path / "releases" / sha).mkdir(parents=True, exist_ok=True)
    current = tmp_path / "current"
    if current.is_symlink():
        current.unlink()
    current.symlink_to(tmp_path / "releases" / sha)


def test_matching_receipt_passes(tmp_path: Path) -> None:
    _release(tmp_path, "aaa111")
    receipt = tmp_path / "receipt.json"
    _ = receipt.write_text(json.dumps({"release_sha": "aaa111"}), encoding="utf-8")

    result = _run(tmp_path, receipt=receipt)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RECEIPT-PASS" in result.stderr


def test_missing_receipt_fails_and_names_the_remedy(tmp_path: Path) -> None:
    _release(tmp_path, "aaa111")

    result = _run(tmp_path, receipt=tmp_path / "absent.json")

    assert result.returncode != 0
    assert "RECEIPT-MISSING" in result.stderr
    assert "deploy_all.sh" in result.stderr


def test_stale_receipt_fails(tmp_path: Path) -> None:
    _release(tmp_path, "bbb222")
    receipt = tmp_path / "receipt.json"
    _ = receipt.write_text(json.dumps({"release_sha": "aaa111"}), encoding="utf-8")

    result = _run(tmp_path, receipt=receipt)

    assert result.returncode != 0
    assert "RECEIPT-STALE" in result.stderr


def test_unreadable_receipt_is_unknown_not_pass(tmp_path: Path) -> None:
    _release(tmp_path, "aaa111")
    receipt = tmp_path / "receipt.json"
    _ = receipt.write_text("not-json", encoding="utf-8")

    result = _run(tmp_path, receipt=receipt)

    assert result.returncode != 0
    assert "RECEIPT-UNKNOWN" in result.stderr


def test_unreadable_release_pointer_is_unknown(tmp_path: Path) -> None:
    result = _run(tmp_path, receipt=tmp_path / "receipt.json")  # current 심링크 자체가 없음

    assert result.returncode != 0
    assert "RECEIPT-UNKNOWN" in result.stderr
