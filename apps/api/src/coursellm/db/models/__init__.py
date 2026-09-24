"""ORM models.

Importing this package registers every model on ``Base.metadata``. Alembic's
``env.py`` imports it for autogenerate, and the test fixtures import it before
creating the schema, so a model that is not re-exported here would be silently
missing from migrations.
"""

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
from coursellm.db.models.identity import (
    RefreshTokenRevocation,
    Tenant,
    TenantPlan,
    User,
    UserRole,
)
from coursellm.db.models.usage import LLMUsage

__all__ = [
    "EMBEDDING_DIM",
    "Chunk",
    "ChunkEmbedding",
    "ChunkTerm",
    "Conversation",
    "Course",
    "Document",
    "DocumentStatus",
    "LLMUsage",
    "Message",
    "MessageRole",
    "QuarantineState",
    "RefreshTokenRevocation",
    "SourceType",
    "Tenant",
    "TenantCorpusStats",
    "TenantLexicalStats",
    "TenantPlan",
    "User",
    "UserRole",
]
