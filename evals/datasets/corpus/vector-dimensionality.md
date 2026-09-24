---
title: Vector dimensionality and embedding compatibility
provenance: Project-authored reference material for the CourseLLM evaluation corpus. Not an external source.
author: CourseLLM engineering
reviewed: 2026-01-01
---

# Vector dimensionality and embedding compatibility

## What the dimension is

An embedding model maps text to a vector of fixed length. That length is the
**dimensionality** of the model: a 384-dimensional model returns 384
floating-point numbers for every input, and a 1536-dimensional model returns
1536. The length is a property of the model, not of the text, and it does not
change between calls.

## Why dimensionality matters operationally

The dimension fixes the size of every stored vector. Storing a corpus of `n`
chunks costs roughly `n` times the dimension times the width of one floating
point number, before index overhead, so doubling the dimension doubles vector
storage. An approximate nearest neighbour index such as HNSW also needs a fixed
dimension in order to be built, because its distance computations assume all
vectors live in the same coordinate space. In pgvector the dimension is declared
on the column, and a vector of a different length cannot be inserted into it.

## Similarity

Cosine similarity compares the directions of two vectors and ignores their
magnitudes. If every vector is normalised to unit length, cosine similarity and
inner product are equivalent, which is why normalised embeddings are often
stored and compared with an inner-product operation. pgvector's `<=>` operator
computes cosine **distance**, the complement of cosine similarity: it returns
`1 - cosine_similarity` for the `vector_cosine_ops` operator class. A consumer
that expects similarity must convert it, and must not store a distance in a
field that is read as a similarity.

## Vectors from different models are not comparable

Two embedding models define two different vector spaces even when they share a
dimension. Coordinate 17 of a 384-dimensional vector from model A has no
relationship to coordinate 17 of a 384-dimensional vector from model B. Mixing
them does not raise an error, because the arithmetic is still valid; it silently
produces meaningless neighbours. This is worse than a crash, because the results
look like results.

The defence is to make the model part of the key. Each stored vector should be
tagged with the model that produced it, and retrieval should require the query
vector to carry the same tag. After a model change, old vectors remain valid for
the old model but must not be compared against vectors from the new one.

## More dimensions is not automatically better

A higher dimension gives the model more capacity to represent distinctions, but
it is not a free quality improvement. A larger vector costs more memory, more
compute per comparison, and more index build time, and a poorly trained
high-dimensional model can be worse than a well-trained smaller one. Distance
concentration in high dimensions can also reduce the contrast between the
nearest and the merely near neighbours if the model has not learned a useful
geometry. Choose the dimension by measuring retrieval quality, not by assuming
that larger is better.
