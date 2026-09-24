"""The curated resource catalogue: real entries only, idempotently seeded.

## The rule this module exists to obey

**No fabricated metadata.** Every :class:`SeedResource` below names a resource
that genuinely exists, at a canonical publisher, institution, project or
preprint URL. There is no generated URL, no guessed deep link and no invented
author. When a fact could not be stated with confidence it is left empty — an
unknown author is an empty ``authors`` tuple, an unknown year is ``None``, and a
rating is always ``None`` because this project has measured nothing. A short,
true catalogue is worth more than a long, plausible one, and a fabricated URL in
a recommendation is the worst defect the feature could ship.

Each seeded URL was checked as reachable before it was added, and
``tests/unit/test_seed_catalogue_integrity.py`` re-checks the structural rules:
unique URLs, ``https`` only, every host on :data:`SOURCE_TRUST_BY_DOMAIN`, and a
trust level that the host can legitimately carry.

## Trust is a property of the host

``SOURCE_TRUST_BY_DOMAIN`` is the allowlist *and* the trust decision in one
mapping. ``official`` is used only for a project's or institution's own
documentation domain (``docs.python.org``, ``postgresql.org``, ``pytorch.org``,
``redis.io``, ``rfc-editor.org`` …). ``academic`` is used only for universities,
publishers and preprint servers (``mit.edu``, ``stanford.edu``, ``arxiv.org``,
``springer.com``, ``manning.com`` …). Platforms and free community material
(``coursera.org``, ``khanacademy.org``, ``freecodecamp.org``) are ``community``.
Nothing is ``secondary``: that level is reserved for pages fetched at request
time, which this PR deliberately does not do.

## The shared concept vocabulary

:data:`CONCEPT_SLUGS` is the published vocabulary the catalogue is keyed on.
``resource_concepts.concept_slug`` matches ``concepts.slug`` by value, so graph
extraction and review can align a tenant's extracted concepts to the catalogue
without a cross-tenant foreign key.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.db.models.resource import Resource, ResourceConcept
from coursellm.recommend.schemas import ResourceType, SourceTrust

_OFFICIAL = SourceTrust.OFFICIAL
_ACADEMIC = SourceTrust.ACADEMIC
_COMMUNITY = SourceTrust.COMMUNITY

#: The domain allowlist and the trust each domain can carry. A host matches a key
#: when it equals the key or is a subdomain of it (``docs.python.org`` matches
#: ``python.org``). A URL whose host matches no key is rejected by the seed
#: integrity test, so adding a resource means making a deliberate trust decision.
SOURCE_TRUST_BY_DOMAIN: dict[str, SourceTrust] = {
    # -- projects' and institutions' own documentation ---------------------
    "python.org": _OFFICIAL,
    "postgresql.org": _OFFICIAL,
    "pytorch.org": _OFFICIAL,
    "scikit-learn.org": _OFFICIAL,
    "numpy.org": _OFFICIAL,
    "pydata.org": _OFFICIAL,
    "redis.io": _OFFICIAL,
    "docker.com": _OFFICIAL,
    "git-scm.com": _OFFICIAL,
    "developer.mozilla.org": _OFFICIAL,
    "rfc-editor.org": _OFFICIAL,
    # -- universities, publishers and preprints -----------------------------
    "mit.edu": _ACADEMIC,
    "stanford.edu": _ACADEMIC,
    "cs.cmu.edu": _ACADEMIC,
    "cs61a.org": _ACADEMIC,
    "cs186berkeley.net": _ACADEMIC,
    "arxiv.org": _ACADEMIC,
    "springer.com": _ACADEMIC,
    "manning.com": _ACADEMIC,
    "openstax.org": _ACADEMIC,
    "usenix.org": _ACADEMIC,
    "mlr.press": _ACADEMIC,
    # -- platforms and free community material ------------------------------
    "coursera.org": _COMMUNITY,
    "khanacademy.org": _COMMUNITY,
    "freecodecamp.org": _COMMUNITY,
}

#: The published concept vocabulary. ``resource_concepts.concept_slug`` must be
#: one of these, and graph extraction/review aligns its slugs to this list.
CONCEPT_SLUGS: tuple[str, ...] = (
    "acid",
    "algorithm-analysis",
    "algorithms",
    "attention-mechanism",
    "autoencoders",
    "backpropagation",
    "bayesian-methods",
    "bias-variance-tradeoff",
    "caching",
    "classification",
    "clustering",
    "cloud-computing",
    "computer-science-theory",
    "consensus",
    "containerization",
    "convolutional-neural-networks",
    "cross-validation",
    "data-analysis",
    "data-structures",
    "data-versioning",
    "database-indexing",
    "database-systems",
    "decision-trees",
    "deep-learning",
    "dimensionality-reduction",
    "discrete-mathematics",
    "distributed-systems",
    "docker",
    "dynamic-programming",
    "eigenvalues",
    "ensemble-methods",
    "experiment-tracking",
    "feature-engineering",
    "generative-adversarial-networks",
    "gradient-boosting",
    "gradient-descent",
    "graph-algorithms",
    "http",
    "hyperparameter-tuning",
    "hypothesis-testing",
    "information-retrieval",
    "information-theory",
    "kubernetes",
    "language-models",
    "linear-algebra",
    "logistic-regression",
    "machine-learning",
    "matrix-decomposition",
    "message-queues",
    "mlops",
    "model-deployment",
    "model-evaluation",
    "model-monitoring",
    "natural-language-processing",
    "neural-networks",
    "normalization",
    "object-oriented-programming",
    "optimization",
    "principal-component-analysis",
    "probability",
    "probability-distributions",
    "probabilistic-graphical-models",
    "python",
    "python-asyncio",
    "query-optimization",
    "random-forests",
    "recurrent-neural-networks",
    "regression",
    "regularization",
    "reinforcement-learning",
    "relational-model",
    "replication",
    "representation-learning",
    "reproducibility",
    "scientific-computing",
    "sequence-to-sequence",
    "sharding",
    "single-variable-calculus",
    "software-engineering",
    "software-testing",
    "sql",
    "statistical-inference",
    "statistics",
    "storage-engines",
    "supervised-learning",
    "support-vector-machines",
    "tokenization",
    "transactions",
    "transfer-learning",
    "transformers",
    "version-control",
    "web-standards",
    "word-embeddings",
)

#: Lower bound for a plausible publication year, used by the integrity test.
MIN_RESOURCE_YEAR = 1800


@dataclass(frozen=True, slots=True)
class SeedResource:
    """One catalogue entry as authored, before it becomes a ``resources`` row."""

    title: str
    authors: tuple[str, ...]
    publisher: str | None
    year: int | None
    url: str
    resource_type: ResourceType
    provider: str
    trust: SourceTrust
    difficulty: int
    description: str
    concept_slugs: tuple[str, ...]
    is_free: bool = True


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What one seeding run did. ``skipped`` is the idempotency signal."""

    total: int
    inserted: int
    skipped: int
    concept_links_inserted: int


