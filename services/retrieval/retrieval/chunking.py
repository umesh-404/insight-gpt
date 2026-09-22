"""Chunking — the unit that gets embedded, retrieved, reranked, and cited.

Retrieval quality is decided here, not at query time. InsightGPT uses two
chunkers, chosen by document type (docs/04-retrieval-rag.md §2), the same split
rememory makes between record-level content and its heading-aware DocsChunker:

* **Whole-record** for tickets and reviews. A ticket or review is already one
  self-contained thought; splitting it would scatter a single complaint across
  chunks and destroy citations. One record -> one chunk, unless it is
  pathologically long (a giant pasted log), which is windowed with overlap.
* **Heading/section-aware** for reports and emails. Long documents are split on
  their structure (Markdown/section headings), so each chunk is a coherent
  section rather than an arbitrary character window; the heading trail rides on
  every chunk so no fragment loses its topic.

The **breadcrumb header** (``embed_text``) is prepended to the embedded and
sparse text ONLY — never to the stored ``content`` quoted back to the user. It
supplies the vocabulary a bare chunk lacks (title, section path, date, source
type), exactly rememory's ``header_for`` / ``embed_text`` split.
"""

from __future__ import annotations

import re

from .config import ChunkingConfig
from .models import Chunk, Document

# Structure signals for reports/emails.
ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SETEXT = re.compile(r"^\s*(=+|-{3,})\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")

_RECORD_TYPES = {"ticket", "review"}


def chunk_document(doc: Document, cfg: ChunkingConfig) -> list[Chunk]:
    """Split one document into chunks according to its ``source_type``."""
    if doc.source_type in _RECORD_TYPES:
        chunks = _whole_record(doc, cfg)
    else:
        chunks = _heading_aware(doc, cfg)
    total = len(chunks)
    for c in chunks:
        c.chunk_total = total
    return chunks


def embed_text(
    doc: Document, chunk: Chunk, cfg: ChunkingConfig, *, doc_context: str | None = None
) -> str:
    """The text actually embedded / sparse-tokenized for a chunk.

    Prefixed with a breadcrumb built from the document's provenance, plus — when
    ``contextual_augmentation`` is on — the region and category so a chunk that
    never names its own region ("the backlog delayed shipments") still carries
    that vocabulary. Mirroring docs/04-retrieval-rag.md §2.3::

        Q2 Operations Review :: Fulfilment > Root cause
        2026-05-14 · report · manager · North · Electronics
        [context: North fulfilment backlog concentrated in electronics.]
        <chunk text…>

    This "situate the chunk within its document" prefix is the contextual-
    retrieval technique that cuts retrieval failures. It is a pure function of
    the document's canonical fields (``doc_context`` aside), so it runs fully
    offline and does NOT enter the content hash — the hash is computed in
    :mod:`retrieval.schema` over the original fields, so augmenting the embedded
    text never churns changed-only re-indexing.

    ``doc_context`` is an optional one-line situating sentence (LLM-written at
    index time); the header is deliberately NOT stored, so the user reads back
    real text either way.
    """
    if not cfg.contextual_header:
        return chunk.content
    crumb = doc.title or doc.doc_id
    if chunk.heading_path:
        crumb = f"{crumb} :: {chunk.heading_path}"
    meta_bits = [doc.date, doc.source_type, doc.author_role]
    if cfg.contextual_augmentation:
        meta_bits += [doc.region, doc.category]
    present = [b for b in meta_bits if b]
    header = crumb if not present else f"{crumb}\n{' · '.join(present)}"
    if doc_context:
        header = f"{header}\n[context: {doc_context.strip()}]"
    return f"{header}\n{chunk.content}"


# --------------------------------------------------------------- record chunker
def _whole_record(doc: Document, cfg: ChunkingConfig) -> list[Chunk]:
    body = doc.body.strip()
    if len(body) <= cfg.max_chunk_chars:
        return [Chunk(content=body, chunk_index=0, chunk_total=1)]
    # Pathologically long record: window with overlap so no sentence is cut in a
    # way that appears in neither chunk.
    return _window(body, cfg)


# -------------------------------------------------------------- heading chunker
def _heading_aware(doc: Document, cfg: ChunkingConfig) -> list[Chunk]:
    lines = doc.body.splitlines()
    if not lines:
        return [Chunk(content=doc.body.strip(), chunk_index=0, chunk_total=1)]

    sections = _split_sections(lines)
    chunks: list[Chunk] = []
    for start, end, trail in sections:
        text = "\n".join(lines[start:end]).strip("\n")
        if not text.strip():
            continue
        heading_path = " > ".join(trail) if trail else None

        if len(text) <= cfg.max_chunk_chars:
            if len(text.strip()) < cfg.min_chunk_chars and chunks:
                # A stub section: fold into the previous chunk rather than
                # storing a near-empty vector.
                chunks[-1].content += "\n\n" + text
                continue
            chunks.append(
                Chunk(content=text, chunk_index=0, chunk_total=0, heading_path=heading_path)
            )
        else:
            for part in _window(text, cfg):
                part.heading_path = heading_path
                chunks.append(part)

    if not chunks:  # a body with no headings and no splittable content
        chunks = [Chunk(content=doc.body.strip(), chunk_index=0, chunk_total=1)]
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


def _split_sections(lines: list[str]) -> list[tuple[int, int, list[str]]]:
    """Yield ``(start, end, heading_trail)`` spans, respecting code fences."""
    boundaries: list[tuple[int, int, str]] = []  # (line_index, level, title)
    in_fence = False
    fence_marker = ""

    for i, line in enumerate(lines):
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue  # a '#' inside a fenced block is not a heading

        m = ATX_HEADING.match(line)
        if m:
            boundaries.append((i, len(m.group(1)), m.group(2).strip()))
            continue

        if (
            i > 0
            and SETEXT.match(line)
            and lines[i - 1].strip()
            and not ATX_HEADING.match(lines[i - 1])
        ):
            level = 1 if line.strip().startswith("=") else 2
            boundaries.append((i - 1, level, lines[i - 1].strip()))

    if not boundaries:
        return [(0, len(lines), [])]

    sections: list[tuple[int, int, list[str]]] = []
    # Preamble before the first heading (often the real summary).
    if boundaries[0][0] > 0:
        sections.append((0, boundaries[0][0], []))

    trail: list[tuple[int, str]] = []  # (level, title)
    for idx, (line_no, level, title) in enumerate(boundaries):
        while trail and trail[-1][0] >= level:
            trail.pop()
        trail.append((level, title))
        end = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines)
        sections.append((line_no, end, [t for _, t in trail]))

    return sections


# ------------------------------------------------------------------- windowing
def _window(text: str, cfg: ChunkingConfig) -> list[Chunk]:
    """Fixed line windows with overlap, for a section too large to keep whole."""
    lines = text.splitlines() or [text]
    chunks: list[Chunk] = []
    i = 0
    while i < len(lines):
        window: list[str] = []
        size = 0
        j = i
        while j < len(lines) and size + len(lines[j]) + 1 <= cfg.max_chunk_chars:
            window.append(lines[j])
            size += len(lines[j]) + 1
            j += 1
        if not window:  # one line longer than max_chars: take it alone, truncate
            window = [lines[i][: cfg.max_chunk_chars]]
            j = i + 1

        piece = "\n".join(window)
        if len(piece) >= cfg.min_chunk_chars or not chunks:
            chunks.append(Chunk(content=piece, chunk_index=len(chunks), chunk_total=0))
        else:
            chunks[-1].content += "\n" + piece

        if j >= len(lines):
            break
        i = max(j - cfg.overlap_lines, i + 1)

    return chunks
