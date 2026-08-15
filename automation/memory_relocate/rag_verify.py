"""Read-only verification gate for RAG ingest state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from automation.rag_ingest.chunking import chunk_markdown, parse_frontmatter
from automation.rag_ingest.hashing import doc_fingerprint


@dataclass(frozen=True, slots=True)
class RagVerdict:
    ingested: bool
    reason: str
    source_key: str
    fingerprint: str | None


def rag_source_key(note_relpath: str) -> str:
    return f"obsidian:{note_relpath}"


def verify_ingested(state_path: Path, note_relpath: str, note_body: str) -> RagVerdict:
    """Verify that a note has been ingested into RAG by checking the state file.
    
    This is a read-only operation. It never modifies the state file.
    """
    source_key = rag_source_key(note_relpath)
    
    if not state_path.exists():
        return RagVerdict(False, "state_missing", source_key, None)
        
    try:
        state_raw: object = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return RagVerdict(False, "state_missing", source_key, None)
        
    if not isinstance(state_raw, dict) or "documents" not in state_raw:
        return RagVerdict(False, "state_missing", source_key, None)
    state = cast(dict[str, object], state_raw)
        
    documents = state["documents"]
    if not isinstance(documents, dict):
        return RagVerdict(False, "state_missing", source_key, None)
    documents_dict = cast(dict[str, object], documents)
        
    entry = documents_dict.get(source_key)
    if not isinstance(entry, dict):
        return RagVerdict(False, "source_absent", source_key, None)
    entry_dict = cast(dict[str, object], entry)
        
    stored_fp = entry_dict.get("fingerprint")
    if not isinstance(stored_fp, str):
        stored_fp = None
        
    point_ids = entry_dict.get("point_ids")
    if not isinstance(point_ids, list) or not point_ids:
        return RagVerdict(False, "no_points", source_key, stored_fp)
    if stored_fp is None:
        return RagVerdict(False, "fingerprint_mismatch", source_key, None)
        
    # Recompute fingerprint
    _, body = parse_frontmatter(note_body)
    chunk_texts = chunk_markdown(body, 1500)
    
    chunk_sources_and_contents = [
        (f"{source_key}#c{index:04d}", text)
        for index, text in enumerate(chunk_texts)
    ]
    recomputed_fp = doc_fingerprint(chunk_sources_and_contents)
    
    if stored_fp != recomputed_fp:
        return RagVerdict(False, "fingerprint_mismatch", source_key, stored_fp)
        
    return RagVerdict(True, "", source_key, stored_fp)