# ---------------------------------------------------------------------------
# The catalogue. 50 entries, every one a resource that exists.
# ---------------------------------------------------------------------------
SEED_CATALOGUE: tuple[SeedResource, ...] = (
    # -- mathematics foundations -------------------------------------------
    SeedResource(
        title="Linear Algebra",
        authors=("Gilbert Strang",),
        publisher="MIT OpenCourseWare",
        year=2010,
        url="https://ocw.mit.edu/courses/18-06-linear-algebra-spring-2010/",
        resource_type=ResourceType.COURSE,
        provider="MIT OpenCourseWare",
        trust=_ACADEMIC,
        difficulty=2,
        description=(
            "MIT 18.06 with Gilbert Strang: elimination and matrix factorisations, "
            "vector spaces, eigenvalues and positive definite matrices, with problem "
            "sets and exams."
        ),
        concept_slugs=("linear-algebra", "matrix-decomposition", "eigenvalues"),
    ),
    SeedResource(
        title="Introduction to Probability and Statistics",
        authors=(),
        publisher="MIT OpenCourseWare",
        year=2022,
        url=(
            "https://ocw.mit.edu/courses/"
            "18-05-introduction-to-probability-and-statistics-spring-2022/"
        ),
        resource_type=ResourceType.COURSE,
        provider="MIT OpenCourseWare",
        trust=_ACADEMIC,
        difficulty=2,
        description=(
            "MIT 18.05: counting and conditional probability, random variables and "
            "distributions, Bayesian inference, confidence intervals and hypothesis "
            "testing, with problem sets."
        ),
        concept_slugs=(
            "probability",
            "probability-distributions",
            "statistics",
            "hypothesis-testing",
        ),
    ),
    SeedResource(
        title="Matrix Methods in Data Analysis, Signal Processing, and Machine Learning",
        authors=("Gilbert Strang",),
        publisher="MIT OpenCourseWare",
        year=2018,
        url=(
            "https://ocw.mit.edu/courses/"
            "18-065-matrix-methods-in-data-analysis-signal-processing-and-machine-learning-spring-2018/"
        ),
        resource_type=ResourceType.COURSE,
        provider="MIT OpenCourseWare",
        trust=_ACADEMIC,
        difficulty=3,
        description=(
            "MIT 18.065 connects linear algebra to least squares, the SVD, principal "
            "component analysis, covariance matrices and the linear algebra behind "
            "deep learning."
        ),
        concept_slugs=(
            "linear-algebra",
            "matrix-decomposition",
            "principal-component-analysis",
            "machine-learning",
        ),
    ),
    SeedResource(
        title="Mathematics for Computer Science",
        authors=(),
        publisher="MIT OpenCourseWare",
        year=2015,
        url="https://ocw.mit.edu/courses/6-042j-mathematics-for-computer-science-spring-2015/",
        resource_type=ResourceType.COURSE,
        provider="MIT OpenCourseWare",
        trust=_ACADEMIC,
        difficulty=3,
        description=(
            "MIT 6.042J: proofs, sets and relations, graph theory, counting, discrete "
            "probability and recurrences — the discrete mathematics underpinning "
            "algorithm analysis."
        ),
        concept_slugs=("discrete-mathematics", "probability", "computer-science-theory"),
    ),
    SeedResource(
        title="Linear Algebra",
        authors=(),
        publisher="Khan Academy",
        year=None,
        url="https://www.khanacademy.org/math/linear-algebra",
        resource_type=ResourceType.TUTORIAL,
        provider="Khan Academy",
        trust=_COMMUNITY,
        difficulty=1,
        description=(
            "Khan Academy's linear algebra sequence: vectors and spaces, matrix "
            "transformations, determinants, eigenvalues and eigenvectors, with "
            "worked practice."
        ),
        concept_slugs=("linear-algebra", "matrix-decomposition", "eigenvalues"),
    ),
    SeedResource(
        title="Calculus, Volume 1",
        authors=("Gilbert Strang", 'Edwin "Jed" Herman'),
        publisher="OpenStax",
        year=2016,
        url="https://openstax.org/details/books/calculus-volume-1",
        resource_type=ResourceType.BOOK,
        provider="OpenStax",
        trust=_ACADEMIC,
        difficulty=2,
        description=(
            "OpenStax Calculus Volume 1: limits and continuity, differentiation and "
            "its applications, integration and the fundamental theorem of calculus. "
            "Freely available under an open licence."
        ),
        concept_slugs=("single-variable-calculus",),
    ),
    SeedResource(
        title="Introductory Statistics 2e",
        authors=("Barbara Illowsky", "Susan Dean"),
        publisher="OpenStax",
        year=2023,
        url="https://openstax.org/details/books/introductory-statistics-2e",
        resource_type=ResourceType.BOOK,
        provider="OpenStax",
        trust=_ACADEMIC,
        difficulty=2,
        description=(
            "OpenStax Introductory Statistics: descriptive statistics, probability "
            "distributions, sampling distributions, confidence intervals and "
            "hypothesis testing with worked examples."
        ),
        concept_slugs=(
            "statistics",
            "probability",
            "probability-distributions",
            "hypothesis-testing",
            "statistical-inference",
        ),
    ),
    SeedResource(
        title="Linear Algebra Done Right",
        authors=("Sheldon Axler",),
        publisher="Springer",
        year=2024,
        url="https://link.springer.com/book/10.1007/978-3-031-41026-0",
        resource_type=ResourceType.BOOK,
        provider="Springer",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "Sheldon Axler's proof-first linear algebra text: vector spaces, linear "
            "maps, eigenvalues, inner product spaces and the spectral theorem."
        ),
        concept_slugs=("linear-algebra", "eigenvalues", "matrix-decomposition"),
        is_free=False,
    ),
    # -- programming --------------------------------------------------------
    SeedResource(
        title="The Python Tutorial",
        authors=(),
        publisher="Python Software Foundation",
        year=None,
        url="https://docs.python.org/3/tutorial/",
        resource_type=ResourceType.DOCUMENTATION,
        provider="Python Software Foundation",
        trust=_OFFICIAL,
        difficulty=1,
        description=(
            "The official Python tutorial: data structures, control flow, modules, "
            "classes, exceptions, virtual environments and the standard library."
        ),
        concept_slugs=("python", "data-structures", "object-oriented-programming"),
    ),
    SeedResource(
        title="asyncio — Asynchronous I/O",
        authors=(),
        publisher="Python Software Foundation",
        year=None,
        url="https://docs.python.org/3/library/asyncio.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="Python Software Foundation",
        trust=_OFFICIAL,
        difficulty=3,
        description=(
            "The official asyncio reference: the event loop, coroutines and tasks, "
            "futures, streams and synchronisation primitives for concurrent Python."
        ),
        concept_slugs=("python", "python-asyncio"),
    ),
    SeedResource(
        title="unittest — Unit testing framework",
        authors=(),
        publisher="Python Software Foundation",
        year=None,
        url="https://docs.python.org/3/library/unittest.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="Python Software Foundation",
        trust=_OFFICIAL,
        difficulty=2,
        description=(
            "The official unittest documentation: test cases, fixtures, assertions "
            "and test discovery for the Python standard library testing framework."
        ),
        concept_slugs=("python", "software-testing"),
    ),
    SeedResource(
        title="Scientific Computing with Python",
        authors=(),
        publisher="freeCodeCamp",
        year=None,
        url="https://www.freecodecamp.org/learn/scientific-computing-with-python/",
        resource_type=ResourceType.TUTORIAL,
        provider="freeCodeCamp",
        trust=_COMMUNITY,
        difficulty=1,
        description=(
            "freeCodeCamp's Scientific Computing with Python certification: Python "
            "fundamentals, data structures, algorithms and five required projects."
        ),
        concept_slugs=("python", "algorithms", "data-structures"),
    ),
    SeedResource(
        title="Introduction to Algorithms",
        authors=(),
        publisher="MIT OpenCourseWare",
        year=2020,
        url="https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-spring-2020/",
        resource_type=ResourceType.COURSE,
        provider="MIT OpenCourseWare",
        trust=_ACADEMIC,
        difficulty=3,
        description=(
            "MIT 6.006: asymptotic analysis, sorting, hashing, binary trees, heaps, "
            "graphs and shortest paths, dynamic programming, with problem sets."
        ),
        concept_slugs=(
            "algorithms",
            "data-structures",
            "algorithm-analysis",
            "dynamic-programming",
        ),
    ),
    SeedResource(
        title="Design and Analysis of Algorithms",
        authors=(),
        publisher="MIT OpenCourseWare",
        year=2015,
        url="https://ocw.mit.edu/courses/6-046j-design-and-analysis-of-algorithms-spring-2015/",
        resource_type=ResourceType.COURSE,
        provider="MIT OpenCourseWare",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "MIT 6.046J: divide and conquer, randomised algorithms, amortised "
            "analysis, graph algorithms, dynamic programming and complexity."
        ),
        concept_slugs=(
            "algorithms",
            "algorithm-analysis",
            "dynamic-programming",
            "graph-algorithms",
        ),
    ),
    SeedResource(
        title="Structure and Interpretation of Computer Programs",
        authors=(),
        publisher="University of California, Berkeley",
        year=None,
        url="https://cs61a.org/",
        resource_type=ResourceType.COURSE,
        provider="UC Berkeley",
        trust=_ACADEMIC,
        difficulty=3,
        description=(
            "UC Berkeley CS 61A: abstraction, recursion, higher-order functions, "
            "data structures, object-oriented programming and interpreters, with "
            "projects and exams."
        ),
        concept_slugs=("python", "data-structures", "object-oriented-programming"),
    ),
    SeedResource(
        title="Pro Git",
        authors=("Scott Chacon", "Ben Straub"),
        publisher="Apress",
        year=2014,
        url="https://git-scm.com/book/en/v2",
        resource_type=ResourceType.BOOK,
        provider="Git project",
        trust=_OFFICIAL,
        difficulty=2,
        description=(
            "Pro Git, the official Git book: repositories, branching and merging, "
            "remotes, distributed workflows and internals. Free to read online."
        ),
        concept_slugs=("version-control", "software-engineering"),
    ),
    # -- databases ----------------------------------------------------------
    SeedResource(
        title="PostgreSQL Tutorial",
        authors=(),
        publisher="PostgreSQL Global Development Group",
        year=None,
        url="https://www.postgresql.org/docs/current/tutorial.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="PostgreSQL Global Development Group",
        trust=_OFFICIAL,
        difficulty=1,
        description=(
            "The official PostgreSQL tutorial: creating and querying tables, joins, "
            "aggregates, updates, foreign keys and transactions."
        ),
        concept_slugs=("sql", "relational-model", "database-systems"),
    ),
    SeedResource(
        title="Indexes",
        authors=(),
        publisher="PostgreSQL Global Development Group",
        year=None,
        url="https://www.postgresql.org/docs/current/indexes.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="PostgreSQL Global Development Group",
        trust=_OFFICIAL,
        difficulty=3,
        description=(
            "The official PostgreSQL chapter on indexes: B-tree, hash, GiST, SP-GiST, "
            "GIN and BRIN index types, multicolumn and partial indexes, and the "
            "planner's use of them."
        ),
        concept_slugs=("database-indexing", "query-optimization", "sql"),
    ),
    SeedResource(
        title="Transactions",
        authors=(),
        publisher="PostgreSQL Global Development Group",
        year=None,
        url="https://www.postgresql.org/docs/current/transactions.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="PostgreSQL Global Development Group",
        trust=_OFFICIAL,
        difficulty=3,
        description=(
            "The official PostgreSQL transaction documentation: transaction "
            "isolation levels, serialization failures, savepoints and read-only "
            "transactions."
        ),
        concept_slugs=("transactions", "acid", "sql"),
    ),
    SeedResource(
        title="Introduction to Database Systems",
        authors=(),
        publisher="University of California, Berkeley",
        year=None,
        url="https://cs186berkeley.net/",
        resource_type=ResourceType.COURSE,
        provider="UC Berkeley",
        trust=_ACADEMIC,
        difficulty=3,
        description=(
            "UC Berkeley CS 186: the relational model and SQL, query optimization, "
            "indexing, transactions and recovery, concurrency control and "
            "distributed data."
        ),
        concept_slugs=(
            "database-systems",
            "sql",
            "transactions",
            "storage-engines",
            "query-optimization",
        ),
    ),
    SeedResource(
        title="Database Systems",
        authors=("Andy Pavlo",),
        publisher="Carnegie Mellon University",
        year=None,
        url="https://15445.courses.cs.cmu.edu/",
        resource_type=ResourceType.COURSE,
        provider="Carnegie Mellon University",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "CMU 15-445/645 with Andy Pavlo: database internals — storage engines, "
            "buffer pools, indexing, query execution and optimization, concurrency "
            "control and recovery."
        ),
        concept_slugs=(
            "database-systems",
            "storage-engines",
            "query-optimization",
            "transactions",
        ),
    ),
    SeedResource(
        title="Relational Database",
        authors=(),
        publisher="freeCodeCamp",
        year=None,
        url="https://www.freecodecamp.org/learn/relational-database/",
        resource_type=ResourceType.TUTORIAL,
        provider="freeCodeCamp",
        trust=_COMMUNITY,
        difficulty=1,
        description=(
            "freeCodeCamp's Relational Database certification: Bash, SQL and "
            "PostgreSQL, database normalization, and building a relational database "
            "from scratch."
        ),
        concept_slugs=("sql", "relational-model", "database-systems", "normalization"),
    ),
    SeedResource(
        title="Redis Documentation: Develop with Redis",
        authors=(),
        publisher="Redis",
        year=None,
        url="https://redis.io/docs/latest/develop/",
        resource_type=ResourceType.DOCUMENTATION,
        provider="Redis",
        trust=_OFFICIAL,
        difficulty=2,
        description=(
            "The official Redis developer documentation: data types, keys and "
            "expiry, pub/sub, streams, transactions and client-side caching."
        ),
        concept_slugs=("caching", "message-queues"),
    ),
    # -- classical machine learning ----------------------------------------
    SeedResource(
        title="scikit-learn User Guide",
        authors=(),
        publisher="scikit-learn developers",
        year=None,
        url="https://scikit-learn.org/stable/user_guide.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="scikit-learn developers",
        trust=_OFFICIAL,
        difficulty=2,
        description=(
            "The official scikit-learn user guide: supervised and unsupervised "
            "learning, model selection, pipelines, preprocessing and feature "
            "extraction."
        ),
        concept_slugs=(
            "machine-learning",
            "supervised-learning",
            "classification",
            "regression",
            "clustering",
            "principal-component-analysis",
        ),
    ),
    SeedResource(
        title="Model Evaluation and Metrics",
        authors=(),
        publisher="scikit-learn developers",
        year=None,
        url="https://scikit-learn.org/stable/modules/model_evaluation.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="scikit-learn developers",
        trust=_OFFICIAL,
        difficulty=3,
        description=(
            "The official scikit-learn guide to scoring: cross-validation, "
            "classification and regression metrics, threshold tuning and evaluating "
            "model output."
        ),
        concept_slugs=("model-evaluation", "cross-validation", "hyperparameter-tuning"),
    ),
    SeedResource(
        title="XGBoost: A Scalable Tree Boosting System",
        authors=("Tianqi Chen", "Carlos Guestrin"),
        publisher="arXiv",
        year=2016,
        url="https://arxiv.org/abs/1603.02754",
        resource_type=ResourceType.PAPER,
        provider="arXiv",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "The XGBoost paper: a regularised, sparsity-aware tree-boosting system "
            "with a weighted quantile sketch and cache-aware block structure."
        ),
        concept_slugs=(
            "gradient-boosting",
            "decision-trees",
            "ensemble-methods",
            "machine-learning",
        ),
    ),
    SeedResource(
        title="CS229: Machine Learning",
        authors=(),
        publisher="Stanford University",
        year=None,
        url="https://cs229.stanford.edu/",
        resource_type=ResourceType.COURSE,
        provider="Stanford University",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "Stanford CS229: supervised learning, generative and discriminative "
            "models, kernel methods, learning theory, reinforcement learning and "
            "advice on applying machine learning."
        ),
        concept_slugs=(
            "machine-learning",
            "supervised-learning",
            "regression",
            "classification",
            "bias-variance-tradeoff",
            "reinforcement-learning",
        ),
    ),
    SeedResource(
        title="The Elements of Statistical Learning",
        authors=("Trevor Hastie", "Robert Tibshirani", "Jerome Friedman"),
        publisher="Springer",
        year=2009,
        url="https://link.springer.com/book/10.1007/978-0-387-84858-7",
        resource_type=ResourceType.BOOK,
        provider="Springer",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "Hastie, Tibshirani and Friedman's reference on statistical learning: "
            "linear and logistic regression, regularization, trees and boosting, "
            "support vector machines, neural networks and model assessment."
        ),
        concept_slugs=(
            "machine-learning",
            "statistical-inference",
            "regression",
            "regularization",
            "ensemble-methods",
            "model-evaluation",
        ),
        is_free=False,
    ),
    SeedResource(
        title="Pattern Recognition and Machine Learning",
        authors=("Christopher M. Bishop",),
        publisher="Springer",
        year=2006,
        url="https://link.springer.com/book/10.1007/978-0-387-45528-0",
        resource_type=ResourceType.BOOK,
        provider="Springer",
        trust=_ACADEMIC,
        difficulty=5,
        description=(
            "Bishop's Bayesian treatment of pattern recognition: probability "
            "distributions, linear models for regression and classification, neural "
            "networks, kernel methods, graphical models and approximate inference."
        ),
        concept_slugs=(
            "machine-learning",
            "bayesian-methods",
            "probabilistic-graphical-models",
            "classification",
        ),
        is_free=False,
    ),
    # -- deep learning and NLP ---------------------------------------------
    SeedResource(
        title="Deep Learning with Python, Second Edition",
        authors=("François Chollet",),
        publisher="Manning Publications",
        year=2021,
        url="https://www.manning.com/books/deep-learning-with-python-second-edition",
        resource_type=ResourceType.BOOK,
        provider="Manning Publications",
        trust=_ACADEMIC,
        difficulty=3,
        description=(
            "François Chollet's practical introduction to deep learning with Keras: "
            "tensors and gradients, convolutional and recurrent networks, sequence "
            "models and the Keras workflow."
        ),
        concept_slugs=(
            "deep-learning",
            "neural-networks",
            "python",
            "convolutional-neural-networks",
        ),
        is_free=False,
    ),
    SeedResource(
        title="CS231n: Deep Learning for Computer Vision",
        authors=(),
        publisher="Stanford University",
        year=None,
        url="https://cs231n.stanford.edu/",
        resource_type=ResourceType.COURSE,
        provider="Stanford University",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "Stanford CS231n: convolutional networks for visual recognition — "
            "backpropagation, training dynamics, architectures, detection, "
            "segmentation and generative models."
        ),
        concept_slugs=(
            "deep-learning",
            "convolutional-neural-networks",
            "neural-networks",
            "backpropagation",
        ),
    ),
    SeedResource(
        title="CS224n: Natural Language Processing with Deep Learning",
        authors=("Christopher Manning",),
        publisher="Stanford University",
        year=None,
        url="https://web.stanford.edu/class/cs224n/",
        resource_type=ResourceType.COURSE,
        provider="Stanford University",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "Stanford CS224n with Christopher Manning: word vectors, recurrent and "
            "transformer networks, pretraining, question answering and natural "
            "language generation."
        ),
        concept_slugs=(
            "natural-language-processing",
            "deep-learning",
            "transformers",
            "word-embeddings",
        ),
    ),
    SeedResource(
        title="Attention Is All You Need",
        authors=(
            "Ashish Vaswani",
            "Noam Shazeer",
            "Niki Parmar",
            "Jakob Uszkoreit",
            "Llion Jones",
            "Aidan N. Gomez",
            "Łukasz Kaiser",
            "Illia Polosukhin",
        ),
        publisher="arXiv",
        year=2017,
        url="https://arxiv.org/abs/1706.03762",
        resource_type=ResourceType.PAPER,
        provider="arXiv",
        trust=_ACADEMIC,
        difficulty=5,
        description=(
            "The transformer paper: self-attention replaces recurrence and "
            "convolution, with multi-head attention, positional encodings and "
            "state-of-the-art machine translation."
        ),
        concept_slugs=(
            "transformers",
            "attention-mechanism",
            "natural-language-processing",
            "sequence-to-sequence",
        ),
    ),
    SeedResource(
        title="BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        authors=("Jacob Devlin", "Ming-Wei Chang", "Kenton Lee", "Kristina Toutanova"),
        publisher="arXiv",
        year=2019,
        url="https://arxiv.org/abs/1810.04805",
        resource_type=ResourceType.PAPER,
        provider="arXiv",
        trust=_ACADEMIC,
        difficulty=5,
        description=(
            "BERT: masked-language-model pretraining of deep bidirectional "
            "transformers, then fine-tuning for downstream natural language "
            "understanding tasks."
        ),
        concept_slugs=(
            "transformers",
            "language-models",
            "transfer-learning",
            "natural-language-processing",
        ),
    ),
    SeedResource(
        title="Deep Residual Learning for Image Recognition",
        authors=("Kaiming He", "Xiangyu Zhang", "Shaoqing Ren", "Jian Sun"),
        publisher="arXiv",
        year=2015,
        url="https://arxiv.org/abs/1512.03385",
        resource_type=ResourceType.PAPER,
        provider="arXiv",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "ResNet: residual connections that make very deep convolutional "
            "networks trainable, with the 152-layer architecture that won ILSVRC "
            "2015."
        ),
        concept_slugs=("convolutional-neural-networks", "deep-learning", "neural-networks"),
    ),
    SeedResource(
        title="Adam: A Method for Stochastic Optimization",
        authors=("Diederik P. Kingma", "Jimmy Ba"),
        publisher="arXiv",
        year=2014,
        url="https://arxiv.org/abs/1412.6980",
        resource_type=ResourceType.PAPER,
        provider="arXiv",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "The Adam optimiser: adaptive per-parameter learning rates from "
            "first and second moment estimates, with bias correction and an "
            "efficient implementation."
        ),
        concept_slugs=("gradient-descent", "optimization", "deep-learning"),
    ),
    SeedResource(
        title=(
            "Batch Normalization: Accelerating Deep Network Training by "
            "Reducing Internal Covariate Shift"
        ),
        authors=("Sergey Ioffe", "Christian Szegedy"),
        publisher="arXiv",
        year=2015,
        url="https://arxiv.org/abs/1502.03167",
        resource_type=ResourceType.PAPER,
        provider="arXiv",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "Batch normalization: normalising layer inputs per mini-batch to "
            "stabilise and accelerate training, allowing higher learning rates and "
            "less careful initialisation."
        ),
        concept_slugs=("deep-learning", "neural-networks", "regularization"),
    ),
    SeedResource(
        title="Sequence to Sequence Learning with Neural Networks",
        authors=("Ilya Sutskever", "Oriol Vinyals", "Quoc V. Le"),
        publisher="arXiv",
        year=2014,
        url="https://arxiv.org/abs/1409.3215",
        resource_type=ResourceType.PAPER,
        provider="arXiv",
        trust=_ACADEMIC,
        difficulty=5,
        description=(
            "Sequence-to-sequence learning: an encoder LSTM maps a sequence to a "
            "fixed-dimensional vector and a decoder LSTM generates the output, with "
            "reversal and ensembling for machine translation."
        ),
        concept_slugs=(
            "sequence-to-sequence",
            "recurrent-neural-networks",
            "natural-language-processing",
        ),
    ),
    SeedResource(
        title="Efficient Estimation of Word Representations in Vector Space",
        authors=("Tomas Mikolov", "Kai Chen", "Greg Corrado", "Jeffrey Dean"),
        publisher="arXiv",
        year=2013,
        url="https://arxiv.org/abs/1301.3781",
        resource_type=ResourceType.PAPER,
        provider="arXiv",
        trust=_ACADEMIC,
        difficulty=3,
        description=(
            "Word2Vec: the skip-gram and continuous bag-of-words architectures that "
            "learn distributed word representations from large corpora."
        ),
        concept_slugs=("word-embeddings", "natural-language-processing", "representation-learning"),
    ),
    SeedResource(
        title="Understanding the difficulty of training deep feedforward neural networks",
        authors=("Xavier Glorot", "Yoshua Bengio"),
        publisher="PMLR",
        year=2010,
        url="https://proceedings.mlr.press/v9/glorot10a.html",
        resource_type=ResourceType.PAPER,
        provider="PMLR",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "The Xavier/Glorot initialisation paper: why standard initialisation "
            "hinders deep feedforward networks, and how scaled initialisation and "
            "activation choice keep signal variance stable."
        ),
        concept_slugs=("neural-networks", "backpropagation", "deep-learning", "gradient-descent"),
    ),
    SeedResource(
        title="Machine Learning",
        authors=("Andrew Ng",),
        publisher="Coursera",
        year=None,
        url="https://www.coursera.org/learn/machine-learning",
        resource_type=ResourceType.MOOC,
        provider="Coursera",
        trust=_COMMUNITY,
        difficulty=2,
        description=(
            "Andrew Ng's introductory machine learning course: linear and logistic "
            "regression, regularization, neural networks, advice for applying "
            "machine learning and anomaly detection."
        ),
        concept_slugs=(
            "machine-learning",
            "supervised-learning",
            "regression",
            "classification",
            "regularization",
        ),
    ),
    SeedResource(
        title="Neural Networks and Deep Learning",
        authors=("Andrew Ng",),
        publisher="Coursera",
        year=None,
        url="https://www.coursera.org/learn/neural-networks-deep-learning",
        resource_type=ResourceType.MOOC,
        provider="Coursera",
        trust=_COMMUNITY,
        difficulty=2,
        description=(
            "The first course of the Deep Learning Specialization: logistic "
            "regression as a neural network, shallow and deep networks, "
            "backpropagation and practical implementation."
        ),
        concept_slugs=("neural-networks", "deep-learning", "backpropagation", "python"),
    ),
    SeedResource(
        title="PyTorch Tutorials",
        authors=(),
        publisher="PyTorch Foundation",
        year=None,
        url="https://pytorch.org/tutorials/",
        resource_type=ResourceType.DOCUMENTATION,
        provider="PyTorch Foundation",
        trust=_OFFICIAL,
        difficulty=3,
        description=(
            "The official PyTorch tutorials: tensors and autograd, training a "
            "classifier, data loading, optimization and deployment of trained "
            "models."
        ),
        concept_slugs=("deep-learning", "neural-networks", "python", "backpropagation"),
    ),
    SeedResource(
        title="NumPy User Guide",
        authors=(),
        publisher="NumPy developers",
        year=None,
        url="https://numpy.org/doc/stable/user/index.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="NumPy developers",
        trust=_OFFICIAL,
        difficulty=2,
        description=(
            "The official NumPy user guide: ndarrays, broadcasting, indexing, "
            "linear algebra, random sampling and the absolute-beginners guide."
        ),
        concept_slugs=("python", "scientific-computing", "linear-algebra"),
    ),
    SeedResource(
        title="pandas User Guide",
        authors=(),
        publisher="pandas developers",
        year=None,
        url="https://pandas.pydata.org/docs/user_guide/index.html",
        resource_type=ResourceType.DOCUMENTATION,
        provider="pandas developers",
        trust=_OFFICIAL,
        difficulty=2,
        description=(
            "The official pandas user guide: Series and DataFrame data structures, "
            "indexing, reshaping, grouping, merging and time series."
        ),
        concept_slugs=("python", "data-analysis", "scientific-computing"),
    ),
    SeedResource(
        title="Speech and Language Processing (3rd edition draft)",
        authors=("Daniel Jurafsky", "James H. Martin"),
        publisher="Stanford University",
        year=2024,
        url="https://web.stanford.edu/~jurafsky/slp3/",
        resource_type=ResourceType.BOOK,
        provider="Stanford University",
        trust=_ACADEMIC,
        difficulty=4,
        description=(
            "Jurafsky and Martin's NLP textbook: regular expressions and tokenization, "
            "language models, part-of-speech tagging, parsing, information retrieval, "
            "word embeddings and transformers."
        ),
        concept_slugs=(
            "natural-language-processing",
            "language-models",
            "tokenization",
            "information-retrieval",
        ),
    ),
    # -- infrastructure and distributed systems -----------------------------
    SeedResource(
        title="Docker Get Started",
        authors=(),
        publisher="Docker Inc.",
        year=None,
        url="https://docs.docker.com/get-started/",
        resource_type=ResourceType.DOCUMENTATION,
        provider="Docker Inc.",
        trust=_OFFICIAL,
        difficulty=2,
        description=(
            "The official Docker getting-started guide: images and containers, "
            "Dockerfiles, volumes, networking and multi-container applications."
        ),
        concept_slugs=("docker", "containerization"),
    ),
    SeedResource(
        title="In Search of an Understandable Consensus Algorithm",
        authors=("Diego Ongaro", "John Ousterhout"),
        publisher="USENIX Association",
        year=2014,
        url="https://www.usenix.org/conference/atc14/technical-sessions/presentation/ongaro",
        resource_type=ResourceType.PAPER,
        provider="USENIX Association",
        trust=_ACADEMIC,
        difficulty=5,
        description=(
            "The Raft paper: a leader-based consensus algorithm decomposed into "
            "leader election, log replication and safety, presented as a more "
            "understandable alternative to Paxos."
        ),
        concept_slugs=("consensus", "distributed-systems", "replication"),
    ),
    SeedResource(
        title="RFC 9110: HTTP Semantics",
        authors=("Roy T. Fielding", "Mark Nottingham", "Julian Reschke"),
        publisher="RFC Editor",
        year=2022,
        url="https://www.rfc-editor.org/rfc/rfc9110.html",
        resource_type=ResourceType.STANDARD,
        provider="RFC Editor",
        trust=_OFFICIAL,
        difficulty=3,
        description=(
            "The current HTTP semantics standard: methods, status codes, headers, "
            "content negotiation, conditional requests, caching and authentication."
        ),
        concept_slugs=("http", "web-standards"),
    ),
    SeedResource(
        title="HTTP — MDN Web Docs",
        authors=(),
        publisher="Mozilla",
        year=None,
        url="https://developer.mozilla.org/en-US/docs/Web/HTTP",
        resource_type=ResourceType.DOCUMENTATION,
        provider="Mozilla",
        trust=_OFFICIAL,
        difficulty=2,
        description=(
            "MDN's guide to HTTP: requests and responses, methods, status codes, "
            "headers, cookies, caching, CORS and content security."
        ),
        concept_slugs=("http", "web-standards"),
    ),
)


