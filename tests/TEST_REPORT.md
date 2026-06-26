## wandering-rag-mcp Test Report

**Date**: 2026-06-26
**Commit**: 47e8e86 (feat: add structural chunking mode and collection deletion)
**Environment**: Windows 10, Python 3.x, CPU-only (no CUDA)
**Result**: 33 / 33 passed (100.0%)

---

### 1. Structural Chunking (15 tests)

| # | Test | Result | Description |
|---|------|--------|-------------|
| 1.1 | Parse headings | PASS | `_parse_structural_blocks` correctly identifies Markdown `#` headings |
| 1.1b | Heading count | PASS | 3 headings (`#`, `##`, `##`) parsed from basic Markdown |
| 1.2 | Code block detected | PASS | Fenced code block (` ``` `) recognised as a separate block |
| 1.2b | Code content preserved | PASS | `def hello():` and body retained within the code block |
| 1.3 | Table detected | PASS | Markdown table (`\|...\|`) parsed as a table block |
| 1.4 | Structural chunks created | PASS | `structural_chunk_text()` returns non-empty chunk list |
| 1.4b | Heading prefix in chunks | PASS | At least one chunk contains `#` heading as context prefix |
| 1.4c | Sequential indices | PASS | `chunk_index` values are 0, 1, 2, ... with no gaps |
| 1.5 | Sub-heading prefix propagated | PASS | `## Sub Section` headings appear as chunk prefixes |
| 1.6 | Oversized section split | PASS | Long paragraph (>1500 chars) split into >2 chunks with chunk_size=200 |
| 1.6b | No chunk over limit | PASS | No chunk exceeds 250 chars when limit is 200 |
| 1.7 | Empty input | PASS | Empty string returns 0 chunks |
| 1.7b | Whitespace only | PASS | Whitespace-only string returns 0 chunks |
| 1.8 | Plain text fallback | PASS | Plain text without Markdown structure still produces valid chunks |
| 1.9 | Tilde fence detected | PASS | `~~~` fence (alternative syntax) correctly parsed |

### 2. Collection Deletion (8 tests)

| # | Test | Result | Description |
|---|------|--------|-------------|
| 2.1 | Collection dir exists | PASS | Pre-created test collection directory verified |
| 2.2 | Delete returns deleted=True | PASS | `VectorStore.delete_collection()` returns `{"deleted": True}` |
| 2.3 | Directory removed | PASS | `os.path.exists()` confirms directory deleted from disk |
| 2.4 | Delete non-existent | PASS | Returns `{"deleted": False}` for missing collection |
| 2.5 | Cache cleared | PASS | Collection evicted from `_collections` in-memory cache |
| 2.6 | Service delete_collection | PASS | `service.delete_collection()` returns `{"status": "ok"}` |
| 2.7 | Service delete missing | PASS | Returns `{"status": "error"}` for non-existent collection |
| 2.8 | Service delete empty name | PASS | Returns `{"status": "error"}` for empty string |

### 3. Embedding Prefix (2 tests)

| # | Test | Result | Description |
|---|------|--------|-------------|
| 3.1 | encode_query has prefix | PASS | Source code contains `"query: "` prefix in `encode_query()` |
| 3.2 | encode has no prefix | PASS | `encode()` (document encoding) does not add any prefix |

### 4. Context Expansion (5 tests)

| # | Test | Result | Description |
|---|------|--------|-------------|
| 4.1 | fetch_neighbors exists | PASS | `VectorStore` class has `fetch_neighbors` method |
| 4.2 | search param exists | PASS | `service.search()` signature includes `expand_context` |
| 4.3 | Default is 0 | PASS | `expand_context` defaults to 0 (no expansion) |
| 4.4 | fetch_neighbors signature | PASS | Method accepts `source`, `chunk_index`, `doc_id`, `n_before`, `n_after` |
| 4.5 | Graceful on empty | PASS | Returns empty list when collection has no data (no crash) |

### 5. Configuration (3 tests)

| # | Test | Result | Description |
|---|------|--------|-------------|
| 5.1 | Default chunk_mode | PASS | `DEFAULT_COLLECTION_CONFIG["chunk_mode"]` is `"recursive"` |
| 5.2 | ingest_file handles structural | PASS | `ingest_file` source contains `"structural"` branch |
| 5.3 | ingest_content handles structural | PASS | `ingest_content` source contains `"structural"` branch |

---

### Coverage Notes

The tests cover unit-level logic without requiring a running embedding model or zvec instance. The following scenarios were tested at the source-code inspection level (static analysis) rather than runtime:

- Embedding prefix: verified via `inspect.getsource()` since the model takes ~30s to load
- Context expansion: `fetch_neighbors` tested against an empty collection (no stored vectors)

Future integration tests should cover:

- End-to-end structural chunking with real Markdown documents
- Context expansion with actual stored vectors (ingest -> search with `expand_context=1`)
- `delete_collection` with real zvec data (ingest -> verify vectors -> delete -> verify gone)
- MCP tool and REST API endpoint testing via the running server

### Test Script

Located at `tests/test_new_features.py`. Run with:

```bash
python tests/test_new_features.py
```
