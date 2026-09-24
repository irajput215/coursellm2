---
title: Dense and lexical retrieval
provenance: Project-authored reference material for the CourseLLM evaluation corpus. Not an external source.
author: CourseLLM engineering
reviewed: 2026-01-01
---

# Dense and lexical retrieval

A retrieval-augmented system needs a first-stage retriever that reduces a whole
corpus to a short candidate list. Two families dominate, and they fail in
different ways.

## Dense (vector) retrieval

A **bi-encoder** maps a passage to a fixed-length vector without seeing the
query, and maps the query to a vector in the same space. Relevance is a vector
similarity: cosine similarity, or inner product when vectors are normalised to
unit length. Because the two sides are encoded independently, every passage
vector can be computed once at ingestion time and an approximate nearest
neighbour index (for example HNSW) can serve queries without rescanning the
corpus.

The strength of a bi-encoder is that it generalises beyond the literal words. A
question about "how results are combined from two rankers" can match a passage
that never uses the word "combine". A trained encoder learns that paraphrases
belong together.

The corresponding weakness is exactness. A bi-encoder has no guaranteed
mechanism for matching a rare identifier, an error code, a version string, or a
token that its vocabulary never learned. Two passages that differ only in a
critical identifier can land close together.

## Lexical (sparse) retrieval

A lexical retriever scores a passage by the terms it shares with the query.
Okapi BM25 is the standard, and it is a bag-of-words model: it has no notion of
synonymy and cannot match a paraphrase that shares no terms.

What it does have is precision on exactly the tokens a dense model blurs. A
query for `ef_construction`, `bge-reranker-base`, `BM25_K1`, or `c++` matches
documents that contain those literal tokens and nothing else. Rare terms carry
high inverse document frequency, so a single exact match can dominate the
ranking.

## Why hybrid retrieval

Neither family dominates. Dense retrieval recovers meaning; lexical retrieval
recovers strings. A system that runs both and combines their results keeps the
recall of the union. CourseLLM runs both retrievers over the same eligible,
tenant-scoped candidate set and then fuses their two ranked lists by reciprocal
rank fusion. Fusion consumes ranks rather than scores, because a cosine
similarity and a BM25 score are not on a comparable scale.
