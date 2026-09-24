"""Repositories: the only sanctioned access path to tenant-scoped data.

Every repository is constructed with a tenant scope and applies it to every
statement. See :mod:`coursellm.repositories.base` for why the scope is a
constructor argument rather than a method parameter.
"""

from coursellm.repositories.base import TenantRepository
from coursellm.repositories.content import CourseRepository, DocumentRepository
from coursellm.repositories.identity import AuthLookup, TenantDirectory, UserRepository

__all__ = [
    "AuthLookup",
    "CourseRepository",
    "DocumentRepository",
    "TenantDirectory",
    "TenantRepository",
    "UserRepository",
]
