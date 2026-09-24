"""CourseLLM — an agentic RAG tutoring platform.

Package layout, outermost first:

``coursellm.core``          configuration, logging, errors, telemetry
``coursellm.db``            SQLAlchemy models, session management, tenancy
``coursellm.llm``           LiteLLM gateway: routing, fallback, cost accounting
``coursellm.rag``           ingestion, retrieval, fusion, reranking, generation
``coursellm.graph``         knowledge graph extraction and traversal
``coursellm.agents``        LangGraph state machine and its nodes
``coursellm.tools``         typed, permission-checked tools the agents may call
``coursellm.security``      injection detection, sanitisation, output validation
``coursellm.services``      use-case orchestration shared by routers and agents
``coursellm.api``           FastAPI application and domain routers
"""

__version__ = "0.1.0"
