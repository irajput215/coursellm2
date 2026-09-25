"""The tokenisation contract shared by indexing and querying.

There is exactly **one** analyser in this codebase, and both the write path
(:mod:`coursellm.rag.ingestion.pipeline`) and the eventual read path call into
it. That is the whole point of the module.

## Why PostgreSQL ``to_tsvector`` is not used

The obvious alternative is to store a ``tsvector`` companion column and let
PostgreSQL analyse text. It is faster to write, and it is wrong for this system
for three reasons:

1. **Index-time and query-time analysis must agree exactly.** If documents are
   analysed by ``to_tsvector('english', …)`` and queries by a differently
   configured ``plainto_tsquery``, the two term sets silently diverge and a
   query stops matching a document that plainly contains the word. The failure
   is invisible — retrieval just returns less — and it is found by users, not
   tests. Keeping analysis in one Python function makes the two paths call the
   same code by construction.
2. **BM25 is computed here, not by PostgreSQL.** ``ts_rank`` is not BM25 (no
   saturation, no length normalisation, no IDF), so the database is already
   only being used as an inverted index. Once scoring lives in application
   code, there is no benefit to splitting analysis across two languages.
3. **Identifiers are the reason the lexical half exists at all.** A student
   asking about ``ef_construction``, ``bge-reranker-base`` or ``c++`` needs
   exact-term matching; the ``english`` text-search configuration strips
   punctuation and stems, which destroys exactly those tokens.

The consequence is that ``chunk_terms.term`` holds whatever this module emits.
Changing the rules below is a re-index, not a config change.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

# Characters that survival matters for. ``-``, ``_`` and ``.`` join identifier
# fragments (``ef_construction``, ``bge-reranker-base``, ``gpt-4.1``); ``+`` is
# load-bearing for ``c++``. Everything else is a separator.
_INTRA_WORD = "-_.+"

# A token starts with an alphanumeric and continues with alphanumerics or the
# intra-word punctuation above. Requiring an alphanumeric first keeps the final
# ``+`` of ``c++`` without accepting a bare ``++`` as a term.
_TOKEN_RE = re.compile(rf"[a-z0-9][a-z0-9{re.escape(_INTRA_WORD)}]*")

# Trailing punctuation that carries no meaning. ``+`` is deliberately absent:
# stripping it would turn ``c++`` into ``c``.
_TRAILING_NOISE = "-_."

# Deliberately a plain English list rather than a library dependency. It is
# frozen, inspectable and changing it is a re-index, which is exactly the
# property wanted from a component that affects every stored term.
STOPWORDS: frozenset[str] = frozenset(
    (
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "aren't",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "cannot",
        "could",
        "couldn't",
        "did",
        "didn't",
        "do",
        "does",
        "doesn't",
        "doing",
        "don't",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "hadn't",
        "has",
        "hasn't",
        "have",
        "haven't",
        "having",
        "he",
        "he'd",
        "he'll",
        "he's",
        "her",
        "here",
        "here's",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "how's",
        "i",
        "i'd",
        "i'll",
        "i'm",
        "i've",
        "if",
        "in",
        "into",
        "is",
        "isn't",
        "it",
        "it's",
        "its",
        "itself",
        "let's",
        "me",
        "more",
        "most",
        "mustn't",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shan't",
        "she",
        "she'd",
        "she'll",
        "she's",
        "should",
        "shouldn't",
        "so",
        "some",
        "such",
        "than",
        "that",
        "that's",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "there's",
        "these",
        "they",
        "they'd",
        "they'll",
        "they're",
        "they've",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "wasn't",
        "we",
        "we'd",
        "we'll",
        "we're",
        "we've",
        "were",
        "weren't",
        "what",
        "what's",
        "when",
        "when's",
        "where",
        "where's",
        "which",
        "while",
        "who",
        "who's",
        "whom",
        "why",
        "why's",
        "with",
        "won't",
        "would",
        "wouldn't",
        "you",
        "you'd",
        "you'll",
        "you're",
        "you've",
        "your",
        "yours",
        "yourself",
        "yourselves",
    )
)


def tokenize(text: str) -> list[str]:
    """Split ``text`` into the canonical term sequence.

    NFKC normalisation runs first so that a full-width form of ``4.2`` becomes
    the ASCII ``4.2``; lowercasing then makes matching case-insensitive. The
    allowed intra-word punctuation is kept so identifiers survive as single
    terms.
    """
    normalised = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(normalised):
        token = raw.rstrip(_TRAILING_NOISE)
        if token:
            tokens.append(token)
    return tokens


def tokenize_for_index(text: str) -> list[str]:
    """Index-time analysis: :func:`tokenize` with stopwords removed."""
    return [token for token in tokenize(text) if token not in STOPWORDS]


def normalize_query(text: str) -> list[str]:
    """Query-time analysis: identical to :func:`tokenize_for_index`.

    The two paths are deliberately the same function of the text, including the
    stopword removal. An earlier version fell back to the unfiltered tokens for
    a stopword-only query, which made the query analyser *different* from the
    index analyser and forced every caller to re-filter. A stopword-only query
    now yields no terms, and the lexical retriever reports that honestly as
    ``empty_query_terms`` rather than searching for terms the index cannot hold.
    """
    return tokenize_for_index(text)


def term_frequencies(tokens: Iterable[str]) -> dict[str, int]:
    """Count occurrences of each term, preserving no particular order."""
    return dict(Counter(tokens))
