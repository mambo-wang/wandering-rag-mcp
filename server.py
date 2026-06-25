"""RAG Knowledge Base MCP Server.

A local RAG (Retrieval-Augmented Generation) knowledge base server
that uses zvec for vector storage and Qwen3-Embedding for text embedding.

Exposes 8 MCP tools for knowledge management and (optionally) a REST API
for web frontend integration. Both interfaces share the same vector store.

MCP Tools:
  - search: Semantic search across the knowledge base
  - ingest_file: Import a single file
  - ingest_directory: Batch import a directory of files
  - list_collections: List all knowledge base collections
  - list_documents: List documents in a collection
  - delete_document: Remove a document from the knowledge base
  - configure_collection: Set default parameters for a collection
  - get_collection_config: View a collection's configuration

REST API (enabled by default in SSE / streamable-http modes):
  - GET  /api/health
  - GET  /api/collections
  - GET  /api/collections/{name}/documents
  - POST /api/collections/{name}/documents  (multipart file upload)
  - DELETE /api/collections/{name}/documents
  - POST /api/collections/{name}/search
  - GET  /api/collections/{name}/config
  - PUT  /api/collections/{name}/config
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Ensure the project root is in the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import service

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

# Parse CLI arguments early (before FastMCP creation)
_parser = argparse.ArgumentParser(
    description="RAG Knowledge Base MCP Server",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  python server.py                          # stdio mode (default, for QoderWork/Claude Desktop)
  python server.py --mode sse               # SSE + REST API on 127.0.0.1:8000
  python server.py --mode sse --no-api      # SSE only, no REST API
  python server.py --mode streamable-http --host 0.0.0.0  # Streamable HTTP + REST API
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
_parser.add_argument(
    "--api",
    action=argparse.BooleanOptionalAction,
    default=True,
    help="Enable REST API alongside MCP (default: enabled in SSE/streamable-http modes)",
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


# ── MCP Tools ────────────────────────────────────────────────────────────────


@mcp.tool()
def search(
    query: str,
    top_k: int = 5,
    collection: str = "default",
    rerank: bool | None = None,
    filter: str = "",
) -> str:
    """Search the knowledge base for relevant document chunks.

    Use this tool to find information related to the user's question.
    Returns the most relevant text chunks with their source file and relevance score.

    Args:
        query: Natural language search query.
        top_k: Number of results to return (default: 5).
        collection: Knowledge base collection to search (default: "default").
        rerank: If True, use a cross-encoder reranker model to improve
            result relevance. If not specified, uses the collection config
            default (default: False unless configured otherwise).
        filter: Glob pattern to filter by source file path, e.g. "*.md",
            "**/docs/*", "README*". Leave empty to search all documents (default: "").
    """
    results = service.search(
        query=query, top_k=top_k, collection=collection,
        rerank=rerank, filter=filter,
    )

    if not results:
        if filter:
            return f"No relevant documents found matching '{filter}' in the knowledge base."
        return "No relevant documents found in the knowledge base."

    output_parts = [f"Found {len(results)} relevant chunks:\n"]
    if filter:
        output_parts[0] = f"Found {len(results)} relevant chunks (filter: {filter}):\n"
    for i, r in enumerate(results, 1):
        score_val = r.get("rerank_score", r.get("score", 0))
        score_pct = f"{score_val * 100:.1f}%"
        source = r.get("source", "unknown")
        text = r.get("text", "")
        label = "rerank" if rerank and "rerank_score" in r else "vector"
        output_parts.append(
            f"--- Result {i} ({label} score: {score_pct}, source: {source}) ---\n{text}\n"
        )

    return "\n".join(output_parts)


@mcp.tool()
def ingest_file(
    filepath: str,
    collection: str = "default",
    chunk_size: int = 0,
    force: bool = False,
    chunk_mode: str = "",
) -> str:
    """Import a file into the knowledge base.

    Supports plain text files (md, txt, py, js, etc.) and binary documents
    (pdf, docx, pptx, xlsx). Binary documents are automatically converted
    to text using markitdown before chunking and indexing.

    Args:
        filepath: Absolute or relative path to the file to import.
        collection: Target knowledge base collection (default: "default").
        chunk_size: Maximum characters per chunk. 0 = use collection config
            (default: 500 if not configured).
        force: If True, re-import even if file content hasn't changed
            since last import (default: False).
        chunk_mode: Chunking strategy. Empty = use collection config.
            "recursive" splits by paragraphs, sentences, then characters.
            "semantic" uses the embedding model to detect topic boundaries.
    """
    result = service.ingest_file(
        filepath,
        collection=collection,
        chunk_size=chunk_size if chunk_size > 0 else None,
        force=force,
        chunk_mode=chunk_mode if chunk_mode else None,
    )
    if result["status"] == "skipped":
        return (
            f"Skipped '{result['filepath']}': file unchanged since last import. "
            f"Use force=True to re-import anyway."
        )
    if result["status"] == "error":
        return f"Error: {result['error']}"
    return (
        f"Successfully imported '{result['filepath']}' into collection "
        f"'{collection}': {result['chunks']} chunks indexed."
    )


@mcp.tool()
def ingest_directory(
    dirpath: str,
    collection: str = "default",
    recursive: bool = True,
    extensions: str = "",
    chunk_size: int = 0,
    force: bool = False,
    chunk_mode: str = "",
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
        chunk_size: Maximum characters per chunk. 0 = use collection config
            (default: 500 if not configured).
        force: If True, re-import files even if they haven't changed
            since last import (default: False).
        chunk_mode: Chunking strategy. Empty = use collection config.
            "recursive" splits by paragraphs, sentences, then characters.
            "semantic" uses the embedding model to detect topic boundaries.
    """
    dirpath = os.path.abspath(dirpath)

    if not os.path.isdir(dirpath):
        return f"Error: Directory not found: {dirpath}"

    # Parse extensions
    if extensions.strip():
        ext_set = {e.strip().lower() for e in extensions.split(",")}
        ext_set = {e if e.startswith(".") else f".{e}" for e in ext_set}
    else:
        ext_set = service.ALL_EXTENSIONS

    # Find files
    files = []
    if recursive:
        for root, dirs, filenames in os.walk(dirpath):
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

    # Import each file via service layer (leverages change detection)
    success = 0
    skipped = 0
    failed = 0
    total_chunks = 0

    for fpath in files:
        result = service.ingest_file(
            fpath, collection=collection,
            chunk_size=chunk_size if chunk_size > 0 else None,
            force=force,
            chunk_mode=chunk_mode if chunk_mode else None,
        )
        if result["status"] == "ok":
            total_chunks += result["chunks"]
            success += 1
        elif result["status"] == "skipped":
            skipped += 1
        else:
            logger.warning(result.get("error", f"Unknown error for {fpath}"))
            failed += 1

    return (
        f"Batch import complete for '{dirpath}' \u2192 collection '{collection}':\n"
        f"  Files: {success} imported, {skipped} unchanged (skipped), {failed} failed\n"
        f"  Total chunks indexed: {total_chunks}"
    )


