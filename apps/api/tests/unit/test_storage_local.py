"""Local object store: round-trip, key containment and server-side key shape.

Two security properties are exercised here:

* a key can never resolve outside the store root — including through a symlink
  planted inside it, which a naive ``..`` filter would miss; and
* :func:`new_storage_key` never incorporates the client's filename, so path
  traversal is impossible by construction rather than by filtering.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from coursellm.storage import (
    InvalidStorageKeyError,
    LocalObjectStore,
    ObjectNotFoundError,
    new_storage_key,
)

pytestmark = pytest.mark.unit

_TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
_DOCUMENT = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def store(tmp_path: Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


class TestRoundTrip:
    async def test_put_get_exists_delete(self, store: LocalObjectStore) -> None:
        key = "tenants/t/documents/d.txt"
        assert await store.exists(key) is False

        await store.put(key, b"attention weights", content_type="text/plain")
        assert await store.exists(key) is True
        assert await store.get(key) == b"attention weights"
        assert await store.delete(key) is True
        assert await store.exists(key) is False

    async def test_put_creates_parent_directories(self, store: LocalObjectStore) -> None:
        key = "a/b/c/deep.txt"
        await store.put(key, b"x", content_type=None)
        assert (store.root / key).is_file()

    async def test_put_overwrites_an_existing_object(self, store: LocalObjectStore) -> None:
        key = "same.txt"
        await store.put(key, b"first", content_type=None)
        await store.put(key, b"second", content_type=None)
        assert await store.get(key) == b"second"

    async def test_get_missing_key_raises_the_documented_error(
        self, store: LocalObjectStore
    ) -> None:
        with pytest.raises(ObjectNotFoundError):
            await store.get("does/not/exist.txt")

    async def test_delete_missing_key_returns_false(self, store: LocalObjectStore) -> None:
        assert await store.delete("does/not/exist.txt") is False

    def test_backend_identifier_is_stable(self, store: LocalObjectStore) -> None:
        assert store.backend == "local"


class TestContainment:
    async def test_parent_traversal_is_refused(self, store: LocalObjectStore) -> None:
        with pytest.raises(InvalidStorageKeyError):
            await store.put("../escape.txt", b"x", content_type=None)
        assert not (store.root.parent / "escape.txt").exists()

    async def test_deep_parent_traversal_is_refused(self, store: LocalObjectStore) -> None:
        with pytest.raises(InvalidStorageKeyError):
            await store.put("a/../../escape.txt", b"x", content_type=None)

    async def test_absolute_path_is_refused(self, store: LocalObjectStore) -> None:
        with pytest.raises(InvalidStorageKeyError):
            await store.get("/etc/passwd")

    async def test_symlink_escape_is_refused(self, store: LocalObjectStore, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (store.root / "link").symlink_to(outside)

        # The resolved path is ``outside/escape.txt``, which is not under the
        # root; a filter that only looked for ``..`` would allow this.
        with pytest.raises(InvalidStorageKeyError):
            await store.put("link/escape.txt", b"x", content_type=None)
        assert not (outside / "escape.txt").exists()

    async def test_exists_also_refuses_a_traversal_key(self, store: LocalObjectStore) -> None:
        with pytest.raises(InvalidStorageKeyError):
            await store.exists("../anything")

    @pytest.mark.parametrize("key", ["", "nul\x00byte.txt"])
    async def test_malformed_keys_are_refused(self, store: LocalObjectStore, key: str) -> None:
        with pytest.raises(InvalidStorageKeyError):
            await store.put(key, b"x", content_type=None)

    async def test_a_sibling_with_a_shared_prefix_is_outside_the_root(self, tmp_path: Path) -> None:
        root = tmp_path / "objects"
        store = LocalObjectStore(root)
        # ``objects_evil`` shares a textual prefix with ``objects`` but is not
        # inside it; containment must compare path components, not strings.
        (tmp_path / "objects_evil").mkdir()
        with pytest.raises(InvalidStorageKeyError):
            await store.put("../objects_evil/escape.txt", b"x", content_type=None)


class TestNewStorageKey:
    def test_shape_is_server_generated(self) -> None:
        assert new_storage_key(_TENANT, _DOCUMENT, ".pdf") == (
            f"tenants/{_TENANT}/documents/{_DOCUMENT}.pdf"
        )

    def test_extension_is_lowercased(self) -> None:
        assert new_storage_key(_TENANT, _DOCUMENT, ".PDF").endswith(".pdf")

    @pytest.mark.parametrize("extension", [None, "", "pdf", "/etc/passwd", "..", ".p df"])
    def test_unsafe_extensions_are_discarded(self, extension: str | None) -> None:
        key = new_storage_key(_TENANT, _DOCUMENT, extension)
        assert key == f"tenants/{_TENANT}/documents/{_DOCUMENT}"

    @pytest.mark.parametrize(
        ("hostile", "fragment"),
        [
            ("../../etc/passwd.txt", "etc/passwd"),
            ("..%2f..%2fetc%2fpasswd.txt", "etc%2fpasswd"),
            ("evil\x00.txt", "\x00"),
            ("a" * 496 + ".txt", "a" * 496),
            ("/absolute/path/report.txt", "/absolute/path"),
        ],
    )
    def test_key_never_contains_the_client_filename(self, hostile: str, fragment: str) -> None:
        key = new_storage_key(_TENANT, _DOCUMENT, Path(hostile).suffix.lower())
        assert fragment not in key
        assert key.startswith(f"tenants/{_TENANT}/documents/{_DOCUMENT}")

    def test_two_documents_never_share_a_key(self) -> None:
        other = uuid.uuid4()
        assert new_storage_key(_TENANT, _DOCUMENT, ".txt") != new_storage_key(
            _TENANT, other, ".txt"
        )
