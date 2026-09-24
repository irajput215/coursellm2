---
title: Cross-encoder reranking
provenance: Project-authored reference material for the CourseLLM evaluation corpus. Not an external source.
author: CourseLLM engineering
reviewed: 2026-01-01
---

# Cross-encoder reranking

A reranking stage takes a small candidate list from first-stage retrieval and
reorders it with a more expensive, more accurate scoring model. In a
retrieval-augmented pipeline the candidates come from fusion; the reranker's job
is to put the passages that actually answer the question at the top, because
only the top few are passed to the generator.

## Bi-encoder versus cross-encoder

A **bi-encoder** encodes the query and each passage separately, then compares
the two vectors. Passage vectors can be computed once, offline, which is what
makes retrieval over a large corpus fast. The cost is that the passage
representation is produced without ever seeing the query.

A **cross-encoder** concatenates the query and the passage into one input and
runs the model over both together, so every query token can attend to every
passage token. The score depends jointly on the pair. That joint view is
strictly more expressive than comparing two independently produced vectors, and
it is stronger at judging whether a passage actually answers a question.

The consequence is cost. A cross-encoder cannot precompute passage
representations, because the representation depends on the query. It performs
one forward pass per `(query, passage)` pair, so scoring `N` candidates costs
`N` forward passes. That is why it is applied only to the fused top-N and never
to the whole corpus.

## Reranking changes order, not recall

A reranker can only reorder the candidates it is given. If the correct document
was never retrieved by the first stage, no reranker can recover it. This
distinction matters when diagnosing quality: "the right document was never
retrieved" is a recall failure in a first-stage retriever, while "the right
document was retrieved but ranked below the cut" is a ranking failure that a
reranker can fix.

Keeping the pre-rerank position alongside the new one makes the two failures
separable in evaluation. It also means a reranker that mutates its inputs in
place would destroy the evidence needed to tell them apart, so a reranking
implementation should return new records rather than overwrite the candidate
list.

## Degradation

A cross-encoder is a model: it can be unavailable, slow, or fail on a malformed
input. Because reranking is an improvement rather than a correctness
requirement, a reranker that fails or exceeds a timeout should not fail the
request. The safe behaviour is to keep the fused order, record a
machine-readable degradation reason, and answer from that order. BGE rerankers
are trained with a maximum input length of 512 tokens, so long passages are
truncated before scoring.