@mcp.tool()
def list_collections() -> str:
    """List all available knowledge base collections.

    Returns the names of all collections that have been created.
    """
    collections = service.list_collections()

    if not collections:
        return "No collections found. Use ingest_file or ingest_directory to create one."

    lines = [f"Found {len(collections)} collection(s):\n"]
    for c in collections:
        desc = c.get("description", "")
        if desc:
            lines.append(f"  - {c['name']} ({c['doc_count']} documents) — {desc}")
        else:
            lines.append(f"  - {c['name']} ({c['doc_count']} documents)")

    return "\n".join(lines)


@mcp.tool()
def list_documents(collection: str = "default") -> str:
    """List all documents imported into a knowledge base collection.

    Args:
        collection: Collection name to list (default: "default").
    """
    docs = service.list_documents(collection=collection)

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
    result = service.delete_document(filepath, collection=collection)
    deleted = result["deleted"]

    if deleted > 0:
        return (
            f"Deleted {deleted} chunk(s) for '{filepath}' "
            f"from collection '{collection}'."
        )
    else:
        return f"No chunks found for '{filepath}' in collection '{collection}'."


@mcp.tool()
def configure_collection(
    collection: str = "default",
    chunk_mode: str = "",
    chunk_size: int = 0,
    chunk_overlap: int = -1,
    rerank: bool | None = None,
    description: str | None = None,
) -> str:
    """Configure default parameters for a knowledge base collection.

    These defaults are used when importing or searching without explicitly
    specifying parameters. For example, setting chunk_mode="semantic" here
    means all future ingest_file calls will use semantic chunking by default.

    Args:
        collection: Collection name (default: "default").
        chunk_mode: Default chunking strategy. Empty = keep current.
            "recursive" or "semantic".
        chunk_size: Default max characters per chunk. 0 = keep current.
        chunk_overlap: Default overlap characters. -1 = keep current.
        rerank: Default whether to use reranker for search.
            None = keep current, True/False to set.
        description: Description of this collection. None = keep current.
    """
    config = service.set_collection_config(
        collection=collection,
        chunk_mode=chunk_mode if chunk_mode else None,
        chunk_size=chunk_size if chunk_size > 0 else None,
        chunk_overlap=chunk_overlap if chunk_overlap >= 0 else None,
        rerank=rerank,
        description=description,
    )
    parts = [f"Collection '{collection}' configured:"]
    parts.append(f"  chunk_mode:    {config['chunk_mode']}")
    parts.append(f"  chunk_size:    {config['chunk_size']}")
    parts.append(f"  chunk_overlap: {config['chunk_overlap']}")
    parts.append(f"  rerank:        {config['rerank']}")
    if config.get("description"):
        parts.append(f"  description:   {config['description']}")
    return "\n".join(parts)


