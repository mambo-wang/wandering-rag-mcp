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
