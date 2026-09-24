"""Database layer: models, session management and tenancy enforcement."""

from coursellm.db.base import GLOBAL_TABLES, TENANT_SCOPED_TABLES, Base

__all__ = ["GLOBAL_TABLES", "TENANT_SCOPED_TABLES", "Base"]