@mcp.tool()
def get_collection_config(collection: str = "default") -> str:
    """View the current configuration for a knowledge base collection.

    Args:
        collection: Collection name (default: "default").
    """
    config = service.get_collection_config(collection)
    parts = [f"Configuration for collection '{collection}':"]
    parts.append(f"  chunk_mode:    {config['chunk_mode']}")
    parts.append(f"  chunk_size:    {config['chunk_size']}")
    parts.append(f"  chunk_overlap: {config['chunk_overlap']}")
    parts.append(f"  rerank:        {config['rerank']}")
    if config.get("description"):
        parts.append(f"  description:   {config['description']}")
    return "\n".join(parts)


# ── Combined ASGI Application ────────────────────────────────────────────────


def _create_combined_app():
    """Create a combined ASGI app with REST API + MCP on the same port.

    Routes:
        /api/*   -> REST API (JSON)
        /mcp     -> MCP Streamable HTTP
        /sse     -> MCP SSE
        /messages/ -> MCP SSE message endpoint
    """
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware

    from api.app import create_api_routes, get_cors_middleware

    # Get the MCP ASGI app (Starlette instance)
    if _args.mode == "sse":
        mcp_app = mcp.sse_app()
    else:
        mcp_app = mcp.streamable_http_app()

    # Build combined route list: API routes first, then MCP routes
    routes = list(create_api_routes()) + list(mcp_app.routes)

    # CORS middleware for web frontend access
    middleware = [get_cors_middleware()]

    # Use MCP app's lifespan (needed for streamable-http session manager)
    combined = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=mcp_app.router.lifespan_context,
    )

    return combined


# ── Entry Point ──────────────────────────────────────────────────────────────


def main():
    """Entry point for the MCP server."""
    mode = _args.mode

    if mode == "stdio":
        mcp.run(transport="stdio")

    elif mode in ("sse", "streamable-http"):
        if _args.api:
            # Combined mode: REST API + MCP on the same port
            import uvicorn

            app = _create_combined_app()

            if mode == "sse":
                logger.info(
                    f"Starting wandering-rag-mcp in SSE mode: "
                    f"http://{_args.host}:{_args.port}/sse"
                )
                logger.info(
                    f"REST API available at: "
                    f"http://{_args.host}:{_args.port}/api/"
                )
            else:
                logger.info(
                    f"Starting wandering-rag-mcp in Streamable HTTP mode: "
                    f"http://{_args.host}:{_args.port}/mcp"
                )
                logger.info(
                    f"REST API available at: "
                    f"http://{_args.host}:{_args.port}/api/"
                )

            uvicorn.run(app, host=_args.host, port=_args.port)
        else:
            # MCP-only mode (no REST API)
            if mode == "sse":
                logger.info(
                    f"Starting wandering-rag-mcp in SSE mode: "
                    f"http://{_args.host}:{_args.port}/sse"
                )
            else:
                logger.info(
                    f"Starting wandering-rag-mcp in Streamable HTTP mode: "
                    f"http://{_args.host}:{_args.port}/mcp"
                )
            mcp.run(transport=mode)


if __name__ == "__main__":
    main()
