"""W2-4 personal RAG ingest pipeline.

Ingests personal + team knowledge into the ``personal_cha`` vector collection
through the W2-1 MCP memory server. Embedding happens ONLY inside the RAG node
(the configured RAG node, local bge-m3); this package never calls any embedding API itself.

Pure logic (chunking, hashing, dedup state, queue, metadata) lives here and is
unit-tested in ``tests/unit/``. Deployment target is the ``agent`` account
(``~/.hermes/rag_ingest_runtime/rag_ingest``) driven by a Hermes cron
``no_agent`` script.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
