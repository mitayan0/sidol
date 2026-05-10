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

__all__ = [
    # Version
    "__version__",
    # Core API
    "connect",
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
