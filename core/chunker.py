"""Text chunker with recursive character splitting."""

import os
import hashlib
from dataclasses import dataclass


@dataclass
class Chunk:
    """A single text chunk with metadata."""
    text: str
    source: str
    chunk_index: int
    doc_id: str  # sha256(source)[:16]


def compute_doc_id(filepath: str) -> str:
    """Compute a stable document ID from file path."""
    normalized = os.path.normpath(os.path.abspath(filepath))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def chunk_text(
    text: str,
    filepath: str = "",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """Split text into overlapping chunks using recursive character splitting.

    Splits by paragraphs first, then sentences, then characters,
    trying to keep chunks under chunk_size while preserving semantic boundaries.
    """
    if not text.strip():
        return []

    doc_id = compute_doc_id(filepath)
    segments = _recursive_split(text, chunk_size, chunk_overlap)

    chunks = []
    for i, segment in enumerate(segments):
        segment = segment.strip()
        if segment:
            chunks.append(Chunk(
                text=segment,
                source=filepath,
                chunk_index=i,
                doc_id=doc_id,
            ))
    return chunks


def _recursive_split(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """Recursively split text by paragraph, sentence, then character boundaries."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    # Try splitting by paragraphs first
    paragraphs = text.split("\n\n")
    if len(paragraphs) > 1:
        return _merge_segments(paragraphs, chunk_size, chunk_overlap, "\n\n")

    # Try splitting by single newlines
    lines = text.split("\n")
    if len(lines) > 1:
        return _merge_segments(lines, chunk_size, chunk_overlap, "\n")

    # Try splitting by sentences
    sentences = _split_sentences(text)
    if len(sentences) > 1:
        return _merge_segments(sentences, chunk_size, chunk_overlap, " ")

    # Last resort: split by characters with overlap
    return _split_by_chars(text, chunk_size, chunk_overlap)


def _merge_segments(
    segments: list[str],
    chunk_size: int,
    chunk_overlap: int,
    separator: str,
) -> list[str]:
    """Merge small segments into chunks, splitting large ones recursively."""
    result = []
    current_parts = []
    current_len = 0

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue

        seg_len = len(seg)

        # If a single segment exceeds chunk_size, recursively split it
        if seg_len > chunk_size:
            if current_parts:
                result.append(separator.join(current_parts))
                current_parts = []
                current_len = 0
            sub_chunks = _recursive_split(seg, chunk_size, chunk_overlap)
            result.extend(sub_chunks)
            continue

        # Check if adding this segment would exceed chunk_size
        new_len = current_len + seg_len + (len(separator) if current_parts else 0)
        if new_len > chunk_size and current_parts:
            result.append(separator.join(current_parts))
            # Keep overlap: carry over tail of current chunk
            current_parts, current_len = _compute_overlap(
                current_parts, separator, chunk_overlap
            )
            current_parts.append(seg)
            current_len += seg_len + len(separator)
        else:
            current_parts.append(seg)
            current_len = new_len

    if current_parts:
        result.append(separator.join(current_parts))

    return result


def _compute_overlap(
    parts: list[str],
    separator: str,
    overlap_size: int,
) -> tuple[list[str], int]:
    """Compute overlap parts from the tail of the current chunk."""
    if overlap_size <= 0:
        return [], 0

    overlap_parts = []
    overlap_len = 0
    for part in reversed(parts):
        part_len = len(part) + (len(separator) if overlap_parts else 0)
        if overlap_len + part_len > overlap_size:
            break
        overlap_parts.insert(0, part)
        overlap_len += part_len

    return overlap_parts, overlap_len


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, handling Chinese and English punctuation."""
    import re
    # Split on sentence-ending punctuation followed by space or end
    sentences = re.split(r'(?<=[。！？.!?])\s*', text)
    return [s for s in sentences if s.strip()]


def _split_by_chars(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """Hard split by character count with overlap."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - chunk_overlap
    return chunks


# ── Semantic Chunking ────────────────────────────────────────


def semantic_chunk_text(
    text: str,
    filepath: str = "",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """Split text into chunks based on semantic similarity between sentences.

    Uses the embedding model to encode sentences, then detects topic boundaries
    where adjacent sentence similarity drops below a dynamic threshold
    (mean - 1 standard deviation). Oversized chunks fall back to recursive
    character splitting.

    Args:
        text: The full document text.
        filepath: Source file path (used for doc_id).
        chunk_size: Max characters per chunk (safety limit).
        chunk_overlap: Overlap size (used only for fallback recursive split).

    Returns:
        List of Chunk objects.
    """
    import logging
    logger = logging.getLogger(__name__)

    if not text.strip():
        return []

    doc_id = compute_doc_id(filepath)

    # Step 1: split into sentences (preserving paragraph structure)
    sentences = _extract_sentences(text)
    if not sentences:
        return []

    # Short-circuit: too few sentences or fits in one chunk
    total_len = sum(len(s) for s in sentences)
    if len(sentences) <= 2 or total_len <= chunk_size:
        combined = " ".join(sentences).strip()
        if combined:
            return [Chunk(text=combined, source=filepath,
                          chunk_index=0, doc_id=doc_id)]
        return []

    # Step 2: encode all sentences
    try:
        from core.embeddings import EmbeddingService
        embedder = EmbeddingService()
        embeddings = embedder.encode(sentences)
    except Exception as e:
        logger.warning(f"Semantic chunking failed to encode sentences: {e}. "
                       f"Falling back to recursive split.")
        segments = _recursive_split(text, chunk_size, chunk_overlap)
        return [
            Chunk(text=seg.strip(), source=filepath,
                  chunk_index=i, doc_id=doc_id)
            for i, seg in enumerate(segments) if seg.strip()
        ]

    if len(embeddings) < 2:
        combined = " ".join(sentences).strip()
        return [Chunk(text=combined, source=filepath,
                      chunk_index=0, doc_id=doc_id)] if combined else []

    # Step 3: compute adjacent cosine similarities
    similarities = []
    for i in range(len(embeddings) - 1):
        sim = _dot_product(embeddings[i], embeddings[i + 1])
        similarities.append(sim)

    # Step 4: dynamic threshold = mean - 1*std
    threshold = _dynamic_threshold(similarities)

    # Step 5: find breakpoints and group sentences
    groups = _group_by_breakpoints(sentences, similarities, threshold, chunk_size)

    # Step 6: build chunks, falling back to recursive split for oversized groups
    chunks = []
    chunk_idx = 0
    for group in groups:
        combined = " ".join(group).strip()
        if not combined:
            continue
        if len(combined) <= chunk_size:
            chunks.append(Chunk(text=combined, source=filepath,
                                chunk_index=chunk_idx, doc_id=doc_id))
            chunk_idx += 1
        else:
            # Oversized group: fall back to recursive character split
            sub_segments = _recursive_split(combined, chunk_size, chunk_overlap)
            for seg in sub_segments:
                seg = seg.strip()
                if seg:
                    chunks.append(Chunk(text=seg, source=filepath,
                                        chunk_index=chunk_idx, doc_id=doc_id))
                    chunk_idx += 1

    return chunks


def _extract_sentences(text: str) -> list[str]:
    """Split text into sentences, treating paragraph breaks as sentence boundaries."""
    import re
    # First split by paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    sentences = []
    for para in paragraphs:
        # Within each paragraph, split by sentence-ending punctuation
        parts = re.split(r'(?<=[。！？.!?])\s*', para)
        for part in parts:
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def _dot_product(a: list[float], b: list[float]) -> float:
    """Compute dot product of two vectors (cosine similarity for normalized vectors)."""
    return sum(x * y for x, y in zip(a, b))


def _dynamic_threshold(similarities: list[float], n_sigma: float = 1.0) -> float:
    """Compute dynamic breakpoint threshold as mean - n_sigma * std."""
    if not similarities:
        return 0.5
    n = len(similarities)
    mean = sum(similarities) / n
    if n < 2:
        return mean
    variance = sum((s - mean) ** 2 for s in similarities) / n
    std = variance ** 0.5
    return mean - n_sigma * std


def _group_by_breakpoints(
    sentences: list[str],
    similarities: list[float],
    threshold: float,
    chunk_size: int,
) -> list[list[str]]:
    """Group sentences into chunks based on similarity breakpoints.

    A new chunk starts wherever adjacent similarity drops below threshold,
    or when the current group would exceed chunk_size.
    """
    groups = []
    current_group = [sentences[0]]
    current_len = len(sentences[0])

    for i in range(len(similarities)):
        next_sentence = sentences[i + 1]
        next_len = len(next_sentence) + 1  # +1 for space join

        is_break = similarities[i] < threshold
        would_exceed = current_len + next_len > chunk_size

        if is_break or (would_exceed and current_group):
            groups.append(current_group)
            current_group = [next_sentence]
            current_len = len(next_sentence)
        else:
            current_group.append(next_sentence)
            current_len += next_len

    if current_group:
        groups.append(current_group)

    return groups


# ── Structural Chunking ──────────────────────────────────────


def structural_chunk_text(
    text: str,
    filepath: str = "",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """Split text into chunks that respect document structure.

    Recognises Markdown headings, fenced code blocks, and tables as
    structural boundaries.  Each resulting chunk is prefixed with its
    enclosing heading (if any) so the LLM retains section context.

    Within a single structural section that exceeds *chunk_size*, the
    content is further split by the existing recursive character splitter.

    Args:
        text: The full document text.
        filepath: Source file path (used for doc_id).
        chunk_size: Max characters per chunk.
        chunk_overlap: Overlap size (passed to recursive split for
            oversized sections).

    Returns:
        List of Chunk objects.
    """
    if not text.strip():
        return []

    doc_id = compute_doc_id(filepath)
    blocks = _parse_structural_blocks(text)

    if not blocks:
        return []

    # Merge small blocks and split large ones
    raw_chunks = _merge_and_split_blocks(blocks, chunk_size, chunk_overlap)

    chunks = []
    for i, segment in enumerate(raw_chunks):
        segment = segment.strip()
        if segment:
            chunks.append(Chunk(
                text=segment,
                source=filepath,
                chunk_index=i,
                doc_id=doc_id,
            ))
    return chunks


def _parse_structural_blocks(text: str) -> list[dict]:
    """Parse text into structural blocks based on document structure.

    Returns a list of dicts with keys:
        heading: str — the most recent Markdown heading (may be empty)
        body: str — the text content of this block
        kind: str — "heading", "code", "table", or "text"
    """
    import re

    lines = text.split("\n")
    blocks: list[dict] = []
    current_heading = ""
    current_lines: list[str] = []
    in_code_fence = False
    in_table = False

    def _flush():
        """Flush accumulated lines as a text block."""
        nonlocal current_lines
        body = "\n".join(current_lines).strip()
        if body:
            blocks.append({"heading": current_heading, "body": body, "kind": "text"})
        current_lines = []

    for line in lines:
        stripped = line.strip()

        # ── Fenced code block (``` or ~~~) ──
        if re.match(r'^(`{3,}|~{3,})', stripped):
            if in_code_fence:
                # Closing fence — flush the code block
                current_lines.append(line)
                _flush()
                in_code_fence = False
                continue
            else:
                # Opening fence — flush any preceding text first
                _flush()
                in_code_fence = True
                current_lines = [line]
                continue

        if in_code_fence:
            current_lines.append(line)
            continue

        # ── Markdown heading ──
        heading_match = re.match(r'^(#{1,6})\s+(.+)', stripped)
        if heading_match:
            _flush()
            current_heading = stripped
            # Store the heading line itself as a tiny block so it's not lost
            blocks.append({"heading": current_heading, "body": current_heading, "kind": "heading"})
            continue

        # ── Table row (lines starting with |) ──
        is_table_row = stripped.startswith("|") and stripped.endswith("|")
        if is_table_row:
            if not in_table:
                # Entering a table — flush preceding text
                _flush()
                in_table = True
            current_lines.append(line)
            continue
        elif in_table:
            # Leaving the table
            _flush()
            in_table = False

        # ── Plain text ──
        current_lines.append(line)

    # Flush remaining content
    if in_code_fence or in_table:
        _flush()
    else:
        _flush()

    return blocks


def _merge_and_split_blocks(
    blocks: list[dict],
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """Merge small structural blocks and split oversized ones.

    Each chunk retains its heading prefix for context.
    """
    result: list[str] = []
    buf_parts: list[str] = []
    buf_heading = ""
    buf_len = 0

    def _prefix(heading: str, body: str) -> str:
        """Prepend heading to body text if heading exists and differs."""
        if heading and not body.startswith(heading):
            return f"{heading}\n\n{body}"
        return body

    for block in blocks:
        heading = block["heading"]
        body = block["body"]
        prefixed = _prefix(heading, body)
        block_len = len(prefixed)

        # Oversized block: flush buffer first, then split
        if block_len > chunk_size:
            if buf_parts:
                result.append("\n\n".join(buf_parts))
                buf_parts = []
                buf_len = 0

            # Split the body (without prefix) then prepend heading to each
            sub_segments = _recursive_split(body, chunk_size, chunk_overlap)
            for seg in sub_segments:
                seg = seg.strip()
                if seg:
                    result.append(_prefix(heading, seg))
            continue

        # Check if merging would exceed chunk_size
        new_len = buf_len + block_len + (2 if buf_parts else 0)  # \n\n join
        heading_changed = buf_parts and heading != buf_heading

        if heading_changed or (new_len > chunk_size and buf_parts):
            result.append("\n\n".join(buf_parts))
            buf_parts = []
            buf_len = 0

        buf_parts.append(prefixed)
        buf_len += block_len + (2 if buf_len > 0 else 0)
        buf_heading = heading

    if buf_parts:
        result.append("\n\n".join(buf_parts))

    return result
