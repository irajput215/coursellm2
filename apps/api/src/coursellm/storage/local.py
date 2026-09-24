"""Filesystem :class:`~coursellm.storage.base.ObjectStore` implementation.

Local development writes to a directory under the repository so that a running
API and the test suite agree on where bytes live. It is deliberately *not* the
deployment implementation: object storage (S3) is the target, because a
container's filesystem is ephemeral and is not shared between replicas. This
class exists so the ingestion path is exercised end to end without an external
service, and so the interface an S3 backend must satisfy is pinned by a working
implementation.

The security property this module owns is containment: a key is resolved
against the root and rejected unless the resolved path is strictly inside it.
Resolving — rather than pattern-matching ``..`` — is what defeats a symlink
planted inside the root that points outside it.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from coursellm.core.config import REPO_ROOT
from coursellm.storage.base import (
    InvalidStorageKeyError,
    ObjectNotFoundError,
    ObjectStoreError,
)

#: Directory (relative to the repository root) used when the application builds
#: its default store. ``uploads/`` is already git-ignored, so running the API
#: locally cannot leave a committable object in the working tree. Kept out of
#: ``core.config`` deliberately: it is not a retrieval-affecting tunable, and
#: adding a setting for it would imply a deployment knob that does not exist yet.
_DEFAULT_ROOT_PARTS = ("uploads", "object_store")


def default_storage_root() -> Path:
    """The process default store root for local development."""
    return REPO_ROOT.joinpath(*_DEFAULT_ROOT_PARTS)


class LocalObjectStore:
    """A filesystem object store rooted at ``root``.

    All blocking filesystem work runs in a worker thread, so the async contract
    is honest: a caller on the event loop does not block on disk I/O.
    """

    backend: str = "local"

    def __init__(self, root: Path) -> None:
        # ``resolve`` normalises the root once so every containment check is a
        # comparison of already-resolved paths.
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """The resolved root. Exposed for tests and diagnostics, not for callers."""
        return self._root

    # ------------------------------------------------------------------
    # Key containment
    # ------------------------------------------------------------------
    def _resolve(self, key: str) -> Path:
        """Map ``key`` to a path strictly inside the root, or refuse it.

        Rejects, in order: an empty key, a NUL byte, an absolute key, and any
        key whose resolved path is not a descendant of the root. The last check
        is what catches ``..`` segments *and* symlinks that leave the root,
        because :meth:`Path.resolve` follows symlinks before the comparison.
        """
        if not key or "\x00" in key:
            raise InvalidStorageKeyError("A storage key must be a non-empty path without NUL.")
        if Path(key).is_absolute() or key.startswith("\\"):
            raise InvalidStorageKeyError(f"Storage key {key!r} must be relative to the store root.")
        candidate = (self._root / key).resolve()
        if self._root not in candidate.parents:
            raise InvalidStorageKeyError(
                f"Storage key {key!r} resolves outside the store root and was refused."
            )
        return candidate

    # ------------------------------------------------------------------
    # ObjectStore protocol
    # ------------------------------------------------------------------
    async def put(self, key: str, data: bytes, *, content_type: str | None) -> None:
        """Write ``data`` under ``key``, creating parent directories.

        ``content_type`` is accepted for interface parity with S3 and ignored:
        a filesystem has nowhere to record it, and the value is already stored
        on the ``documents`` row.
        """
        path = self._resolve(key)
        try:
            await asyncio.to_thread(_write_bytes, path, data)
        except OSError as exc:
            raise ObjectStoreError(f"Could not store object at {key!r}.") from exc

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(f"No object exists at {key!r}.") from exc
        except OSError as exc:
            raise ObjectStoreError(f"Could not read object at {key!r}.") from exc

    async def delete(self, key: str) -> bool:
        path = self._resolve(key)
        try:
            return await asyncio.to_thread(_unlink, path)
        except OSError as exc:
            raise ObjectStoreError(f"Could not delete object at {key!r}.") from exc

    async def exists(self, key: str) -> bool:
        path = self._resolve(key)
        return await asyncio.to_thread(path.is_file)


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


@lru_cache(maxsize=1)
def get_local_object_store() -> LocalObjectStore:
    """The process-wide development store.

    Cached so the directory is created once, and so tests can override the
    FastAPI dependency rather than reaching into a module global. It is built
    lazily: importing the application must not create directories.
    """
    return LocalObjectStore(default_storage_root())


__all__ = [
    "LocalObjectStore",
    "default_storage_root",
    "get_local_object_store",
]
