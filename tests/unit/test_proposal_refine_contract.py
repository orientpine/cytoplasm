from __future__ import annotations

from skills.proposal.scripts.proposal_refine import refine_section, verify_invariants


def _prose(length: int) -> str:
    return "가" * (length - len("한다.")) + "한다."


def _expanded_prose_transport(text: str, host: str, timeout: float) -> str:
    del text, host, timeout
    return _prose(980)


def test_polishing_can_expand_from_input_target_within_output_ceiling() -> None:
    result = refine_section(
        _prose(840),
        _expanded_prose_transport,
        char_budget=900,
    )

    assert result.text == _prose(980)


def test_polishing_still_rejects_output_above_ceiling() -> None:
    at_ceiling = verify_invariants(_prose(900), _prose(990), char_budget=900)
    above_ceiling = verify_invariants(_prose(900), _prose(991), char_budget=900)

    assert next(check for check in at_ceiling if check.name == "char-budget").passed
    assert not next(
        check for check in above_ceiling if check.name == "char-budget"
    ).passed
