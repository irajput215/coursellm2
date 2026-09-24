"""Recommendations: a curated catalogue, pure ranking and deterministic explanation.

Four modules do the work and each has one job:

* :mod:`coursellm.recommend.catalogue` is the only reader of the global
  ``resources``/``resource_concepts`` tables.
* :mod:`coursellm.recommend.ranking` is pure: candidate rows plus gaps in, scored
  rows out, with every contribution returned so the result is explainable.
* :mod:`coursellm.recommend.explanation` composes "why this resource?" from those
  contributions with deterministic templates — no model call, so it cannot invent
  a justification the ranking never used.
* :mod:`coursellm.recommend.seed` holds the curated catalogue itself.

The package ``__init__`` deliberately imports nothing. ``coursellm.db.models``
imports :mod:`coursellm.recommend.schemas` for the stored enums, so if this module
eagerly imported the service it would create an import cycle at model-registration
time.
"""

from __future__ import annotations

__all__: list[str] = []
