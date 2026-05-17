"""In-memory TTL cache shared across connectors."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    """Simple in-memory TTL cache.

    Keys are arbitrary strings.  Values expire after ``default_ttl`` seconds
    unless a per-call TTL is given to ``set()``.
    """

    def __init__(self, default_ttl: int = 300):
        self._store: dict[str, _CacheEntry] = {}
        self.default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        duration = ttl if ttl is not None else self.default_ttl
        self._store[key] = _CacheEntry(value=value, expires_at=time.time() + duration)
