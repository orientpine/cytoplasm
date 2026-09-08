from __future__ import annotations

from automation.stt_eval.normalize import NORMALIZATION_VERSION, normalize_cer


def test_normalize_cer_protects_numeric_atoms_and_removes_other_punctuation() -> None:
    normalized = normalize_cer("삼십 %, 1,000원 -3.5%.", keep_spaces=True)
    assert "1,000" in normalized
    assert "-3.5%" in normalized
    assert "." not in normalized.replace("-3.5%", "")
    assert "," not in normalized.replace("1,000", "")


def test_normalize_cer_uses_nfc_casefold_and_removes_spaces_by_default() -> None:
    assert normalize_cer("Cafe\u0301  가  나") == "café가나"
    assert normalize_cer("Cafe\u0301  가  나", keep_spaces=True) == "café 가 나"


def test_normalize_cer_keeps_numeric_atoms_without_numeric_conversion() -> None:
    assert normalize_cer("＋1,000 / .5% -3.5%") == "＋1,000.5%-3.5%"


def test_normalization_version_is_stable() -> None:
    assert NORMALIZATION_VERSION == "ko-cer-v1"
