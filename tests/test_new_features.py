"""Test script for new features: structural chunking, collection deletion,
embedding prefix, and context expansion.

Runs unit tests directly without needing the full server running.
"""

import os
import sys
import json
import shutil
import tempfile
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = []
PASS_COUNT = 0
FAIL_COUNT = 0


def test(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    status = "PASS" if condition else "FAIL"
    if condition:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    msg = f"[{status}] {name}"
    if detail and not condition:
        msg += f" — {detail}"
    RESULTS.append(msg)
    print(msg)
    return condition


# ══════════════════════════════════════════════════════════════
# 1. STRUCTURAL CHUNKING
# ══════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("1. STRUCTURAL CHUNKING TESTS")
print("=" * 60)

from core.chunker import structural_chunk_text, _parse_structural_blocks


# 1.1 Basic heading parsing
md_basic = """# Introduction

This is the intro paragraph.

## Section One

Content of section one.

## Section Two

Content of section two with more detail.
"""

blocks = _parse_structural_blocks(md_basic)
kinds = [b["kind"] for b in blocks]
test("1.1 Parse headings", "heading" in kinds, f"kinds={kinds}")

# Count heading blocks
heading_blocks = [b for b in blocks if b["kind"] == "heading"]
test("1.1b Heading count", len(heading_blocks) == 3,
     f"expected 3, got {len(heading_blocks)}")


# 1.2 Code block detection
md_code = """# Setup

Here is the setup code:

```python
def hello():
    print("hello world")
    return True
```

After the code block.
"""

blocks = _parse_structural_blocks(md_code)
code_blocks = [b for b in blocks if b["body"].startswith("```")]
test("1.2 Code block detected", len(code_blocks) >= 1,
     f"code blocks: {len(code_blocks)}")

# Verify code block content is preserved
if code_blocks:
    code_body = code_blocks[0]["body"]
    test("1.2b Code content preserved", "def hello():" in code_body,
         f"body preview: {code_body[:80]}")


# 1.3 Table detection
md_table = """# Data

Some intro text.

| Name  | Age | City     |
|-------|-----|----------|
| Alice | 30  | Beijing  |
| Bob   | 25  | Shanghai |

More text after the table.
"""

blocks = _parse_structural_blocks(md_table)
table_blocks = [b for b in blocks if "|" in b["body"] and b["kind"] == "text"]
test("1.3 Table detected", len(table_blocks) >= 1,
     f"table blocks: {len(table_blocks)}")


# 1.4 Full structural chunking
chunks = structural_chunk_text(md_basic, filepath="test.md", chunk_size=500)
test("1.4 Structural chunks created", len(chunks) > 0,
     f"got {len(chunks)} chunks")

# Verify chunks have heading context
has_heading_prefix = any("# " in c.text for c in chunks)
test("1.4b Heading prefix in chunks", has_heading_prefix,
     f"chunk texts: {[c.text[:60] for c in chunks]}")

# Verify chunk indices are sequential
indices = [c.chunk_index for c in chunks]
test("1.4c Sequential indices", indices == list(range(len(chunks))),
     f"indices: {indices}")


# 1.5 Heading prefix propagation
md_prefix = """# Main Title

Short intro.

## Sub Section A

Content A is here.

## Sub Section B

Content B is here.
"""

chunks = structural_chunk_text(md_prefix, filepath="prefix.md", chunk_size=200)
# At least one chunk should contain "## Sub Section"
has_sub = any("## Sub Section" in c.text for c in chunks)
test("1.5 Sub-heading prefix propagated", has_sub,
     f"chunk texts: {[c.text[:80] for c in chunks]}")


# 1.6 Oversized section falls back to recursive split
long_paragraph = "This is a sentence about testing. " * 50
md_oversized = f"""# Long Section

{long_paragraph}

## Next Section

Short content here.
"""

chunks = structural_chunk_text(md_oversized, filepath="big.md", chunk_size=200)
test("1.6 Oversized section split", len(chunks) > 2,
     f"got {len(chunks)} chunks (expected >2)")

# Each chunk should still respect chunk_size roughly
over_limit = [c for c in chunks if len(c.text) > 250]
test("1.6b No chunk wildly over limit", len(over_limit) == 0,
     f"{len(over_limit)} chunks over 250 chars (limit=200)")


# 1.7 Empty input
chunks = structural_chunk_text("", filepath="empty.md")
test("1.7 Empty input", len(chunks) == 0)

chunks = structural_chunk_text("   \n  \n  ", filepath="whitespace.md")
test("1.7b Whitespace only", len(chunks) == 0)


# 1.8 Plain text (no Markdown structure)
plain = "Just some plain text without any Markdown headings or structure. " * 10
chunks = structural_chunk_text(plain, filepath="plain.txt", chunk_size=200)
test("1.8 Plain text fallback", len(chunks) > 0,
     f"got {len(chunks)} chunks")


# 1.9 Nested code fence with tildes
md_tilde = """# Example

~~~bash
echo "hello"
ls -la
~~~

Done.
"""

blocks = _parse_structural_blocks(md_tilde)
code_blocks = [b for b in blocks if "echo" in b["body"]]
test("1.9 Tilde fence detected", len(code_blocks) >= 1,
     f"blocks: {[(b['kind'], b['body'][:40]) for b in blocks]}")


# ══════════════════════════════════════════════════════════════
# 2. COLLECTION DELETION
# ══════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("2. COLLECTION DELETION TESTS")
print("=" * 60)

# Use a temp directory to avoid conflicts with existing data
test_data_dir = tempfile.mkdtemp(prefix="rag_test_")

try:
    from core.vector_store import VectorStore

    store = VectorStore(data_dir=test_data_dir)

    # 2.1 Create a collection by opening it
    coll_name = "test_delete"
    coll_path = os.path.join(test_data_dir, coll_name)

    # Manually create collection directory with dummy files
    os.makedirs(coll_path, exist_ok=True)
    os.makedirs(os.path.join(coll_path, "db"), exist_ok=True)

    # Write a dummy registry
    registry = {"/tmp/test.md": {"chunk_count": 3, "doc_id": "abc123"}}
    with open(os.path.join(coll_path, "_registry.json"), "w") as f:
        json.dump(registry, f)

    # Write a dummy config
    config = {"chunk_mode": "recursive", "chunk_size": 500}
    with open(os.path.join(coll_path, "_config.json"), "w") as f:
        json.dump(config, f)

    test("2.1 Collection dir exists", os.path.isdir(coll_path))

    # 2.2 Delete the collection
    result = store.delete_collection(coll_name)
    test("2.2 Delete returns deleted=True", result["deleted"] is True)

    # 2.3 Verify directory is removed
    test("2.3 Directory removed", not os.path.exists(coll_path))

    # 2.4 Delete non-existent collection
    result = store.delete_collection("nonexistent_collection")
    test("2.4 Delete non-existent", result["deleted"] is False)

    # 2.5 Collection removed from cache
    store._collections["cached_test"] = None  # add to cache
    coll_path2 = os.path.join(test_data_dir, "cached_test")
    os.makedirs(coll_path2, exist_ok=True)
    result = store.delete_collection("cached_test")
    test("2.5 Cache cleared", "cached_test" not in store._collections)

    # 2.6 Service layer delete_collection
    from core import service
    # Override the store singleton for testing
    old_store = service._store
    service._store = VectorStore(data_dir=test_data_dir)

    # Create a collection via service
    svc_coll = "svc_delete_test"
    svc_path = os.path.join(test_data_dir, svc_coll)
    os.makedirs(svc_path, exist_ok=True)
    with open(os.path.join(svc_path, "_registry.json"), "w") as f:
        json.dump({}, f)

    svc_result = service.delete_collection(svc_coll)
    test("2.6 Service delete_collection", svc_result["status"] == "ok",
         f"result: {svc_result}")

    # 2.7 Service delete non-existent
    svc_result = service.delete_collection("no_such_collection")
    test("2.7 Service delete missing", svc_result["status"] == "error",
         f"result: {svc_result}")

    # 2.8 Service delete empty name
    svc_result = service.delete_collection("")
    test("2.8 Service delete empty name", svc_result["status"] == "error")

    # Restore original store
    service._store = old_store

finally:
    # Cleanup
    shutil.rmtree(test_data_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# 3. EMBEDDING PREFIX
# ══════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("3. EMBEDDING PREFIX TESTS")
print("=" * 60)

import inspect
from core.embeddings import EmbeddingService

# 3.1 Verify encode_query source contains "query:" prefix
source = inspect.getsource(EmbeddingService.encode_query)
test("3.1 encode_query has query: prefix", '"query:' in source or "'query:" in source,
     "prefix not found in source")

# 3.2 Verify encode does NOT add prefix
source_encode = inspect.getsource(EmbeddingService.encode)
test("3.2 encode has no prefix", "query:" not in source_encode and "document:" not in source_encode)


# ══════════════════════════════════════════════════════════════
# 4. CONTEXT EXPANSION (unit level — no model needed)
# ══════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("4. CONTEXT EXPANSION TESTS")
print("=" * 60)

# 4.1 Verify fetch_neighbors exists on VectorStore
test("4.1 fetch_neighbors method exists",
     hasattr(VectorStore, "fetch_neighbors"))

# 4.2 Verify search accepts expand_context parameter
import inspect
sig = inspect.signature(service.search)
params = list(sig.parameters.keys())
test("4.2 search has expand_context param", "expand_context" in params,
     f"params: {params}")

# 4.3 Verify expand_context default is 0
default_val = sig.parameters["expand_context"].default
test("4.3 expand_context default is 0", default_val == 0,
     f"default: {default_val}")

# 4.4 Verify fetch_neighbors signature
sig_fn = inspect.signature(VectorStore.fetch_neighbors)
fn_params = list(sig_fn.parameters.keys())
test("4.4 fetch_neighbors signature",
     all(p in fn_params for p in ["source", "chunk_index", "doc_id", "n_before", "n_after"]),
     f"params: {fn_params}")

# 4.5 Test fetch_neighbors with temp store (no real vectors)
test_dir2 = tempfile.mkdtemp(prefix="rag_test_expand_")
try:
    store2 = VectorStore(data_dir=test_dir2)
    # Calling fetch_neighbors on a non-existent collection should not crash
    neighbors = store2.fetch_neighbors(
        source="test.md", chunk_index=5, doc_id="abc123",
        n_before=1, n_after=1, collection="expand_test"
    )
    test("4.5 fetch_neighbors graceful on empty", isinstance(neighbors, list))
finally:
    shutil.rmtree(test_dir2, ignore_errors=True)


# ══════════════════════════════════════════════════════════════
# 5. CONFIGURATION — chunk_mode includes "structural"
# ══════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("5. CONFIGURATION TESTS")
print("=" * 60)

from core.vector_store import DEFAULT_COLLECTION_CONFIG

test("5.1 Default chunk_mode is recursive",
     DEFAULT_COLLECTION_CONFIG["chunk_mode"] == "recursive")

# 5.2 Verify service.ingest_file accepts "structural" via source
source_ingest = inspect.getsource(service.ingest_file)
test("5.2 ingest_file handles structural", "structural" in source_ingest,
     "'structural' not found in ingest_file source")

# 5.3 Verify service.ingest_content accepts "structural"
source_content = inspect.getsource(service.ingest_content)
test("5.3 ingest_content handles structural", "structural" in source_content,
     "'structural' not found in ingest_content source")


# ══════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("TEST SUMMARY")
print("=" * 60)
total = PASS_COUNT + FAIL_COUNT
print(f"Total: {total}  |  Passed: {PASS_COUNT}  |  Failed: {FAIL_COUNT}")
print(f"Pass rate: {PASS_COUNT / total * 100:.1f}%")
print()
for r in RESULTS:
    print(r)
