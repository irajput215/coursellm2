"""Personalised learning roadmaps: planning, progress projection and adaptation.

This package is the deterministic core of PR 11. It owns three pure concerns:

* :mod:`coursellm.learning.planner` orders required concepts and estimates effort
  from concept difficulty and prerequisite fan-in;
* :mod:`coursellm.learning.progress` projects mastery, velocity, weak and stale
  concepts from the append-only progress log;
* :mod:`coursellm.learning.adaptation` revises a plan when measured progress
  invalidates it, without ever dropping completed work.

Persistence and HTTP live in :mod:`coursellm.services.roadmap` and
:mod:`coursellm.api.routers.roadmaps`, so the rules above are testable without a
database.
"""

from coursellm.learning.adaptation import adapt
from coursellm.learning.planner import build_roadmap, estimate_step_hours
from coursellm.learning.progress import (
    learning_velocity,
    project_mastery,
    stale_concepts,
    weak_concepts,
)
from coursellm.learning.schemas import (
    AdaptationResult,
    AdaptedStep,
    GoalUnresolvedError,
    PlannedStep,
    RoadmapCycleError,
    RoadmapPlan,
    StepEstimate,
    StepSnapshot,
)

__all__ = [
    "AdaptationResult",
    "AdaptedStep",
    "GoalUnresolvedError",
    "PlannedStep",
    "RoadmapCycleError",
    "RoadmapPlan",
    "StepEstimate",
    "StepSnapshot",
    "adapt",
    "build_roadmap",
    "estimate_step_hours",
    "learning_velocity",
    "project_mastery",
    "stale_concepts",
    "weak_concepts",
]
