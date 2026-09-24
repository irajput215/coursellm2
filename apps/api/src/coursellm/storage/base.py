"""Object storage: the interface the ingestion use case writes bytes through.

Why an interface at all, when local development is a filesystem write: the
deployment target is object storage (S3), and the *only* thing that changes
between the two is where the bytes land. Keeping that behind a protocol means
the ingestion use case — which owns validation, deduplication, the database row
and the pipeline call — never learns which backend is in use, and an S3 backend
can be added without touching the service.

Two properties are load-bearing and are the reason this module exists rather
than an ``open()`` call inside the service:

**Keys are server-generated.** The client's filename is display metadata on the
``documents`` row; it is never part of a storage key. :func:`new_storage_key`
builds a key from identifiers the caller controls and a validated extension, so
path traversal is *structurally impossible* rather than filtered: there is no
client-supplied path segment to sanitise, and no filter whose omission would
reopen the hole.

**Keys cannot escape the root.** Every implementation must resolve a key against
its root and refuse one whose resolution lands outside it, including the case
where an in-root symlink points elsewhere. A filter that only strips ``..`` is
bypassed by a symlink; resolving and checking is not.
"""

from __future__ import annotations

import re
import uuid
from typing import Protocol, runtime_checkable

#: A storage key suffix: a dot followed by a short alphanumeric token. Anything
#: else (``..``, a slash, a backslash, a colon) is discarded, so an extension
#: can never contribute a path separator or a parent reference.
_SAFE_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,16}$")


class ObjectStoreError(Exception):
    """Base class for storage failures.

    Deliberately not a domain error: storage is infrastructure, and the
    ingestion use case translates these into the response the user sees.
    """


class InvalidStorageKeyError(ObjectStoreError):
    """A key is malformed or resolves outside the store root."""


class ObjectNotFoundError(ObjectStoreError):
    """No object exists at the requested key."""


@runtime_checkable
class ObjectStore(Protocol):
    """Byte storage, keyed by opaque server-generated strings.

    Implementations in this repository: :class:`~coursellm.storage.local.
    LocalObjectStore` (development). S3 is the deployment target; adding it is a
    new class implementing this protocol plus a dependency change, not a change
    to the ingestion service.
    """

    #: Short identifier recorded in logs so an operator can tell backends apart.
    backend: str

    async def put(self, key: str, data: bytes, *, content_type: str | None) -> None:
        """Store ``data`` at ``key``, overwriting any existing object.

        ``content_type`` is a hint for backends that persist metadata (S3);
        a filesystem implementation may ignore it.
        """
        ...

    async def get(self, key: str) -> bytes:
        """Return the object at ``key``, or raise :class:`ObjectNotFoundError`."""
        ...

    async def delete(self, key: str) -> bool:
        """Remove ``key``. Returns whether an object was actually removed."""
        ...

    async def exists(self, key: str) -> bool:
        """Whether an object exists at ``key``."""
        ...


def new_storage_key(tenant_id: uuid.UUID, document_id: uuid.UUID, extension: str | None) -> str:
    """Build the server-generated key for an uploaded document.

    The shape is ``tenants/{tenant_id}/documents/{document_id}{extension}``.
    Every component is either a server-generated UUID or a validated extension,
    so no part of the client's filename reaches the path. That is what makes
    traversal structurally impossible: there is nothing to filter, because the
    hostile text never enters this function.

    ``extension`` is lower-cased and accepted only when it matches
    :data:`_SAFE_EXTENSION_RE`; anything else (including ``None`` and a bare
    filename with no suffix) yields a key with no extension.
    """
    suffix = (extension or "").strip().lower()
    if not _SAFE_EXTENSION_RE.fullmatch(suffix):
        suffix = ""
    return f"tenants/{tenant_id}/documents/{document_id}{suffix}"


__all__ = [
    "InvalidStorageKeyError",
    "ObjectNotFoundError",
    "ObjectStore",
    "ObjectStoreError",
    "new_storage_key",
]
