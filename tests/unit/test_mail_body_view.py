"""Readable mail projections; synthetic documents only, no mail/network I/O."""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))


@pytest.fixture()
def view() -> ModuleType:
    assert importlib.util.find_spec("mail_body_view") is not None, "mail body renderer is missing"
    return importlib.import_module("mail_body_view")


def test_headers_when_vendor_markdown_is_present(view: ModuleType) -> None:
    # Given: vendor front matter plus its duplicated display headers and attachments.
    document = ('---\nsubject: "문서 검토"\nfrom: "a@example.invalid"\n'
                'to: "b@example.invalid"\ncc: "c@example.invalid"\n'
                'date: "2026-09-01T09:00:00"\nattachments:\n  - "one.pdf"\n'
                '  - "two.pdf"\n---\n\n# 문서 검토\n\n**From**: a@example.invalid\n'
                '\n## Attachments\n\n- [one.pdf](one.pdf)\n- [two.pdf](two.pdf)\n'
                '\n## Body\n\n확인 부탁드립니다.\n')
    # When
    chunks = view.render({"body": document, "subject": "row fallback"})
    # Then: each metadata field appears once, in bullet form, before the body.
    header, body = chunks[0].split("\n\n", 1)
    assert header.splitlines() == [
        "- 제목: 문서 검토", "- 보낸 사람: a@example.invalid",
        "- 받는 사람·참조: b@example.invalid · 참조: c@example.invalid",
        "- 날짜: 2026-09-01T09:00:00", "- 첨부 2건",
    ]
    assert body == "확인 부탁드립니다."


def test_markdown_when_blank_runs_and_html_breaks_are_present(view: ModuleType) -> None:
    # Given
    markdown = "- 하나\n  - 하위\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n[자료](https://example.invalid/doc)"
    body = "앞<br>뒤\n\n\n&nbsp;\n\n" + markdown
    # When
    rendered = "\n\n".join(view.render({"body": body}))
    # Then
    assert rendered.endswith("앞\n뒤\n\n" + markdown)
    assert "\n\n\n" not in rendered


@pytest.mark.parametrize("quote", ["-----원본 메시지-----\n옛 제목\n옛 본문", "> 옛 제목\n> 옛 본문\n> 끝"])
def test_quote_folding_when_original_is_present(view: ModuleType, quote: str) -> None:
    # Given
    mail = {"body": "새 본문\n\n" + quote}
    # When
    rendered = "\n\n".join(view.render(mail))
    # Then
    assert "새 본문" in rendered and "옛 본문" not in rendered
    assert rendered.endswith("— 원문 인용 3줄 생략 (전체: `--full`) —")


def test_inline_responses_when_quote_lines_are_folded(view: ModuleType) -> None:
    # Given
    mail = {"body": "> 질문 하나\n답 하나\n\n> 질문 둘\n답 둘"}
    # When
    rendered = "\n\n".join(view.render(mail))
    # Then
    assert "답 하나" in rendered and "답 둘" in rendered
    assert rendered.endswith("— 원문 인용 2줄 생략 (전체: `--full`) —")


def test_quotes_when_full_is_requested(view: ModuleType) -> None:
    # Given: signature before a quoted original must not swallow the original.
    quote = "-----원본 메시지-----\n> 옛 본문\n원문 끝"
    mail = {"body": "새 본문\n\n-- \n작성자\n연락처\n\n" + quote}
    # When
    rendered = "\n\n".join(view.render(mail, full=True))
    # Then
    assert rendered.endswith(quote)
    assert "--full" not in rendered


@pytest.mark.parametrize("footer", [
    "This email is intended for the recipient.\nDo not distribute.\nDelete if received in error.",
    "본 메일은 지정 수신자에게만 제공됩니다.\n무단 배포 금지.\n오수신 시 삭제.",
    "-- \n작성자\n연락처\n주소",
])
def test_footer_when_signature_or_disclaimer_is_present(view: ModuleType, footer: str) -> None:
    # Given
    mail = {"body": "새 본문\n\n" + footer}
    # When
    rendered = "\n\n".join(view.render(mail))
    # Then: body plus a single footer line, not the entire boilerplate.
    body = rendered.split("\n\n", 1)[1]
    assert body.startswith("새 본문\n\n")
    assert len(body.split("\n\n", 1)[1].splitlines()) == 1


def test_chunks_when_paragraphs_fit_individually(view: ModuleType) -> None:
    # Given
    paragraphs = [f"[{i}] " + "가" * 950 for i in range(12)]
    # When
    chunks = view.render({"body": "\n\n".join(paragraphs)})
    # Then: no paragraph is cut; multi-digit numbering is inside the budget.
    assert len(chunks) > 9
    for i, chunk in enumerate(chunks, 1):
        assert len(chunk) <= 1900
        assert chunk.endswith(f"({i}/{len(chunks)})")
    for paragraph in paragraphs:
        assert sum(paragraph in chunk for chunk in chunks) == 1


@pytest.mark.parametrize("body", ["가" * 8000, "😀" * 8000, ("문장 " * 800) + "끝"],
                         ids=["unbroken", "emoji", "words"])
def test_chunks_when_a_paragraph_exceeds_limit(view: ModuleType, body: str) -> None:
    # Given
    mail = {"body": body}
    # When
    chunks = view.render(mail)
    # Then: even unbroken text is bounded and no body character is lost.
    assert all(len(chunk) <= 1900 for chunk in chunks)
    content = "".join(re.sub(r"\n\n\(\d+/\d+\)$", "", chunk) for chunk in chunks)
    assert content.split("\n\n", 1)[1] == body


def test_link_when_long_paragraph_crosses_chunk_boundary(view: ModuleType) -> None:
    # Given
    link = "[자료 링크](https://example.invalid/" + "x" * 100 + ")"
    body = "가 " * 850 + link + " 나" * 200
    # When
    chunks = view.render({"body": body})
    # Then
    assert sum(link in chunk for chunk in chunks) == 1
    assert all(len(chunk) <= 1900 for chunk in chunks)


def test_short_mail_when_only_row_metadata_exists(view: ModuleType) -> None:
    # Given
    mail = {"subject": "제목", "sender": "a@example.invalid", "body": None}
    # When
    chunks = view.render(mail)
    # Then
    assert len(chunks) == 1 and "(1/1)" not in chunks[0]
    assert "- 제목: 제목" in chunks[0] and "- 첨부 0건" in chunks[0]
