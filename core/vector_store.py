"""Vector store wrapper for zvec embedded vector database."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

import zvec

from core.chunker import Chunk
from core.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

# Default data directory
DEFAULT_DATA_DIR = os.getenv(
    "RAG_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)


class VectorStore:
    """Manages zvec collections for RAG vector storage and retrieval.

    Each collection is stored in a subdirectory under the data directory:
        data/{collection_name}/

    Schema per collection:
        - embedding: VECTOR_FP32 (dimension from embedding model)
        - text: STRING (chunk text content)
        - source: STRING (source file path)
        - chunk_index: INT64 (chunk position in source document)
    """

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR):
        self.data_dir = data_dir
        self.embedding_service = EmbeddingService()
        self._collections: dict[str, zvec.Collection] = {}
        os.makedirs(data_dir, exist_ok=True)

    def _collection_path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    def _get_or_create_collection(self, name: str) -> zvec.Collection:
        """Open an existing collection or create a new one."""
        if name in self._collections:
            return self._collections[name]

        path = self._collection_path(name)

        if os.path.exists(path) and os.path.isdir(path):
            # Check if it's an existing zvec collection
            # zvec stores internal files in the directory
            try:
                coll = zvec.open(path)
                self._collections[name] = coll
                logger.info(f"Opened existing collection: {name}")
                return coll
            except Exception:
                logger.warning(f"Failed to open collection at {path}, will recreate")

        # Create new collection
        dimension = self.embedding_service.dimension
        schema = zvec.CollectionSchema(
            name=name,
            fields=[
                zvec.FieldSchema("text", zvec.DataType.STRING, nullable=True),
                zvec.FieldSchema("source", zvec.DataType.STRING, nullable=True),
                zvec.FieldSchema("chunk_index", zvec.DataType.INT64, nullable=True),
            ],
            vectors=zvec.VectorSchema(
                "embedding",
                zvec.DataType.VECTOR_FP32,
                dimension,
            ),
        )
        coll = zvec.create_and_open(path, schema=schema)
        self._collections[name] = coll
        logger.info(f"Created new collection: {name} (dim={dimension})")
        return coll

    def ingest_chunks(self, chunks: list[Chunk], collection: str = "default") -> int:
        """Insert pre-chunked text into the vector store.

        Args:
            chunks: List of Chunk objects (already split and with metadata).
            collection: Target collection name.

        Returns:
            Number of chunks inserted.
        """
        if not chunks:
            return 0

        coll = self._get_or_create_collection(collection)
        texts = [c.text for c in chunks]
        embeddings = self.embedding_service.encode(texts)

        docs = zvec.DocList([
            zvec.Doc(
                id=f"{chunk.doc_id}_{chunk.chunk_index}",
                vectors={"embedding": emb},
                fields={
                    "text": chunk.text,
                    "source": chunk.source,
                    "chunk_index": chunk.chunk_index,
                },
            )
            for chunk, emb in zip(chunks, embeddings)
        ])

        coll.insert(docs)
        coll.flush()
        logger.info(f"Inserted {len(chunks)} chunks into collection '{collection}'")
        return len(chunks)

    def search(
        self,
        query: str,
        top_k: int = 5,
        collection: str = "default",
    ) -> list[dict]:
        """Semantic search across the knowledge base.

        Args:
            query: Search query string.
            top_k: Number of results to return.
            collection: Collection to search in.

        Returns:
            List of result dicts with keys: id, score, text, source, chunk_index.
        """
        coll = self._get_or_create_collection(collection)
        query_vec = self.embedding_service.encode_query(query)

        results = coll.query(
            zvec.Query("embedding", vector=query_vec),
            topk=top_k,
        )

        return [
            {
                "id": r.id,
                "score": r.score,
                "text": r.fields.get("text", "") if hasattr(r.fields, 'get') else str(r.fields.get("text", "")),
                "source": r.fields.get("source", "") if hasattr(r.fields, 'get') else str(r.fields.get("source", "")),
                "chunk_index": r.fields.get("chunk_index", 0) if hasattr(r.fields, 'get') else 0,
            }
            for r in results
        ]

    def delete_document(
        self,
        filepath: str,
        collection: str = "default",
    ) -> int:
        """Delete all chunks belonging to a specific document.

        Uses the doc_id prefix (derived from file path hash) to identify
        all chunks belonging to the document.

        Args:
            filepath: Path of the document to delete.
            collection: Collection name.

        Returns:
            Number of chunks deleted.
        """
        from core.chunker import compute_doc_id

        coll = self._get_or_create_collection(collection)
        doc_id = compute_doc_id(filepath)

        # We need to find all chunk IDs belonging to this document.
        # Strategy: search with a dummy query to get all results,
        # then filter by source field matching the filepath.
        # Alternative: try to delete by known ID pattern.
        deleted = 0
        # Try deleting chunks by ID pattern: {doc_id}_{index}
        # We try a reasonable range of chunk indices (0..9999)
        ids_to_delete = []
        for i in range(10000):
            chunk_id = f"{doc_id}_{i}"
            try:
                fetched = coll.fetch([chunk_id])
                if chunk_id in fetched and fetched[chunk_id] is not None:
                    ids_to_delete.append(chunk_id)
                else:
                    # Once we hit a gap, no more chunks
                    if i > 0:
                        break
            except Exception:
                break

        if ids_to_delete:
            coll.delete(ids_to_delete)
            coll.flush()
            deleted = len(ids_to_delete)
            logger.info(
                f"Deleted {deleted} chunks for document '{filepath}' "
                f"from collection '{collection}'"
            )
        return deleted

    def list_collections(self) -> list[str]:
        """List all available collection names."""
        if not os.path.exists(self.data_dir):
            return []
        collections = []
        for entry in sorted(os.listdir(self.data_dir)):
            path = os.path.join(self.data_dir, entry)
            if os.path.isdir(path):
                collections.append(entry)
        return collections

    def list_documents(self, collection: str = "default") -> list[dict]:
        """List all documents in a collection with chunk counts.

        Returns:
            List of dicts with keys: source, chunk_count.
        """
        coll = self._get_or_create_collection(collection)

        # Use the collection stats to get total count
        stats = coll.stats
        total_docs = stats.num_docs if hasattr(stats, 'num_docs') else 0

        # To get per-document info, we do a broad search
        # This is a limitation of the embedded approach
        doc_map: dict[str, int] = {}

        # Fetch all documents by trying common doc_id patterns
        # For a more robust approach, we maintain a document registry file
        registry_path = os.path.join(self._collection_path(collection), "_registry.json")
        if os.path.exists(registry_path):
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
            return [
                {"source": path, "chunk_count": info.get("chunk_count", 0)}
                for path, info in sorted(registry.items())
            ]

        return []

    def register_document(
        self,
        filepath: str,
        chunk_count: int,
        collection: str = "default",
    ):
        """Register a document in the collection's document registry."""
        coll_path = self._collection_path(collection)
        os.makedirs(coll_path, exist_ok=True)
        registry_path = os.path.join(coll_path, "_registry.json")

        registry = {}
        if os.path.exists(registry_path):
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)

        abs_path = os.path.normpath(os.path.abspath(filepath))
        registry[abs_path] = {
            "chunk_count": chunk_count,
            "doc_id": __import__("core.chunker", fromlist=["compute_doc_id"]).compute_doc_id(filepath),
        }

        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)

    def unregister_document(self, filepath: str, collection: str = "default"):
        """Remove a document from the collection's registry."""
        coll_path = self._collection_path(collection)
        registry_path = os.path.join(coll_path, "_registry.json")

        if not os.path.exists(registry_path):
            return

        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)

        abs_path = os.path.normpath(os.path.abspath(filepath))
        registry.pop(abs_path, None)

        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
