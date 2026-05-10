"""TTL cache for schema and other connector metadata."""

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    expires_at: float


class TTLCache:
    """Simple in-memory TTL cache."""

    def __init__(self, default_ttl: int = 300):  # 5 minutes default
        self._store: dict[str, CacheEntry] = {}
        self.default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        """Get value if not expired. Returns None if missing or expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store value with TTL (defaults to cache's default_ttl)."""
        ttl = ttl if ttl is not None else self.default_ttl
        self._store[key] = CacheEntry(
            value=value,
            expires_at=time.time() + ttl
        )

    def invalidate(self, key: str) -> None:
        """Remove a specific key."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()
