"""Owner-gated relocation of operational-fact native-memory entries to Obsidian.

A SIBLING package to ``automation.memory_curator`` (kept separate so the curator
stays a pure proposer and its boundary test never has to allow an approval
producer here).  It takes an ``OPS_REFERENCE`` entry the curator classified,
writes it to an Obsidian ops-reference note (RAG-ingested), and — only after a
single owner ✅ that binds the write AND the delete — removes the exact native
entry via the curator's own ``deletion.delete_entry``.  Nothing is deleted
until every gate passes; any failure leaves native memory untouched.
"""
