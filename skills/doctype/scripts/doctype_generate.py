"""Private document-type draft assembly with Codex-authored narrative sections."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from skills.doctype.scripts import doctype_extract, doctype_llm, doctype_schema, doctype_sensitivity, doctype_store


class GenerationError(RuntimeError):
    """Input, private example, template, or output policy prevents a safe draft."""


@dataclass(frozen=True, slots=True)
class DraftResult:
    """A generated private artifact and the section keys authored by Codex."""

    path: Path
    version: int
    narrative_sections: tuple[str, ...]
    sha256: str


def load_inputs(path: Path) -> dict[str, str]:
    """Parse a JSON object into non-empty string inputs at the CLI trust boundary."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GenerationError("--inputs-json must be a readable JSON object") from error
    if not isinstance(raw, dict):
        raise GenerationError("--inputs-json must be a JSON object")
    inputs: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise GenerationError("inputs must use non-empty string keys and values")
        inputs[key] = value.strip()
    return inputs


def _required_values(metadata: doctype_schema.DocTypeMetadata, inputs: dict[str, str]) -> dict[str, str]:
    missing = tuple(field.name for field in metadata.fields if field.name not in inputs)
    if missing:
        raise GenerationError(f"missing fields: {', '.join(missing)}")
    return {field.name: inputs[field.name] for field in metadata.fields}


def _is_narrative(mode: str, section: doctype_schema.Section) -> bool:
    return section.kind != "slot-fill" and mode != "slot-fill"


def _few_shot(store: doctype_store.DocTypeStore, metadata: doctype_schema.DocTypeMetadata) -> str:
    examples: list[str] = []
    for reference in metadata.examples[-2:]:
        text = doctype_extract.read_document(store.example_path(reference)).text.strip()
        examples.append(text[:6000])
    return "\n\n--- APPROVED PRIVATE EXAMPLE ---\n".join(examples)


def _narrative_prompt(
    metadata: doctype_schema.DocTypeMetadata,
    section: doctype_schema.Section,
    inputs: dict[str, str],
    examples: str,
) -> str:
    context = json.dumps(inputs, ensure_ascii=False, sort_keys=True)
    return (
        "DOCTYPE_STAGE=NARRATIVE\n"
        "Write only the finished Korean prose for this section. Author the reasoning and argumentation; "
        "do not return JSON, labels, or analysis. Do not invent facts beyond INPUTS.\n"
        f"TYPE={metadata.doc_type_name}\nGIST={metadata.gist}\nTONE={metadata.tone}\n"
        f"SECTION={section.title}\nGUIDANCE={section.guidance}\nINPUTS={context}\n"
        f"FEW_SHOT_PRIVATE_EXAMPLES:\n{examples}"
    )


def _render_slots(metadata: doctype_schema.DocTypeMetadata, section: doctype_schema.Section, values: dict[str, str]) -> str:
    items = [field for field in metadata.fields if field.section == section.key]
    return "\n".join(f"- {field.name}: {values[field.name]}" for field in items) or "- 해당 없음"


def _render_markdown(
    metadata: doctype_schema.DocTypeMetadata,
    values: dict[str, str],
    narratives: dict[str, str],
) -> str:
    chunks = [f"# {metadata.doc_type_name} 초안"]
    for section in metadata.sections:
        body = narratives.get(section.key) or _render_slots(metadata, section, values)
        chunks.extend(("", f"## {section.title}", body))
    return "\n".join(chunks).strip() + "\n"


def ensure_private_output(store: doctype_store.DocTypeStore, output: Path) -> Path:
    resolved = output.expanduser().resolve()
    try:
        resolved.relative_to(store.repo_root().resolve())
    except ValueError:
        return resolved
    raise GenerationError("draft output must be outside the repository; use a private runtime path")


def _fill_template(
    store: doctype_store.DocTypeStore,
    metadata: doctype_schema.DocTypeMetadata,
    values: dict[str, str],
    output: Path,
) -> bool:
    reference = metadata.template_ref
    if reference is None:
        return False
    template = store.example_path(reference)
    if reference.format == "docx":
        from skills.procurement.scripts import procure_docx

        procure_docx.fill(template, values, output)
        return True
    if reference.format == "hwpx":
        from skills.procurement.scripts import procure_hwpx

        procure_hwpx.fill(template, values, output)
        return True
    return False


def generate(
    store: doctype_store.DocTypeStore,
    entry: doctype_store.StoredDocType,
    inputs: dict[str, str],
    output: Path,
) -> DraftResult:
    """Gate all private context, author narrative sections via Codex, then write privately."""
    target = ensure_private_output(store, output)
    values = _required_values(entry.metadata, inputs)
    examples = _few_shot(store, entry.metadata)
    gate_text = "\n".join((*values.values(), examples))
    verdict = doctype_sensitivity.evaluate(gate_text, doctype_sensitivity.load_rules(store.paths.rules_file))
    narratives: dict[str, str] = {}
    for section in entry.metadata.sections:
        if _is_narrative(entry.metadata.mode, section):
            prompt = _narrative_prompt(entry.metadata, section, inputs, examples)
            opaque = hashlib.sha256(f"{entry.metadata.id}:{entry.metadata.version}:{section.key}".encode()).hexdigest()[:16]
            narratives[section.key] = doctype_llm.call_codex(
                prompt, purpose="narrative-draft", sensitive=verdict.sensitive, opaque_id=opaque
            )
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _fill_template(store, entry.metadata, values, target):
        payload = target.read_bytes()
    else:
        rendered = _render_markdown(entry.metadata, values, narratives)
        target.write_text(rendered, encoding="utf-8")
        payload = rendered.encode("utf-8")
    target.chmod(0o600)
    return DraftResult(
        path=target,
        version=entry.metadata.version,
        narrative_sections=tuple(narratives),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
