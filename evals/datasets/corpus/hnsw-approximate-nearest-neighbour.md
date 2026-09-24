---
title: HNSW and approximate nearest neighbour recall
provenance: Project-authored reference material for the CourseLLM evaluation corpus. Not an external source.
author: CourseLLM engineering
reviewed: 2026-01-01
---

# HNSW and approximate nearest neighbour recall

HNSW (Hierarchical Navigable Small World) is a graph index for approximate
nearest neighbour search. It is **approximate**: a query returns neighbours that
are probably close to the true nearest neighbours, not certainly the closest
points in the dataset.

## The structure

HNSW builds a hierarchy of proximity graphs. The top layers are sparse and let
a search move quickly across the space; the bottom layer is dense and holds the
actual candidates. A query enters at an upper layer, greedily walks to a closer
node, drops to the next layer, and repeats. The bottom layer is searched with a
bounded candidate list, and the best nodes found become the result.

## The parameters

- **`m`** — the maximum number of connections each node keeps per layer (each
  node keeps more than `m` connections in the bottom layer by convention). A
  larger `m` makes the graph better connected and raises recall, at the cost of
  memory and build time. pgvector's default is `m = 16`.
- **`ef_construction`** — the size of the candidate list used while building the
  graph. A larger value produces a better-quality graph and a slower build.
  pgvector's default is `ef_construction = 64`.
- **`ef_search`** — the size of the candidate list explored at query time. This
  is the recall/latency knob that does not require rebuilding the index. A
  larger `ef_search` explores more of the graph, which raises recall and raises
  latency; a smaller value is faster and may miss true neighbours.

## Recall is measured against exact search

Because the result is approximate, recall is defined relative to the exact
answer: recall@k is the fraction of the true top-k neighbours that the
approximate search returned. There is no single setting that is both fastest
and perfectly exhaustive. `ef_search` must be greater than or equal to `k` to
have any chance of returning `k` useful results, and raising it beyond a point
yields diminishing recall for rising latency.

## Filtering interacts with the graph

A metadata filter (tenant, course, document type) narrows the eligible set.
Applying the filter **inside** the index scan lets the planner work with the
eligible set directly. Retrieving a global top-k and discarding other tenants'
rows afterwards is unsafe: the approximate scan may never visit the rows the
filter would keep, so a small tenant can receive zero results while latency
looks healthy. CourseLLM pushes the tenant predicate into the SQL statement
rather than filtering the result list in application code.

In pgvector, `hnsw.ef_search` is a session or transaction setting, so it can be
raised for a heavy query and lowered for a cheap one without touching the
index.
