"""Runtime/input status regression kept separate from the FS3-pinned slide suite."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "skills" / "meeting" / "scripts"

_ENTRYPOINT = """
import json
import sys
sys.path.insert(0, sys.argv[2])
import meeting_slides

deck = meeting_slides.extract_deck(__import__('pathlib').Path(sys.argv[1]))
print(json.dumps({'status': deck.status, 'text': deck.text, 'slides': deck.slide_count}))
"""


def _extract(path: Path, runtime: Path, work: Path) -> dict[str, object]:
    env = os.environ.copy()
    env["AUTOPHAGY_REPO_ROOT"] = str(runtime)
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _ENTRYPOINT, str(path), str(SCRIPTS)],
        cwd=work,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(completed.stdout)


def _runtime(tmp_path: Path, document_text: str | None = None) -> Path:
    root = tmp_path / "runtime"
    package = root / "automation"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    if document_text is not None:
        (package / "document_text.py").write_text(document_text, encoding="utf-8")
    return root


def test_missing_runtime_is_not_reported_as_a_missing_slide(tmp_path: Path) -> None:
    status = _extract(tmp_path / "missing.pptx", tmp_path / "no-runtime", tmp_path)["status"]

    assert status == "읽지 못함: 본문 추출 런타임을 찾지 못했습니다"


@pytest.mark.parametrize(
    "document_text",
    [None, "RUNTIME_GENERATION = 'before-extract-document'\n"],
)
def test_stale_runtime_has_a_version_skew_status(
    tmp_path: Path, document_text: str | None
) -> None:
    status = _extract(
        tmp_path / "missing.pptx", _runtime(tmp_path, document_text), tmp_path
    )["status"]

    assert status == "읽지 못함: 본문 추출 런타임 버전이 맞지 않습니다"


def test_extractor_failure_is_not_reported_as_a_runtime_failure(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        "def extract_document(path, *, max_bytes):\n    raise OSError('broken deck')\n",
    )
    deck = tmp_path / "broken.pptx"
    deck.write_bytes(b"not-a-presentation")

    status = _extract(deck, runtime, tmp_path)["status"]

    assert status == "읽지 못함: 발표자료 본문 추출에 실패했습니다"


def test_real_extractor_preserves_missing_and_broken_slide_statuses(tmp_path: Path) -> None:
    missing = _extract(tmp_path / "missing.pptx", REPO, tmp_path)
    broken_path = tmp_path / "broken.pptx"
    broken_path.write_bytes(b"not-a-presentation")
    broken = _extract(broken_path, REPO, tmp_path)

    assert missing == {
        "status": "읽지 못함: 파일을 찾을 수 없습니다",
        "text": "",
        "slides": 0,
    }
    assert broken == {
        "status": "읽지 못함: 파일을 여는 데 실패했습니다",
        "text": "",
        "slides": 0,
    }