def host_of(url: str) -> str:
    """The lowercase host of ``url``, or an empty string when there is none."""
    return (urlparse(url).hostname or "").lower()


def domain_for_host(host: str) -> str | None:
    """The allowlist key that governs ``host``, longest match first."""
    candidates = [
        domain for domain in SOURCE_TRUST_BY_DOMAIN if host == domain or host.endswith(f".{domain}")
    ]
    if not candidates:
        return None
    return max(candidates, key=len)


def trust_for_url(url: str) -> SourceTrust | None:
    """The trust level the allowlist assigns to ``url``'s host."""
    domain = domain_for_host(host_of(url))
    return None if domain is None else SOURCE_TRUST_BY_DOMAIN[domain]


async def _insert_resource(session: AsyncSession, entry: SeedResource) -> uuid.UUID | None:
    """Insert one resource on ``url``, returning its id or ``None`` if it existed.

    ``ON CONFLICT (url) DO NOTHING`` is what makes re-seeding idempotent and safe
    under concurrency, and the ``RETURNING`` clause distinguishes "inserted" from
    "already there" without a second query in the common case.
    """
    statement = (
        pg_insert(Resource)
        .values(
            title=entry.title,
            authors=list(entry.authors),
            publisher=entry.publisher,
            year=entry.year,
            url=entry.url,
            resource_type=entry.resource_type.value,
            provider=entry.provider,
            trust=entry.trust.value,
            difficulty=entry.difficulty,
            description=entry.description,
            duration_hours=None,
            is_free=entry.is_free,
            rating=None,
            is_verified=False,
            verified_at=None,
            notes=None,
        )
        .on_conflict_do_nothing(index_elements=["url"])
        .returning(Resource.id)
    )
    inserted_id: uuid.UUID | None = (await session.execute(statement)).scalar_one_or_none()
    return inserted_id


