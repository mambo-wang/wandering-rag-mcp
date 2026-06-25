"""RAG Knowledge Base MCP Server.

A local RAG (Retrieval-Augmented Generation) knowledge base server
that uses zvec for vector storage and Qwen3-Embedding for text embedding.

Expose 6 MCP tools for knowledge management:
  - search: Semantic search across the knowledge base
  - ingest_file: Import a single text file
  - ingest_directory: Batch import a directory of text files
  - list_collections: List all knowledge base collections
  - list_documents: List documents in a collection
  - delete_document: Remove a document from the knowledge base
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Ensure the project root is in the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.chunker import chunk_text, compute_doc_id
from core.vector_store import VectorStore
from core.reranker import RerankerService

# Configure logging to stderr (stdout is reserved for MCP JSON-RPC)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("wandering-rag-mcp")

# Suppress verbose HTTP request logging from libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

# Default text file extensions to process
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

# Binary document extensions that need markitdown conversion
BINARY_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls",
}

# All supported extensions (text + binary)
ALL_EXTENSIONS = DEFAULT_TEXT_EXTENSIONS | BINARY_EXTENSIONS

# Lazy-initialized markitdown converter
_markitdown = None


def _get_markitdown():
    """Get or create the MarkItDown converter singleton."""
    global _markitdown
    if _markitdown is None:
        from markitdown import MarkItDown
        _markitdown = MarkItDown()
    return _markitdown


def _read_file_content(filepath: str) -> tuple[str | None, str | None]:
    """Read file content, handling both text and binary document formats.

    Returns:
        (content, error) tuple. One of them is always None.
        For text files: reads directly with UTF-8.
        For binary docs (.pdf, .docx, .pptx, .xlsx): uses markitdown to convert.
    """
    ext = Path(filepath).suffix.lower()

    if ext in BINARY_EXTENSIONS:
        try:
            md = _get_markitdown()
            result = md.convert(filepath)
            content = result.text_content if result.text_content else ""
            if not content.strip():
                return None, f"Warning: No text extracted from {ext} file: {filepath}"
            return content, None
        except Exception as e:
            return None, f"Error converting {ext} file with markitdown: {e}"
    else:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if not content.strip():
                return None, f"Warning: File is empty: {filepath}"
            return content, None
        except Exception as e:
            return None, f"Error reading file: {e}"

# Parse CLI arguments early (before FastMCP creation)
_parser = argparse.ArgumentParser(
    description="RAG Knowledge Base MCP Server",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  python server.py                          # stdio mode (default, for QoderWork/Claude Desktop)
  python server.py --mode sse               # SSE mode on 127.0.0.1:8000
  python server.py --mode sse --port 9000   # SSE mode on custom port
  python server.py --mode streamable-http --host 0.0.0.0  # Streamable HTTP, bind all interfaces
""",
)
_parser.add_argument(
    "--mode",
    choices=["stdio", "sse", "streamable-http"],
    default=os.getenv("RAG_MCP_MODE", "stdio"),
    help="Transport mode: stdio (default), sse, or streamable-http",
)
_parser.add_argument(
    "--host",
    default=os.getenv("RAG_MCP_HOST", "127.0.0.1"),
    help="Host to bind (default: 127.0.0.1, env: RAG_MCP_HOST)",
)
_parser.add_argument(
    "--port",
    type=int,
    default=int(os.getenv("RAG_MCP_PORT", "8000")),
    help="Port to bind (default: 8000, env: RAG_MCP_PORT)",
)
_args = _parser.parse_args()

# Initialize MCP server
mcp = FastMCP(
    "wandering-rag-mcp",
    instructions=(
        "Local RAG knowledge base for semantic document search. "
        "Supports ingesting text files (md, txt, py, etc.) and binary documents "
        "(pdf, docx, pptx, xlsx) with natural language search."
    ),
    host=_args.host,
    port=_args.port,
)

# Lazy-initialized vector store
_store: VectorStore | None = None


def get_store() -> VectorStore:
    """Get or create the VectorStore singleton."""
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


