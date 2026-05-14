"""Session-level context for connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConnectorContext:
    """Read-only context passed to connector methods.

    This allows passing session-level metadata (like trace IDs,
    user info, or global timeouts) without changing every method signature.
    """
    session_id: str | None = None
    user_id: str | None = None
    query_timeout: float | None = None
    options: dict[str, Any] = field(default_factory=dict)
