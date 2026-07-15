"""Pluggable file storage.

Attachments are written through a :class:`StorageBackend` so the persistence
target can be swapped (local disk now, S3 later) without touching callers. The
only contract is save / load / delete keyed by an opaque storage key.
"""

import os
from abc import ABC, abstractmethod
from functools import lru_cache

from app.config import get_settings


class StorageBackend(ABC):
    """Opaque-key blob storage."""

    @abstractmethod
    def save(self, key: str, data: bytes) -> None:
        ...

    @abstractmethod
    def load(self, key: str) -> bytes:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...


class LocalDiskStorage(StorageBackend):
    """Store blobs as files under a base directory.

    Storage keys are server-generated (uuid-based), so they never contain
    caller-controlled path segments; the join below is not a traversal vector.
    """

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.base_dir, key)

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    def load(self, key: str) -> bytes:
        with open(self._path(key), "rb") as fh:
            return fh.read()

    def delete(self, key: str) -> None:
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass


@lru_cache
def get_storage() -> StorageBackend:
    """Return the configured storage backend (local disk by default)."""
    return LocalDiskStorage(get_settings().attachment_storage_dir)
