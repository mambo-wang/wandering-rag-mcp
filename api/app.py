"""REST API for wandering-rag-mcp knowledge base management.

Provides HTTP endpoints for document CRUD and semantic search,
designed to be called from web frontends (e.g. CodingHub).
Uses starlette directly — no FastAPI dependency needed.
"""

import logging
import os

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from core import service

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────

def _json(data, status_code: int = 200):
    return JSONResponse(content=data, status_code=status_code)


def _error(message: str, status_code: int = 400):
    return JSONResponse(content={"error": message}, status_code=status_code)


def _get_collection(request: Request) -> str:
    return request.path_params.get("name", "default")


# ── Route Handlers ───────────────────────────────────────────

async def health(request: Request):
    """GET /api/health — health check."""
    return _json({"status": "ok", "service": "wandering-rag-mcp"})


async def list_collections(request: Request):
    """GET /api/collections — list all collections."""
    try:
        result = service.list_collections()
        return _json(result)
    except Exception as e:
        logger.error(f"list_collections failed: {e}")
        return _error(str(e), 500)


async def list_documents(request: Request):
    """GET /api/collections/{name}/documents — list documents."""
    collection = _get_collection(request)
    try:
        result = service.list_documents(collection=collection)
        return _json(result)
    except Exception as e:
        logger.error(f"list_documents failed: {e}")
        return _error(str(e), 500)


async def upload_document(request: Request):
    """POST /api/collections/{name}/documents — upload a file.

    Accepts multipart/form-data with a 'file' field.
    Optional query params:
      chunk_size (int, default 500) - max characters per chunk.
      chunk_mode (str, default "recursive") - "recursive" or "semantic".
    """
    collection = _get_collection(request)
    chunk_size_raw = request.query_params.get("chunk_size")
    chunk_mode_raw = request.query_params.get("chunk_mode")
    chunk_size = int(chunk_size_raw) if chunk_size_raw else None
    chunk_mode = chunk_mode_raw if chunk_mode_raw else None

    try:
        form = await request.form()
    except Exception:
        return _error("Invalid multipart form data. Did you forget python-multipart?", 400)

    upload = form.get("file")
    if upload is None:
        return _error("Missing 'file' field in multipart form", 400)

    filename = upload.filename or "unnamed"

    # Read file content
    try:
        raw = await upload.read()
    except Exception as e:
        return _error(f"Failed to read uploaded file: {e}", 500)

    # Determine content type and process
    ext = os.path.splitext(filename)[1].lower()

    if ext in service.BINARY_EXTENSIONS:
        # Binary file: save temporarily, convert with markitdown
        store = service.get_store()
        upload_dir = os.path.join(store.data_dir, "_uploads", collection)
        os.makedirs(upload_dir, exist_ok=True)
        tmp_path = os.path.join(upload_dir, filename)

        with open(tmp_path, "wb") as f:
            f.write(raw)

        result = service.ingest_file(tmp_path, collection=collection,
                                      chunk_size=chunk_size, chunk_mode=chunk_mode)
    else:
        # Text file: decode and ingest directly
        try:
            content = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return _error(f"Failed to decode file as UTF-8: {e}", 400)

        result = service.ingest_content(
            content, filename=filename,
            collection=collection, chunk_size=chunk_size,
            chunk_mode=chunk_mode,
        )

    if result.get("status") == "error":
        return _error(result["error"], 422)

    return _json(result, 201)


async def delete_document(request: Request):
    """DELETE /api/collections/{name}/documents — delete a document.

    Expects JSON body: {"filepath": "..."}
    """
    collection = _get_collection(request)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body", 400)

    filepath = body.get("filepath")
    if not filepath:
        return _error("Missing 'filepath' in request body", 400)

    try:
        result = service.delete_document(filepath, collection=collection)
        return _json(result)
    except Exception as e:
        logger.error(f"delete_document failed: {e}")
        return _error(str(e), 500)


async def delete_collection(request: Request):
    """DELETE /api/collections/{name} — delete an entire collection.

    Permanently removes the collection including all documents,
    vectors, and configuration.
    """
    collection = _get_collection(request)

    try:
        result = service.delete_collection(collection)
        if result.get("status") == "error":
            return _error(result["error"], 404)
        return _json(result)
    except Exception as e:
        logger.error(f"delete_collection failed: {e}")
        return _error(str(e), 500)


async def search_documents(request: Request):
    """POST /api/collections/{name}/search — semantic search.

    Expects JSON body: {"query": "...", "top_k": 5, "rerank": false,
                        "filter": "*.md", "expand_context": 0}
    Fields not included in the body use the collection config default.
    """
    collection = _get_collection(request)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body", 400)

    query = body.get("query")
    if not query:
        return _error("Missing 'query' in request body", 400)

    top_k = int(body.get("top_k", 5))
    rerank_val = body.get("rerank")
    rerank = bool(rerank_val) if rerank_val is not None else None
    filter_pattern = body.get("filter", "")
    expand_context = int(body.get("expand_context", 0))

    try:
        results = service.search(
            query=query, top_k=top_k,
            collection=collection, rerank=rerank,
            filter=filter_pattern,
            expand_context=expand_context,
        )
        return _json(results)
    except Exception as e:
        logger.error(f"search failed: {e}")
        return _error(str(e), 500)


# ── Collection Config Endpoints ──────────────────────────────

async def get_config(request: Request):
    """GET /api/collections/{name}/config — get collection configuration."""
    collection = _get_collection(request)
    try:
        config = service.get_collection_config(collection)
        return _json(config)
    except Exception as e:
        logger.error(f"get_config failed: {e}")
        return _error(str(e), 500)


async def update_config(request: Request):
    """PUT /api/collections/{name}/config — update collection configuration.

    Expects JSON body with optional fields:
    {"chunk_mode": "semantic", "chunk_size": 500, "chunk_overlap": 50,
     "rerank": true, "description": "My knowledge base"}
    Only included fields are updated; omitted fields keep their current value.
    """
    collection = _get_collection(request)

    try:
        body = await request.json()
    except Exception:
        return _error("Invalid JSON body", 400)

    try:
        config = service.set_collection_config(
            collection=collection,
            chunk_mode=body.get("chunk_mode"),
            chunk_size=body.get("chunk_size"),
            chunk_overlap=body.get("chunk_overlap"),
            rerank=body.get("rerank"),
            description=body.get("description"),
        )
        return _json(config)
    except Exception as e:
        logger.error(f"update_config failed: {e}")
        return _error(str(e), 500)


# ── Router ───────────────────────────────────────────────────

def create_api_routes() -> list[Route]:
    """Create and return the list of API routes."""
    return [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/collections", list_collections, methods=["GET"]),
        Route("/api/collections/{name}/documents", list_documents, methods=["GET"]),
        Route("/api/collections/{name}/documents", upload_document, methods=["POST"]),
        Route("/api/collections/{name}/documents", delete_document, methods=["DELETE"]),
        Route("/api/collections/{name}", delete_collection, methods=["DELETE"]),
        Route("/api/collections/{name}/search", search_documents, methods=["POST"]),
        Route("/api/collections/{name}/config", get_config, methods=["GET"]),
        Route("/api/collections/{name}/config", update_config, methods=["PUT"]),
    ]


def get_cors_middleware() -> Middleware:
    """Return CORS middleware configured for web frontend access."""
    allowed_origins = os.getenv("RAG_CORS_ORIGINS", "*")
    origins = [o.strip() for o in allowed_origins.split(",")]

    return Middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        allow_credentials=True,
    )