@mcp.tool()
def search(
    query: str,
    top_k: int = 5,
    collection: str = "default",
    rerank: bool = False,
) -> str:
    """Search the knowledge base for relevant document chunks.

    Use this tool to find information related to the user's question.
    Returns the most relevant text chunks with their source file and relevance score.

    Args:
        query: Natural language search query.
        top_k: Number of results to return (default: 5).
        collection: Knowledge base collection to search (default: "default").
        rerank: If True, use a cross-encoder reranker model to improve
            result relevance. Fetches more candidates from vector search
            then reranks them. Slightly slower but more accurate (default: False).
    """
    store = get_store()

    # When reranking, fetch more candidates for the reranker to choose from
    if rerank:
        fetch_k = max(top_k * 3, 20)
    else:
        fetch_k = top_k

    try:
        results = store.search(query, top_k=fetch_k, collection=collection)
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return "No relevant documents found in the knowledge base."

    # Apply reranking if requested
    if rerank and len(results) > 1:
        reranker = RerankerService()
        results = reranker.rerank(query, results, top_n=top_k)
        score_key = "rerank_score"
    else:
        score_key = "score"

    output_parts = [f"Found {len(results)} relevant chunks:\n"]
    for i, r in enumerate(results, 1):
        score_val = r.get(score_key, r.get("score", 0))
        score_pct = f"{score_val * 100:.1f}%"
        source = r.get("source", "unknown")
        text = r.get("text", "")
        label = "rerank" if rerank else "vector"
        output_parts.append(
            f"--- Result {i} ({label} score: {score_pct}, source: {source}) ---\n{text}\n"
        )

    return "\n".join(output_parts)


@mcp.tool()
def ingest_file(
    filepath: str,
    collection: str = "default",
    chunk_size: int = 500,
) -> str:
    """Import a file into the knowledge base.

    Supports plain text files (md, txt, py, js, etc.) and binary documents
    (pdf, docx, pptx, xlsx). Binary documents are automatically converted
    to text using markitdown before chunking and indexing.

    Args:
        filepath: Absolute or relative path to the file to import.
        collection: Target knowledge base collection (default: "default").
        chunk_size: Maximum characters per chunk (default: 500).
    """
    filepath = os.path.abspath(filepath)

    if not os.path.isfile(filepath):
        return f"Error: File not found: {filepath}"

    content, error = _read_file_content(filepath)
    if error:
        return error

    # Delete existing chunks for this file (idempotent re-import)
    store = get_store()
    store.delete_document(filepath, collection=collection)

    # Chunk the text
    chunks = chunk_text(content, filepath=filepath, chunk_size=chunk_size)
    if not chunks:
        return f"Warning: No chunks could be created from: {filepath}"

    # Insert into vector store
    count = store.ingest_chunks(chunks, collection=collection)
    store.register_document(filepath, chunk_count=count, collection=collection)

    return (
        f"Successfully imported '{filepath}' into collection '{collection}': "
        f"{count} chunks indexed."
    )


