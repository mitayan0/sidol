"""Sidol: SQL for everything. Read, write, delete across any API or database.

sidol (𝘴𝘪𝘥𝘰𝘭) — the fermented fish paste that connects everything in Chakma cuisine.
"""

__version__ = "0.1.0"

# Core API
# Connector base
from sidol.connectors.base import BaseConnector
from sidol.connectors.csv_ import CSVConnector

# Built-in connectors
from sidol.connectors.servicenow import ServiceNowConnector
from sidol.connectors.sqlite_ import SQLiteConnector
from sidol.core import Session, connect

# Errors
from sidol.errors import (
    CapabilityError,
    ConnectorError,
    ParseError,
    SchemaError,
    SidolError,
    TableNotFoundError,
    UnsupportedSQLError,
    WriteError,
)
from sidol.types import (
    Capabilities,
    Column,
    QueryResult,
    Result,
    Schema,
    WriteResult,
)


def connect_servicenow(
    instance: str,
    username: str | None = None,
    password: str | None = None,
    token: str | None = None,
) -> Session:
    """Connect to a ServiceNow instance. Query any table without registering."""
    conn = ServiceNowConnector(
        instance=instance,
        username=username,
        password=password,
        token=token,
    )
    session = Session()
    session.use(conn)
    return session


__all__ = [
    # Version
    "__version__",
    # Core API
    "connect",
    "connect_servicenow",
    "Session",
    # Base
    "BaseConnector",
    # Types
    "Column",
    "Schema",
    "Capabilities",
    "WriteResult",
    "QueryResult",
    "Result",
    # Errors
    "SidolError",
    "ConnectorError",
    "WriteError",
    "SchemaError",
    "ParseError",
    "CapabilityError",
    "TableNotFoundError",
    "UnsupportedSQLError",
    # Connectors
    "ServiceNowConnector",
    "CSVConnector",
    "SQLiteConnector",
]
