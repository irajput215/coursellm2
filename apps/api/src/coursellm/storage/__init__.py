"""Object storage abstractions and the local development implementation.

Import surface for the ingestion service: the :class:`ObjectStore` protocol,
the errors a caller must handle, the server-side key builder, and the
filesystem backend used in development.
"""

from __future__ import annotations

from coursellm.storage.base import (
    InvalidStorageKeyError,
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreError,
    new_storage_key,
)
from coursellm.storage.local import (
    LocalObjectStore,
    default_storage_root,
    get_local_object_store,
)

__all__ = [
    "InvalidStorageKeyError",
    "LocalObjectStore",
    "ObjectNotFoundError",
    "ObjectStore",
    "ObjectStoreError",
    "default_storage_root",
    "get_local_object_store",
    "new_storage_key",
]