@mcp.tool()
def ingest_directory(
    dirpath: str,
    collection: str = "default",
    recursive: bool = True,
    extensions: str = "",
    chunk_size: int = 500,
) -> str:
    """Batch import all files in a directory into the knowledge base.

    Scans the directory for supported files and imports each one.
    Supports both text files and binary documents (pdf, docx, pptx, xlsx).

    Args:
        dirpath: Path to the directory to scan.
        collection: Target knowledge base collection (default: "default").
        recursive: Whether to scan subdirectories (default: True).
        extensions: Comma-separated file extensions to include, e.g. ".md,.txt,.pdf".
            Leave empty to use the default set (text files + pdf/docx/pptx/xlsx).
        chunk_size: Maximum characters per chunk (default: 500).
    """
    dirpath = os.path.abspath(dirpath)

    if not os.path.isdir(dirpath):
        return f"Error: Directory not found: {dirpath}"

    # Parse extensions
    if extensions.strip():
        ext_set = {e.strip().lower() for e in extensions.split(",")}
        # Ensure they start with a dot
        ext_set = {e if e.startswith(".") else f".{e}" for e in ext_set}
    else:
        ext_set = ALL_EXTENSIONS

    # Find files
    files = []
    if recursive:
        for root, dirs, filenames in os.walk(dirpath):
            # Skip hidden directories and common non-text dirs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {
                "node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build",
            }]
            for fname in filenames:
                if Path(fname).suffix.lower() in ext_set:
                    files.append(os.path.join(root, fname))
    else:
        for fname in os.listdir(dirpath):
            fpath = os.path.join(dirpath, fname)
            if os.path.isfile(fpath) and Path(fname).suffix.lower() in ext_set:
                files.append(fpath)

    if not files:
        return f"No matching files found in: {dirpath}"

    # Import each file
    success = 0
    failed = 0
    total_chunks = 0
    store = get_store()

    for fpath in files:
        try:
            content, error = _read_file_content(fpath)
            if error:
                logger.warning(error)
                failed += 1
                continue

            # Delete existing chunks (idempotent)
            store.delete_document(fpath, collection=collection)

            chunks = chunk_text(content, filepath=fpath, chunk_size=chunk_size)
            if chunks:
                count = store.ingest_chunks(chunks, collection=collection)
                store.register_document(fpath, chunk_count=count, collection=collection)
                total_chunks += count
                success += 1
        except Exception as e:
            logger.error(f"Failed to ingest {fpath}: {e}")
            failed += 1

    return (
        f"Batch import complete for '{dirpath}' → collection '{collection}':\n"
        f"  Files processed: {success} succeeded, {failed} failed\n"
        f"  Total chunks indexed: {total_chunks}"
    )


@mcp.tool()
def list_collections() -> str:
    """List all available knowledge base collections.

    Returns the names of all collections that have been created.
    """
    store = get_store()
    collections = store.list_collections()

    if not collections:
        return "No collections found. Use ingest_file or ingest_directory to create one."

    lines = [f"Found {len(collections)} collection(s):\n"]
    for name in collections:
        docs = store.list_documents(collection=name)
        doc_count = len(docs)
        lines.append(f"  - {name} ({doc_count} documents)")

    return "\n".join(lines)


@mcp.tool()
def list_documents(collection: str = "default") -> str:
    """List all documents imported into a knowledge base collection.

    Args:
        collection: Collection name to list (default: "default").
    """
    store = get_store()
    docs = store.list_documents(collection=collection)

    if not docs:
        return f"No documents found in collection '{collection}'."

    lines = [f"Documents in collection '{collection}' ({len(docs)} total):\n"]
    for doc in docs:
        source = doc.get("source", "unknown")
        chunk_count = doc.get("chunk_count", 0)
        lines.append(f"  - {source} ({chunk_count} chunks)")

    return "\n".join(lines)


@mcp.tool()
def delete_document(
    filepath: str,
    collection: str = "default",
) -> str:
    """Delete a document and all its chunks from the knowledge base.

    Args:
        filepath: Path of the document to delete (should match the path used during import).
        collection: Collection name (default: "default").
    """
    store = get_store()
    deleted = store.delete_document(filepath, collection=collection)
    store.unregister_document(filepath, collection=collection)

    if deleted > 0:
        return (
            f"Deleted {deleted} chunk(s) for '{filepath}' "
            f"from collection '{collection}'."
        )
    else:
        return f"No chunks found for '{filepath}' in collection '{collection}'."


def main():
    """Entry point for the MCP server."""
    mode = _args.mode
    if mode == "stdio":
        mcp.run(transport="stdio")
    elif mode == "sse":
        logger.info(
            f"Starting wandering-rag-mcp in SSE mode: "
            f"http://{_args.host}:{_args.port}/sse"
        )
        mcp.run(transport="sse")
    elif mode == "streamable-http":
        logger.info(
            f"Starting wandering-rag-mcp in Streamable HTTP mode: "
            f"http://{_args.host}:{_args.port}/mcp"
        )
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
