"""Sidol: SQL for everything. Read, write, delete across any API or database.

sidol (𝘴𝘪𝘥𝘰𝘭) — the fermented fish paste that connects everything in Chakma cuisine.
"""

__version__ = "0.1.0"

# Core API
from sidol.core import Session, connect

# Connector base
from sidol.connectors.base import BaseConnector
from sidol.types import (
    Column,
    Schema,
    Capabilities,
    WriteResult,
    QueryResult,
    Result,
)

# Errors
from sidol.errors import (
    SidolError,
    ConnectorError,
    WriteError,
    SchemaError,
    ParseError,
    CapabilityError,
    TableNotFoundError,
    UnsupportedSQLError,
)

# Built-in connectors
from sidol.connectors.servicenow import ServiceNowConnector
from sidol.connectors.csv_ import CSVConnector
from sidol.connectors.sqlite_ import SQLiteConnector

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
