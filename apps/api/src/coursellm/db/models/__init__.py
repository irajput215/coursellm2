"""ORM models.

Importing this package registers every model on ``Base.metadata``. Alembic's
``env.py`` imports it for autogenerate, and the test fixtures import it before
creating the schema, so a model that is not re-exported here would be silently
missing from migrations.
"""

from coursellm.db.models.assessment import Quiz
from coursellm.db.models.content import (
    EMBEDDING_DIM,
    Chunk,
    ChunkEmbedding,
    ChunkTerm,
    Course,
    Document,
    DocumentStatus,
    QuarantineState,
    SourceType,
    TenantCorpusStats,
    TenantLexicalStats,
)
from coursellm.db.models.conversation import (
    Conversation,
    Message,
    MessageRole,
)
from coursellm.db.models.graph import (
    AliasSource,
    Concept,
    ConceptAlias,
    ConceptEdge,
    ExtractionRunStatus,
    GraphExtractionRun,
)
from coursellm.db.models.identity import (
    RefreshTokenRevocation,
    Tenant,
    TenantPlan,
    User,
    UserRole,
)
from coursellm.db.models.learning import (
    ProgressEvent,
    ProgressEventKind,
    QuizAttempt,
    Roadmap,
    RoadmapStatus,
    RoadmapStep,
    RoadmapStepStatus,
)
from coursellm.db.models.resource import (
    Resource,
    ResourceConcept,
    ResourceType,
    SourceTrust,
)
from coursellm.db.models.usage import LLMUsage

__all__ = [
    "EMBEDDING_DIM",
    "AliasSource",
    "Chunk",
    "ChunkEmbedding",
    "ChunkTerm",
    "Concept",
    "ConceptAlias",
    "ConceptEdge",
    "Conversation",
    "Course",
    "Document",
    "DocumentStatus",
    "ExtractionRunStatus",
    "GraphExtractionRun",
    "LLMUsage",
    "Message",
    "MessageRole",
    "ProgressEvent",
    "ProgressEventKind",
    "QuarantineState",
    "Quiz",
    "QuizAttempt",
    "RefreshTokenRevocation",
    "Resource",
    "ResourceConcept",
    "ResourceType",
    "Roadmap",
    "RoadmapStatus",
    "RoadmapStep",
    "RoadmapStepStatus",
    "SourceTrust",
    "SourceType",
    "Tenant",
    "TenantCorpusStats",
    "TenantLexicalStats",
    "TenantPlan",
    "User",
    "UserRole",
]
