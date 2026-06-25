"""Shared business logic layer for MCP tools and REST API.

This module owns the VectorStore singleton and exposes structured
operations (returning dicts) that both the MCP tools and the HTTP
API can consume.
"""

import fnmatch
import hashlib
import logging
import os
from pathlib import Path

from core.chunker import chunk_text, semantic_chunk_text
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


# ── File hashing ─────────────────────────────────────────────

def _compute_file_hash(filepath: str) -> str:
    """Compute SHA256 hash of file content for change detection."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_registry_hash(filepath: str, collection: str) -> str | None:
    """Read the stored file_hash from the registry, or None if not found."""
    store = get_store()
    registry_path = os.path.join(store._collection_path(collection), "_registry.json")
    if not os.path.exists(registry_path):
        return None
    try:
        import json
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        abs_path = os.path.normpath(os.path.abspath(filepath))
        return registry.get(abs_path, {}).get("file_hash")
    except Exception:
        return None


# ── Operations ───────────────────────────────────────────────

def ingest_file(
    filepath: str,
    collection: str = "default",
    chunk_size: int = 500,
    force: bool = False,
    chunk_mode: str = "recursive",
) -> dict:
    """Import a single file into the knowledge base.

    Args:
        filepath: Path to the file.
        collection: Target collection.
        chunk_size: Max characters per chunk.
        force: If True, re-import even if file hasn't changed.
        chunk_mode: Chunking strategy - "recursive" (character-based) or
            "semantic" (embedding similarity-based).

    Returns:
        {"status": "ok", "filepath": str, "chunks": int}
        {"status": "skipped", "filepath": str, "reason": "unchanged"}
        or {"status": "error", "error": str}
    """
    filepath = os.path.abspath(filepath)

    if not os.path.isfile(filepath):
        return {"status": "error", "error": f"File not found: {filepath}"}

    # Change detection: skip if file hasn't changed
    current_hash = _compute_file_hash(filepath)
    if not force:
        stored_hash = _get_registry_hash(filepath, collection)
        if stored_hash and stored_hash == current_hash:
            logger.info(f"Skipping unchanged file: {filepath}")
            return {"status": "skipped", "filepath": filepath, "reason": "unchanged"}

    content, error = read_file_content(filepath)
    if error:
        return {"status": "error", "error": error}

    store = get_store()
    # Idempotent: delete existing chunks first
    store.delete_document(filepath, collection=collection)

    if chunk_mode == "semantic":
        chunks = semantic_chunk_text(content, filepath=filepath,
                                     chunk_size=chunk_size)
    else:
        chunks = chunk_text(content, filepath=filepath, chunk_size=chunk_size)
    if not chunks:
        return {"status": "error", "error": f"No chunks created from: {filepath}"}

    count = store.ingest_chunks(chunks, collection=collection)
    store.register_document(filepath, chunk_count=count, collection=collection,
                            file_hash=current_hash)

    return {"status": "ok", "filepath": filepath, "chunks": count}


def ingest_content(
    content: str,
    filename: str,
    collection: str = "default",
    chunk_size: int = 500,
    chunk_mode: str = "recursive",
) -> dict:
    """Import text content (from file upload) into the knowledge base.

    Args:
        content: The text content to ingest.
        filename: Original filename (used for doc_id and source tracking).
        collection: Target collection.
        chunk_size: Max characters per chunk.
        chunk_mode: Chunking strategy - "recursive" or "semantic".

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

    if chunk_mode == "semantic":
        chunks = semantic_chunk_text(content, filepath=virtual_path,
                                     chunk_size=chunk_size)
    else:
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
    filter: str = "",
) -> list[dict]:
    """Search the knowledge base.

    Args:
        query: Search query string.
        top_k: Number of results to return.
        collection: Collection to search.
        rerank: Whether to apply cross-encoder reranking.
        filter: Glob pattern to filter by source file path (e.g. "*.md", "**/docs/*").
            Empty string means no filtering.

    Returns:
        List of result dicts with keys:
        id, score, text, source, chunk_index
        (plus rerank_score if rerank=True)
    """
    from core.reranker import RerankerService

    store = get_store()

    # Fetch more candidates when filtering or reranking
    if filter or rerank:
        fetch_k = max(top_k * 5, 20)
    else:
        fetch_k = top_k

    results = store.search(query, top_k=fetch_k, collection=collection)

    if not results:
        return []

    # Apply source path filter (glob pattern)
    if filter:
        results = [
            r for r in results
            if fnmatch.fnmatch(r.get("source", ""), filter)
        ]

    # Apply reranking if requested
    if rerank and len(results) > 1:
        reranker = RerankerService()
        results = reranker.rerank(query, results, top_n=top_k)
    else:
        results = results[:top_k]

    return results
