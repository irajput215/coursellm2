---
title: Chunking and overlap
provenance: Project-authored reference material for the CourseLLM evaluation corpus. Not an external source.
author: CourseLLM engineering
reviewed: 2026-01-01
---

# Chunking and overlap

A retriever does not index a whole document as one unit. It splits the document
into **chunks**, embeds and indexes each chunk, and returns chunks as retrieval
results. The chunk is therefore the unit of retrieval, of scoring, and of
citation.

## Why not one chunk per document

A long document covers many topics. Pooling all of it into one embedding mixes
those topics into one vector, so the vector no longer points strongly at any one
of them, and a query about a single section competes with everything else in the
document. Long chunks also consume the generator's context budget with
irrelevant text, which dilutes the evidence the model reasons over.

## Why not very small chunks

Very small chunks have the opposite problem. A sentence lifted out of its
paragraph may lose the subject it refers to, so it embeds poorly and reads
badly as evidence. A chunk also has to be large enough to be worth a citation:
if the answer spans two adjacent sentences, splitting between them forces the
retriever to surface both, and the generator to stitch them together.

The practical consequence is that chunk size is a trade-off with no universally
correct value. It depends on the documents, the embedding model, and how the
generator uses context. It should be treated as a configuration parameter and
recorded with each document, so a change to it is a reviewable event rather than
an invisible drift.

## Overlap

Chunk boundaries rarely fall at clean semantic edges. If a key sentence sits
exactly at a boundary, a non-overlapping split can place it in a chunk whose
neighbours are missing, or can cut it in half. **Overlap** addresses this by
repeating a small suffix of one chunk at the start of the next, so that text
near a boundary appears in at least one complete chunk.

Overlap has a cost. The repeated text is indexed more than once, so a chunk can
match a query largely because of content that also appears elsewhere, and the
duplicate can occupy two of the top-k slots that could have gone to two
different passages. Overlap is therefore kept small relative to the chunk size,
and the overlap must be strictly smaller than the chunk size or the splitter
never advances.

## CourseLLM's chunker

CourseLLM splits each page independently, which keeps every chunk on a single
page so a citation has one honest page number. Within a page it packs whole
paragraphs greedily up to a token limit; if one paragraph is too large it falls
back to sentence boundaries, and if one sentence is still too large it hard-splits
on word boundaries. Each level runs only when the level above it cannot fit,
which keeps the common case cheap. Overlap is computed over the analysed term
sequence, so the repeated text is a true suffix of the previous chunk and a true
prefix of the next in term space.
