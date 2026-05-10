"""Sidol exception hierarchy."""


class SidolError(Exception):
    """Base error for all sidol errors."""
    pass


class ConnectorError(SidolError):
    """A connector failed to read or write."""
    pass


class WriteError(ConnectorError):
    """A write operation (INSERT/UPDATE/DELETE) failed."""
    pass


class SchemaError(SidolError):
    """Schema discovery failed."""
    pass


class ParseError(SidolError):
    """SQL parsing failed."""
    pass


class CapabilityError(ConnectorError):
    """Connector does not support the requested operation."""

    def __init__(self, connector_name: str, operation: str):
        super().__init__(
            f"Connector '{connector_name}' does not support {operation}. "
            f"Check connector.capabilities() before attempting this operation."
        )


class TableNotFoundError(SidolError):
    """No connector registered for the requested table."""

    def __init__(self, table: str, available: list[str]):
        super().__init__(
            f"No connector registered for table '{table}'.\n"
            f"Registered tables: {', '.join(available) or 'none'}\n"
            f"Add a connector with: db.register('{table}', YourConnector(...)"
        )


class UnsupportedSQLError(SidolError):
    """SQL is valid but outside sidol's supported subset."""
    pass


class UnknownTableError(SidolError):
    """A SQL statement references a table not registered in sidol."""
    pass
