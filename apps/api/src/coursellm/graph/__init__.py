"""Knowledge-graph models, traversal and extraction.

The package is deliberately split so that import order cannot cycle:

* :mod:`coursellm.graph.schemas` depends on nothing in the application and is
  imported by the ORM models (the relation enum is part of the schema).
* :mod:`coursellm.graph.confidence` is pure and depends only on the standard
  library, so the confidence model is testable without a database or a gateway.
* :mod:`coursellm.graph.repository` and :mod:`coursellm.graph.extraction` depend
  on the ORM and are imported lazily by callers.

Nothing is re-exported here on purpose: a package ``__init__`` that eagerly
imports the repository would make ``db.models.graph`` (which imports
``graph.schemas``) import the repository, which imports ``db.models.graph``.
"""
