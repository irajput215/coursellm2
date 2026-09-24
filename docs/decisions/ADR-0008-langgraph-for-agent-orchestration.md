# ADR-0008: LangGraph for agent orchestration

- **Status:** Accepted
- **Date:** 2026-01-01
- **Deciders:** CourseLLM engineering
- **Supersedes:** —
- **Related:** ADR-0001, ADR-0007, ADR-0010

## Context

The prototype's "agents" were independent classes and functions with no shared
state machine. `agents/planner_agent.py` constructs `EmailService`,
`CalendarService`, and `StudyPlanGenerator` and calls them in sequence; `agents/`
also contains `evaluation_agent.py`, `exam_agent.py`, `misconception_detector.py`,
`study_planner.py`, `feedback_generator.py`, and `email_extractor.py`. Generation
used a `pydantic_ai` agent with an inline system prompt. Routing between
capabilities was implicit in whichever endpoint called which class, and there was
no typed state between steps, no checkpointing, no loop bound, and no way to test
a multi-step flow without the real model and real services.

The rebuild needs multi-step, intent-dependent flows: load student context,
decide whether the question is a tutor/planner/assessment/progress request,
retrieve and rerank, optionally traverse the knowledge graph, compose an answer,
validate it, and persist usage. Some flows include a bounded loop, such as
re-retrieving when context is insufficient. Orchestration must be auditable,
testable without a live model, and incapable of running away in cost.

A fully autonomous agent — freely choosing tools and spawning further agents until
it decides it is done — is explicitly not wanted. The system handles untrusted
content (`docs/architecture/system.md` §1 principle 7, §4.2) and has a permission
matrix over consequential tools. Open-ended autonomy makes cost, latency, and
behaviour unbounded and unauditable, and it puts the model's judgement in charge of
the control flow that safety depends on.

## Decision

Orchestrate with **LangGraph**, structured as a **bounded graph with agentic
nodes** rather than an open-ended autonomous swarm.

- **Typed state.** One `ConversationState` (Pydantic or `TypedDict`) is the only
  data passed between nodes: tenant and user context, conversation history,
  intent, resolved filters, retrieved candidates, reranked passages, graph
  results, draft answer, citations, usage, degradation flags. Transitions are
  type-checked, so a node cannot silently expect a field no upstream node
  produces.
- **Explicit nodes.** Each node is a typed function with one responsibility
  (intent routing, context assembly, retrieval, reranking, graph traversal,
  generation, output validation, persistence). Nodes delegate to domain services;
  they do not contain SQL or provider calls.
- **Conditional edges.** Routing is a declared, reviewable edge condition, not an
  `if` buried in a prompt. Degradation paths are edges too: reranker failure
  routes to RRF order, generation failure to the extractive answer.
- **Bounded loops.** Loops carry an explicit counter and terminate at
  `MAX_SUPERSTEPS`/`MAX_TOOL_CALLS`; there is no unbounded retry and no
  self-spawning.
- **Checkpointing.** State is checkpointed by tenant and conversation, enabling
  resume after mid-graph failure, replay for evaluation, and human-in-the-loop
  interrupts for tools needing approval.
- **Registry-mediated tools.** Nodes invoke tools through a typed,
  permission-checked registry from a declared allow-list; the model never receives
  an arbitrary tool handle.
- **Streaming.** Token and node-level progress events are emitted from the graph so
  the API can forward SSE without knowing the internal topology.

## Consequences

### Positive

- Control flow is legible and reviewable: every path, including failure paths, is
  visible in the graph, which is not true of a hand-rolled loop or a ReAct agent
  choosing tools at runtime.
- Typed state turns a step-boundary integration mistake into a validation error
  rather than a symptom deep in generation.
- Nodes are testable with a fake LLM and fake services, so flow logic is tested
  deterministically and cheaply.
- Loop bounds and per-node timeouts make cost and latency bounded by construction.
- Checkpointing gives resume-after-failure and replay, making an evaluation run
  reproducible against a stored trace.
- Degradation and safety checks are graph edges, so a node cannot skip them by
  deciding to answer anyway.
- Human-in-the-loop approval for consequential tools is a first-class interrupt
  rather than an ad-hoc flag.

### Negative

- **A fast-moving dependency with its own abstractions.** State reducers, channel
  semantics, and checkpoint serialisation must be learned, and graph-facing
  tracebacks are harder to read than plain Python.
- **Checkpoint schema migration.** Persisted state has a schema; changing
  `ConversationState` can invalidate in-flight checkpoints, so changes need a
  migration or a compatibility rule.
- **Ceremony for simple flows.** A straight-line request pays for graph
  construction, state validation, and checkpointing a function call would not need.
- **Split logic.** Some behaviour lives in graph topology and some in node bodies;
  developers must know which to change for a given fix.
- **Over-typing can ossify state**, making exploratory features awkward until the
  state model catches up.
- Upgrading LangGraph can change checkpoint compatibility, so the version is
  pinned and upgrades require a replay test.

### Neutral

- The graph is orchestration only; retrieval, ranking, graph traversal, and
  generation stay independently testable domain services.
- A bounded graph with agentic nodes is a deliberate middle point: nodes may use
  the model to decide *content* (which filter, which phrasing, whether evidence is
  sufficient), while the set of possible transitions is fixed by the graph.
- `pydantic-ai` remains available for single-shot typed model calls inside nodes;
  it is not the orchestrator.

## Alternatives considered

- **Hand-rolled agent loop (prototype).** Maximum control and no dependency, but it
  reimplements state, routing, checkpointing, streaming, and loop bounds, and its
  control flow is implicit and hard to audit. Rejected.
- **LangChain agents (ReAct-style).** The model selects tools in a loop: flexible
  but non-deterministic, hard to bound in cost, hard to test, and it hands control
  flow to the same untrusted-content-exposed component the safety layer is meant
  to constrain. Rejected as the primary mechanism.
- **A plain pipeline.** Simple and predictable, but it cannot express
  intent-dependent routing, conditional degradation, or a bounded re-retrieval
  loop without growing ad-hoc branches. Rejected.
- **`pydantic-graph`.** An indirect dependency already, and appealing for typed
  nodes, but it lacks the checkpointing, streaming, and interrupt ecosystem this
  system needs. Not chosen for now; revisitable.
- **A general workflow engine (Temporal, Prefect, Airflow).** Durable execution and
  retries, but no LLM-native streaming or human-in-the-loop interrupt semantics,
  and a substantially heavier operational footprint. Rejected.
- **A minimal state-machine library (`transitions`).** Lightweight, but
  checkpointing, streaming, and persistence must be rebuilt. Not chosen.

## How this is verified

- `apps/api/tests/agents/` runs each node and conditional edge with a fake LLM and
  fake tools, asserting the resulting state with no network call.
- A termination test builds a graph containing a looping edge and asserts it stops
  at the configured step bound rather than hanging.
- A resilience test kills execution mid-graph, resumes from the checkpoint, and
  asserts no duplicated side effects (for example, no duplicate persisted turn).
- `evals/` executes the graph end-to-end on the golden dataset; the recorded trace
  and step count are checked against the configured cost budget, and measured
  latency and cost are published in `evals/reports/`.
- The permission matrix is tested by asserting a node cannot invoke a tool outside
  its allow-list, and that no agent can invoke a consequential tool such as
  outbound email by default.
