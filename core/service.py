"""Shared business logic layer for MCP tools and REST API.

This module owns the VectorStore singleton and exposes structured
operations (returning dicts) that both the MCP tools and the HTTP
API can consume.
"""

import logging
import os
from pathlib import Path

from core.chunker import chunk_text
from core.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ── Singleton ────────────────────────────────────────────────
_store: VectorStore | None = None
_markitdown = None

# Default text file extensions
DEFAULT_TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt",
    ".go", ".rs", ".rb", ".php", ".c", ".cpp", ".h", ".hpp",
    ".css", ".scss", ".html", ".htm", ".xml", ".yaml", ".yml",
    ".json", ".toml", ".ini", ".cfg", ".conf",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".sql", ".r", ".m", ".swift", ".dart", ".lua",
    ".csv", ".tsv", ".env",
}

BINARY_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls",
}

ALL_EXTENSIONS = DEFAULT_TEXT_EXTENSIONS | BINARY_EXTENSIONS


def get_store() -> VectorStore:
    """Get or create the VectorStore singleton."""
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def _get_markitdown():
    """Get or create the MarkItDown converter singleton."""
    global _markitdown
    if _markitdown is None:
        from markitdown import MarkItDown
        _markitdown = MarkItDown()
    return _markitdown


# ── File reading ─────────────────────────────────────────────

def read_file_content(filepath: str) -> tuple[str | None, str | None]:
    """Read file content, handling both text and binary document formats.

    Returns:
        (content, error) tuple. One of them is always None.
    """
    ext = Path(filepath).suffix.lower()

    if ext in BINARY_EXTENSIONS:
        try:
            md = _get_markitdown()
            result = md.convert(filepath)
            content = result.text_content if result.text_content else ""
            if not content.strip():
                return None, f"No text extracted from {ext} file: {filepath}"
            return content, None
        except Exception as e:
            return None, f"Error converting {ext} file: {e}"
    else:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if not content.strip():
                return None, f"File is empty: {filepath}"
            return content, None
        except Exception as e:
            return None, f"Error reading file: {e}"


# ── Operations ───────────────────────────────────────────────

def ingest_file(
    filepath: str,
    collection: str = "default",
    chunk_size: int = 500,
) -> dict:
    """Import a single file into the knowledge base.

    Returns:
        {"status": "ok", "filepath": str, "chunks": int}
        or {"status": "error", "error": str}
    """
    filepath = os.path.abspath(filepath)

    if not os.path.isfile(filepath):
        return {"status": "error", "error": f"File not found: {filepath}"}

    content, error = read_file_content(filepath)
    if error:
        return {"status": "error", "error": error}

    store = get_store()
    # Idempotent: delete existing chunks first
    store.delete_document(filepath, collection=collection)

    chunks = chunk_text(content, filepath=filepath, chunk_size=chunk_size)
    if not chunks:
        return {"status": "error", "error": f"No chunks created from: {filepath}"}

    count = store.ingest_chunks(chunks, collection=collection)
    store.register_document(filepath, chunk_count=count, collection=collection)

    return {"status": "ok", "filepath": filepath, "chunks": count}


def ingest_content(
    content: str,
    filename: str,
    collection: str = "default",
    chunk_size: int = 500,
) -> dict:
    """Import text content (from file upload) into the knowledge base.

    Args:
        content: The text content to ingest.
        filename: Original filename (used for doc_id and source tracking).
        collection: Target collection.
        chunk_size: Max characters per chunk.

    Returns:
        {"status": "ok", "filename": str, "chunks": int}
        or {"status": "error", "error": str}
    """
    if not content or not content.strip():
        return {"status": "error", "error": "Empty content"}

    store = get_store()
    # Use a separate uploads directory to avoid conflicting with zvec collection path
    upload_dir = os.path.join(store.data_dir, "_uploads", collection)
    os.makedirs(upload_dir, exist_ok=True)
    virtual_path = os.path.join(upload_dir, filename)

    # Idempotent: delete existing chunks
    store.delete_document(virtual_path, collection=collection)

    chunks = chunk_text(content, filepath=virtual_path, chunk_size=chunk_size)
    if not chunks:
        return {"status": "error", "error": "No chunks could be created"}

    count = store.ingest_chunks(chunks, collection=collection)
    store.register_document(virtual_path, chunk_count=count, collection=collection)

    return {"status": "ok", "filename": filename, "chunks": count}


def delete_document(
    filepath: str,
    collection: str = "default",
) -> dict:
    """Delete a document and all its chunks.

    Returns:
        {"status": "ok", "filepath": str, "deleted": int}
    """
    store = get_store()
    deleted = store.delete_document(filepath, collection=collection)
    store.unregister_document(filepath, collection=collection)
    return {"status": "ok", "filepath": filepath, "deleted": deleted}


def list_collections() -> list[dict]:
    """List all collections with document counts.

    Returns:
        [{"name": str, "doc_count": int}, ...]
    """
    store = get_store()
    names = store.list_collections()
    result = []
    for name in names:
        docs = store.list_documents(collection=name)
        result.append({"name": name, "doc_count": len(docs)})
    return result


def list_documents(collection: str = "default") -> list[dict]:
    """List all documents in a collection.

    Returns:
        [{"source": str, "chunk_count": int}, ...]
    """
    store = get_store()
    return store.list_documents(collection=collection)


def search(
    query: str,
    top_k: int = 5,
    collection: str = "default",
    rerank: bool = False,
) -> list[dict]:
    """Search the knowledge base.

    Returns:
        List of result dicts with keys:
        id, score, text, source, chunk_index
        (plus rerank_score if rerank=True)
    """
    from core.reranker import RerankerService

    store = get_store()

    # When reranking, fetch more candidates
    fetch_k = max(top_k * 3, 20) if rerank else top_k

    results = store.search(query, top_k=fetch_k, collection=collection)

    if not results:
        return []

    # Apply reranking if requested
    if rerank and len(results) > 1:
        reranker = RerankerService()
        results = reranker.rerank(query, results, top_n=top_k)

    return results
