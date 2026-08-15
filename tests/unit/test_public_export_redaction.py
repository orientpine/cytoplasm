from __future__ import annotations

from pathlib import Path

from automation.public_export_redaction import redact_vendor_tree


def test_redact_vendor_tree_deidentifies_byte_preserved_sources(tmp_path: Path) -> None:
    # Given: byte-preserved vendor files containing production-shaped identity data.
    resolve = tmp_path / "skills" / "mail" / "vendor" / "mailon" / "resolve.py"
    requirements = tmp_path / "skills" / "mail" / "vendor" / "requirements.txt"
    offline = tmp_path / "skills" / "mail" / "vendor" / "tests" / "test_offline.py"
    resolve.parent.mkdir(parents=True)
    offline.parent.mkdir(parents=True)
    _ = resolve.write_text(
        '#   대표 도메인 "김샘플" <k@example.invalid> :: 예시연구원 - 예시센터\n',
        encoding="utf-8",
    )
    _ = requirements.write_text(
        "# Versions locked to the set validated on prod (example123, CPython 3.13)\n",
        encoding="utf-8",
    )
    _ = offline.write_text(
        'ACCOUNT = "person@example-lab.re.kr"\n'
        'me = f"\\"홍길동\\" <{ACCOUNT}>"\n'
        'org = "예시연구원 - 예시센터"\n',
        encoding="utf-8",
    )

    # When: the public snapshot redactor processes only the exported copy.
    redact_vendor_tree(tmp_path)

    # Then: behavior-bearing structure remains while identifying values are placeholders.
    assert "<example-organization>" in resolve.read_text(encoding="utf-8")
    assert "<primary-node>" in requirements.read_text(encoding="utf-8")
    redacted = offline.read_text(encoding="utf-8")
    assert "person@example.invalid" in redacted
    assert "<owner-name>" in redacted
    assert "<example-organization>" in redacted
