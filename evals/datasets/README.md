# The golden dataset

## Contents

* `golden_rag.jsonl` — 32 questions, one JSON object per line. Fields: `id`,
  `question`, `expected_answer`, `expected_sources`, `category`, `notes`.
* `corpus/*.md` — 8 project-authored reference documents, the only material the
  questions are answerable from.

| Category | Count | Meaning |
|----------|-------|---------|
| `factual` | 20 | the answer is stated in one source document |
| `multi_hop` | 4 | the answer requires two source documents |
| `identifier` | 4 | the answer is a specific value, name or number |
| `unanswerable` | 4 | the answer is deliberately absent from the corpus |

Every answerable entry's `expected_sources` lists the corpus files that contain
the answer, and `evals`'s end-to-end test asserts that each expected source is
retrievable for its question by the retrieval stack. That is a lower-bound
sanity check on the dataset, not a quality claim about retrieval.

## The corpus

The eight documents cover the topics the system genuinely implements: dense
versus lexical retrieval, Okapi BM25, HNSW and approximate recall, reciprocal
rank fusion, cross-encoder reranking, chunking and overlap, vector
dimensionality, and prompt injection in retrieved evidence.

Each file's front matter labels its provenance explicitly as
**project-authored reference material, not an external source**. They are
written to be factually correct; an error in them would corrupt every metric
downstream, so claims that could not be stated precisely were omitted rather
than asserted. The documents cite no external numbers except constants that are
standard and checkable (for example BM25's commonly used `k1 = 1.2` and
`b = 0.75`, RRF's `k = 60` from Cormack, Clarke and Buettcher 2009, and
pgvector's default HNSW `m = 16` and `ef_construction = 64`).

## How it was built

1. The corpus was written first, one document per topic, with the exact
   vocabulary a question would need. Distinctive terms (`ef_search`,
   `ef_construction`, `k1`, `reciprocal rank fusion`, `cross-encoder`) are what
   make a hashing-embedder run retrievable at all.
2. Questions were written against the finished corpus. Each `expected_answer` is
   a short, checkable statement drawn from the text, and `expected_sources` names
   every document needed to produce it.
3. The retrieval harness was run and its per-question `candidate_documents`
   inspected; an entry whose expected source was not retrievable was rewritten
   until it was. The committed end-to-end test keeps that property enforced.
4. Four unanswerable entries were added. Their `expected_sources` is empty by
   construction, and `load_golden_entries` rejects an unanswerable entry that
   lists sources or an answerable entry that lists none.

## The unanswerable entries

`q029`–`q032` ask for facts that are not in the corpus: a provider price, a
model checksum, an HNSW layer count, and a recommended contract chunk size. They
exist because the refusal path is a behaviour worth measuring, and because an
evaluation that only contains answerable questions can be gamed by a system that
always answers.

## What this dataset does not cover

* **It is small.** Eight documents and 14 chunks mean the candidate pool
  approaches the corpus size, so `recall@k` saturates for larger `k`. The
  metrics verify the harness and the determinism property; they are not a claim
  about retrieval quality on a real course.
* **The CI embedder is not semantic.** `HashingEmbedder` is deterministic term
  hashing, not a trained encoder. It cannot represent paraphrase, so the run
  exercises the storage, index and fusion stack rather than embedding quality.
  Numbers from a `local` sentence-transformer run and numbers from a `hashing`
  run are not comparable, which is why the embedding provider is recorded in
  every report.
* **No adversarial corpus.** Prompt injection is covered by
  `apps/api/tests/security/`; this dataset measures retrieval and generation
  quality, not injection resistance.
* **No human-labelled relevance.** Relevance is defined by the document that
  contains the answer, which is a coarse proxy for passage-level relevance.
* **No multi-turn or conversational questions.** Every entry is single-turn.
* **No non-English content.**

## Provenance

| Artefact | SHA-256 |
|----------|---------|
| `golden_rag.jsonl` | `309c4d5ceb285841802bec910dd916acfa32ec59403958e870c85cacbb49b5e7` |
| corpus (sorted filename + content) | `b072f18351ece319b9a4e8c3d516b609745c9178b946b51b2455af8a58090b72` |

The report records both hashes, and the regression gate refuses to compare
reports whose dataset hashes differ.
