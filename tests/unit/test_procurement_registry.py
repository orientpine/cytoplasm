"""Registry and XML-first generation regressions for procurement forms."""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from skills.procurement.scripts import make_fixtures
from skills.procurement.scripts import procure_core as core
from skills.procurement.scripts import procure_docx
from skills.procurement.scripts import procure_generate as generate
from skills.procurement.scripts import procure_registry as registry


@pytest.fixture()
def field_values() -> dict[str, str]:
    return {"품목": "합성 시약", "금액": "123,000원", "업체": "테스트 공급사", "일자": "2026-07-17"}


def _normalized_zip_bytes(path: Path) -> bytes:
    """Normalize ZIP timestamps before comparing deterministic HWPX outputs."""
    payload = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(payload, "w") as target:
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, source.read(name))
    return payload.getvalue()


def _root_namespaces(xml: bytes) -> set[bytes]:
    root = re.search(rb"<w:document\b[^>]*>", xml)
    assert root is not None
    return set(re.findall(rb"xmlns(?::[A-Za-z_][\w.-]*)?=['\"][^'\"]+['\"]", root.group()))


def test_hwpx_registered_form_reuses_semantic_map_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field_values: dict[str, str]
) -> None:
    # Given: an empty-slot HWPX form registered once in an isolated runtime store.
    template = tmp_path / "form.hwpx"
    make_fixtures._hwpx_form(template)
    monkeypatch.setenv("PROCURE_TEMPLATE_DIR", str(tmp_path / "templates"))
    record = registry.register("po_form", template)

    # When: the stored template is filled twice using the saved form map.
    preflight = core.detect_format(record.template.name, record.template.read_bytes())
    first, second = tmp_path / "first.hwpx", tmp_path / "second.hwpx"
    generate.generate(preflight, record.template, field_values, first, form_map=record.analysis)
    generate.generate(preflight, record.template, field_values, second, form_map=record.analysis)

    # Then: both output documents read back and normalized archives are identical.
    assert generate.verify(preflight, first, field_values) == sorted(field_values)
    assert _normalized_zip_bytes(first) == _normalized_zip_bytes(second)
    saved = json.loads(record.analysis.read_text(encoding="utf-8"))
    assert [slot["slot_id"] for slot in saved["slots"]] == list(field_values)


def test_docx_xml_method_replaces_placeholder_split_between_runs(
    tmp_path: Path, field_values: dict[str, str]
) -> None:
    # Given: a DOCX fixture deliberately splitting {{품목}} over same-format runs.
    template, output = tmp_path / "split.docx", tmp_path / "filled.docx"
    make_fixtures._docx(template)
    preflight = core.detect_format(template.name, template.read_bytes())

    # When: XML-first generation coalesces the runs before replacement.
    generate.generate(preflight, template, field_values, output)

    # Then: every value is readable and no split placeholder remains.
    assert generate.verify(preflight, output, field_values) == sorted(field_values)
    with zipfile.ZipFile(output) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "{{" not in xml and "합성 시약" in xml


def test_docx_preserves_multinamespace_prefixes_and_declaration(tmp_path: Path) -> None:
    # Given: an XML template whose active Word namespaces have exact original prefixes.
    template, output = tmp_path / "multi.docx", tmp_path / "filled.docx"
    declaration = b"<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
    document = (
        declaration + b"\n"
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        b'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        b'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        b'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
        b'mc:Ignorable="w14 wp14">'
        b'<w:body><w:p><w:hyperlink r:id="rId1"><w:r><w:t>{{field}}</w:t>'
        b'</w:r></w:hyperlink></w:p><w:sectPr/></w:body></w:document>'
    )
    with zipfile.ZipFile(template, "w") as archive:
        archive.writestr("word/document.xml", document)

    # When: XML-first filling serializes the parsed tree.
    procure_docx.fill(template, {"field": "preserved value"}, output)

    # Then: every original root declaration survives even when only mc:Ignorable refers to it.
    with zipfile.ZipFile(output) as archive:
        filled = archive.read("word/document.xml")
    assert filled.startswith(declaration)
    assert b"ns0:" not in filled and b"ns1:" not in filled
    assert _root_namespaces(document) <= _root_namespaces(filled)
    assert b'mc:Ignorable="w14 wp14"' in filled
    assert b"preserved value" in filled and b"{{field}}" not in filled


def test_registry_rejects_duplicate_name_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a registered DOCX template.
    template = tmp_path / "form.docx"
    make_fixtures._docx(template)
    monkeypatch.setenv("PROCURE_TEMPLATE_DIR", str(tmp_path / "templates"))
    registry.register("purchase_doc", template)

    # When / Then: a second registration requires explicit replacement intent.
    with pytest.raises(registry.RegistryError):
        registry.register("purchase_doc", template)


def test_registry_reuses_preflight_to_refuse_binary_hwp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a binary HWP payload sent to the private registry boundary.
    template = tmp_path / "legacy.hwp"
    make_fixtures._hwp_stub(template)
    monkeypatch.setenv("PROCURE_TEMPLATE_DIR", str(tmp_path / "templates"))

    # When / Then: registration returns the established conversion-request error.
    with pytest.raises(core.UnsupportedTemplate) as error:
        registry.register("legacy", template)
    assert "CONVERSION-REQUEST" in error.value.conversion_request
