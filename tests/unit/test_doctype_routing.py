from __future__ import annotations

import pytest

from skills.doctype.scripts.doctype_routing import (
    Destination,
    SaveRoute,
    classify_save_request,
)


def test_sensitive_content_is_gated_before_explicit_destination() -> None:
    # Given: a sensitive document with an otherwise explicit Drive destination.
    # When: the save request is classified.
    route = classify_save_request(
        "Drive에 보고서를 저장해줘",
        has_file_artifact=True,
        sensitivity=frozenset({"patent-sensitive"}),
    )

    # Then: the sensitivity gate owns the request without clarification.
    assert route == SaveRoute(("gated",), "sensitive-gated", False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("옵시디언에 저장해줘", ("obsidian",), id="korean-obsidian"),
        pytest.param("Save this in Obsidian", ("obsidian",), id="english-obsidian"),
        pytest.param("구글 드라이브에 올려줘", ("drive",), id="korean-google-drive"),
        pytest.param("Save to Drive", ("drive",), id="english-drive"),
        pytest.param("로컬에만 저장해줘", ("local",), id="korean-local"),
        pytest.param("Keep it local", ("local",), id="english-local"),
        pytest.param(
            "옵시디언이랑 드라이브 둘 다 저장해줘",
            ("obsidian", "drive"),
            id="named-compound",
        ),
        pytest.param("Save it to both", ("obsidian", "drive"), id="english-both"),
    ],
)
def test_explicit_destination_beats_defaults(
    text: str,
    expected: tuple[Destination, ...],
) -> None:
    # Given: a request naming its save destination.

    # When: the request is classified despite a file artifact default being available.
    route = classify_save_request(text, has_file_artifact=True)

    # Then: only the named destinations are selected.
    assert route == SaveRoute(expected, "explicit-destination", False)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("이 내용을 개인노트 저장해줘", id="compact-personal-note"),
        pytest.param("이건 개인 노트로 남겨줘", id="spaced-personal-note"),
        pytest.param("내 노트에 정리해줘", id="my-note"),
    ],
)
def test_personal_note_routes_to_obsidian_alone(text: str) -> None:
    # Given: personal-note wording without a separate Drive request.

    # When: the request is classified.
    route = classify_save_request(text, has_file_artifact=True)

    # Then: Drive is not added to the personal note destination.
    assert route == SaveRoute(("obsidian",), "personal-note", False)


def test_personal_note_with_separate_drive_request_routes_to_both() -> None:
    # Given: a personal note request that separately names Drive.

    # When: the compound request is classified.
    route = classify_save_request(
        "개인 노트로 남기고 드라이브에도 저장해줘",
        has_file_artifact=True,
    )

    # Then: both explicitly requested destinations are selected.
    assert route == SaveRoute(
        ("obsidian", "drive"),
        "explicit-destination",
        False,
    )


def test_destination_unspecified_file_defaults_to_drive() -> None:
    # Given: a report file artifact with no destination wording.

    # When: its save destination is classified.
    route = classify_save_request("주간 보고서를 파일로 만들어줘", has_file_artifact=True)

    # Then: the owner's private Drive is the deterministic default.
    assert route == SaveRoute(("drive",), "default-drive", False)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("광합성 원리를 설명해줘", id="plain-answer"),
        pytest.param("연구 방향을 같이 브레인스토밍하자", id="brainstorming"),
        pytest.param("아이디어 좀 줘", id="ideas"),
    ],
)
def test_no_save_intent_routes_to_none(text: str) -> None:
    # Given: a conversational request with neither a file nor save intent.

    # When: the request is classified.
    route = classify_save_request(text, has_file_artifact=False)

    # Then: no storage side effect is inferred.
    assert route == SaveRoute(("none",), "no-save-intent", False)


def test_negated_drive_is_not_selected() -> None:
    # Given: a file request that explicitly prohibits Drive upload.

    # When: no alternative destination is named.
    route = classify_save_request(
        "보고서는 만들어도 드라이브에는 올리지 마",
        has_file_artifact=True,
    )

    # Then: the router fails closed instead of guessing another destination.
    assert route == SaveRoute((), "ambiguous", True)


def test_negated_drive_is_removed_from_compound_destination() -> None:
    # Given: Obsidian is requested while Drive is explicitly prohibited.

    # When: the compound request is classified.
    route = classify_save_request(
        "옵시디언에 저장하고 드라이브에는 올리지 마",
        has_file_artifact=True,
    )

    # Then: only the positive explicit destination remains.
    assert route == SaveRoute(("obsidian",), "explicit-destination", False)


def test_global_save_negation_routes_to_none() -> None:
    # Given: a file can be produced but the owner prohibits saving it.

    # When: the request is classified.
    route = classify_save_request(
        "보고서는 만들어줘. 저장하지 마",
        has_file_artifact=True,
    )

    # Then: the explicit no-save instruction beats the file default.
    assert route == SaveRoute(("none",), "no-save-intent", False)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("어딘가에 저장해줘", id="unknown-destination"),
        pytest.param("옵시디언이나 드라이브 중 하나에 저장해줘", id="alternative-destinations"),
        pytest.param("드라이브에 저장할까?", id="destination-question"),
    ],
)
def test_ambiguous_save_request_requires_clarification(text: str) -> None:
    # Given: save wording that cannot determine one intended action.

    # When: the request is classified without a known file artifact.
    route = classify_save_request(text, has_file_artifact=False)

    # Then: no destination is guessed and clarification is required.
    assert route == SaveRoute((), "ambiguous", True)


def test_cli_save_route_without_save_request_does_not_default_to_drive() -> None:
    """An absent --save-request is NOT a request to store the artifact externally.

    Regression: doctype_cli._save_route hardcoded has_file_artifact=True, so
    `register-from-example` with no --save-request routed to ("drive",) and
    attempted a Drive upload. That both broke the offline deploy scenario and
    silently exported a registry artifact the owner never asked to save.
    """
    import argparse

    from skills.doctype.scripts import doctype_cli

    args = argparse.Namespace(save_request="")
    route = doctype_cli._save_route(args, frozenset())
    assert route.destinations == ("none",)
    assert route.clarify is False


def test_cli_save_route_with_explicit_request_still_routes() -> None:
    import argparse

    from skills.doctype.scripts import doctype_cli

    args = argparse.Namespace(save_request="이 안내문 파일로 저장해줘")
    route = doctype_cli._save_route(args, frozenset())
    assert route.destinations == ("drive",)
