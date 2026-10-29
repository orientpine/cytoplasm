"""A staged mail attachment goes out under a Korean display name (owner 2026-10-29, a submission form).

The node refused "과제_행정7_홍길동.pptx" as a stage name, so the card carried the ASCII name.
The stage name stays plain; `stage <name> --name <display>` records the name the mail shows.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_core  # noqa: E402

from automation.yoodori_gate import yoodori_read_gate as gate  # noqa: E402
from automation.yoodori_gate import yoodori_request_runtime as runtime  # noqa: E402

STAGED = "task_admin7_Hong.pptx"
SHOWN = "과제_행정7_홍길동.pptx"
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]


def _compose(attachments: list[str]) -> list[str]:
    args = ["--to", "a@example.invalid", "--subject", "제출", "--body", "제출합니다."]
    for item in attachments:
        args += ["--attachment", item]
    _, argv, _ = gate._card("mail-compose", [*args, *ORIGIN])
    return [argv[i + 1] for i, word in enumerate(argv) if word == "--attachment"]


def test_the_gate_parses_stage_with_a_display_name() -> None:
    # Given / When: the stage command YooDori sends.
    name, argv, _ = gate.build(["stage", STAGED, "--name", SHOWN])
    # Then: the plain stage name and the display name both arrive.
    assert (name, argv) == ("stage", [STAGED, SHOWN])


def test_a_staged_file_goes_out_under_its_display_name(tmp_path: Path, monkeypatch) -> None:
    # Given: a file staged with a Korean display name.
    staging = tmp_path / "staging"
    monkeypatch.setattr(gate, "STAGING", staging)
    runtime.stage_named(staging, STAGED, SHOWN, io.BytesIO(b"PK\x03\x04 deck"))
    # When: a mail card attaches it by its stage name.
    [attached] = _compose([STAGED])
    # Then: the compose call gets the same bytes under the Korean name, inside staging,
    # and the card manifest (what the owner and the recipient see) carries that name.
    path = Path(attached)
    assert path.name == SHOWN
    assert path.resolve().is_relative_to(staging.resolve())
    assert path.samefile(staging / STAGED)
    [entry] = triage_core.build_attachment_manifest((attached,))
    assert entry["display_name"] == SHOWN


def test_two_attachments_keep_their_order_and_names(tmp_path: Path, monkeypatch) -> None:
    # Given: a deck with a display name and a pdf without one.
    staging = tmp_path / "staging"
    monkeypatch.setattr(gate, "STAGING", staging)
    runtime.stage_named(staging, STAGED, SHOWN, io.BytesIO(b"deck"))
    runtime.stage_named(staging, "plain.pdf", None, io.BytesIO(b"%PDF"))
    # When / Then: the deck is renamed, the pdf keeps its name, order unchanged.
    names = [Path(p).name for p in _compose([STAGED, "plain.pdf"])]
    assert names == [SHOWN, "plain.pdf"]


def test_a_re_upload_without_a_name_drops_the_old_name(tmp_path: Path, monkeypatch) -> None:
    # Given: the same stage name uploaded again without --name.
    staging = tmp_path / "staging"
    monkeypatch.setattr(gate, "STAGING", staging)
    runtime.stage_named(staging, STAGED, SHOWN, io.BytesIO(b"old"))
    runtime.stage_named(staging, STAGED, None, io.BytesIO(b"new"))
    # When / Then: the new bytes go out under the stage name, not the stale display name.
    [attached] = _compose([STAGED])
    assert Path(attached).name == STAGED and Path(attached).read_bytes() == b"new"


def test_a_changed_upload_under_the_same_name_is_not_the_old_link(tmp_path: Path, monkeypatch) -> None:
    # Given: a named upload attached once, then replaced with new bytes under the same names.
    staging = tmp_path / "staging"
    monkeypatch.setattr(gate, "STAGING", staging)
    runtime.stage_named(staging, STAGED, SHOWN, io.BytesIO(b"first"))
    [first] = _compose([STAGED])
    runtime.stage_named(staging, STAGED, SHOWN, io.BytesIO(b"second"))
    # When / Then: the second card gets the second bytes; the first card's file is untouched.
    [second] = _compose([STAGED])
    assert Path(second).read_bytes() == b"second" and Path(first).read_bytes() == b"first"


@pytest.mark.parametrize("shown", [
    "../과제_행정7.pptx",          # path
    "폴더/과제_행정7.pptx",         # path
    "AX\\MVP_행정7.pptx",            # Windows path
    "과제_행정7\x01.pptx",          # control
    "AX-MVP_\u202e7xtpp.pptx",       # bidi override (spoofed extension)
    "과제_행정7\u200b.pptx",        # zero-width format character
    "과제_행정7.pdf",              # another extension than the staged file
    ".pptx",                         # no name before the extension
    "." + "가" * 10 + ".pptx",        # hidden file
    "가" * 116 + ".pptx",             # 121 characters
    "AX:MVP?.pptx",                  # Windows cannot save it
])
def test_unsafe_display_names_are_refused(tmp_path: Path, shown: str) -> None:
    # Given / When / Then: the upload is refused before anything lands in staging.
    with pytest.raises(runtime.Refused):
        runtime.stage_named(tmp_path / "staging", STAGED, shown, io.BytesIO(b"deck"))
    assert not (tmp_path / "staging").exists()


def test_a_decomposed_korean_name_is_stored_composed(tmp_path: Path) -> None:
    # Given: a name typed on a Mac (decomposed Hangul jamo).
    import unicodedata
    decomposed = unicodedata.normalize("NFD", SHOWN)
    # When / Then: the recipient sees the composed form.
    assert runtime.display_name(decomposed, STAGED) == SHOWN


def test_a_bad_received_name_keeps_the_staged_name(tmp_path: Path, monkeypatch) -> None:
    # Given: a received attachment whose recorded name has a size tail ("x.wrl 37.86 MB").
    staging = tmp_path / "staging"
    monkeypatch.setattr(gate, "STAGING", staging)
    runtime.stage_file(staging, "mail-7-1.wrl", io.BytesIO(b"x"))
    runtime.stage_file(staging, "mail-7-1.wrl.name", io.BytesIO("모형.wrl 37.86 MB".encode()))
    # When / Then: the card still forms, under the staged name.
    assert [Path(p).name for p in _compose(["mail-7-1.wrl"])] == ["mail-7-1.wrl"]