async def _existing_id(session: AsyncSession, url: str) -> uuid.UUID | None:
    statement = select(Resource.id).where(Resource.url == url)
    return (await session.execute(statement)).scalar_one_or_none()


async def _insert_concept_links(
    session: AsyncSession, resource_id: uuid.UUID, slugs: Sequence[str]
) -> int:
    """Add any missing ``(resource_id, concept_slug)`` rows; return how many."""
    if not slugs:
        return 0
    statement = (
        pg_insert(ResourceConcept)
        .values([{"resource_id": resource_id, "concept_slug": slug} for slug in slugs])
        .on_conflict_do_nothing(index_elements=["resource_id", "concept_slug"])
        .returning(ResourceConcept.concept_slug)
    )
    return len((await session.execute(statement)).scalars().all())


async def seed_catalogue(session: AsyncSession, *, only_missing: bool = True) -> SeedReport:
    """Seed the curated catalogue, idempotently, keyed on ``url``.

    An existing row's metadata is **never** overwritten: a re-run cannot silently
    replace a reviewed description, title or trust level with the seed file's
    copy. With ``only_missing`` (the default) an existing resource is skipped
    entirely. With ``only_missing=False`` the resource row is still left alone,
    but any concept links published by the seed that are not yet present are
    added, which is how the vocabulary can be extended without a data migration.
    """
    inserted = 0
    skipped = 0
    links = 0

    for entry in SEED_CATALOGUE:
        resource_id = await _insert_resource(session, entry)
        if resource_id is None:
            skipped += 1
            if not only_missing:
                existing_id = await _existing_id(session, entry.url)
                if existing_id is not None:
                    links += await _insert_concept_links(session, existing_id, entry.concept_slugs)
            continue
        inserted += 1
        links += await _insert_concept_links(session, resource_id, entry.concept_slugs)

    await session.flush()
    return SeedReport(
        total=len(SEED_CATALOGUE),
        inserted=inserted,
        skipped=skipped,
        concept_links_inserted=links,
    )


__all__ = [
    "CONCEPT_SLUGS",
    "MIN_RESOURCE_YEAR",
    "SEED_CATALOGUE",
    "SOURCE_TRUST_BY_DOMAIN",
    "SeedReport",
    "SeedResource",
    "domain_for_host",
    "host_of",
    "seed_catalogue",
    "trust_for_url",
]
