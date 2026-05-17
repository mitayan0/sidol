import json
import unittest
from typing import Any

import httpx

from sidol.types import QueryResult, WriteResult


class BaseConnectorTestCase(unittest.TestCase):
    """Base class for testing Sidol connectors.

    Provides standardized mocking for REST connectors and common CRUD assertions.
    """

    def setUp(self) -> None:
        self.mock_calls: list[httpx.Request] = []
        self._mock_responses: list[httpx.Response] = []

    def mock_response(
        self,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        """Queue a mock HTTP response."""
        content = None
        if json_data is not None:
            content = json.dumps(json_data).encode("utf-8")
        elif text:
            content = text.encode("utf-8")

        resp = httpx.Response(status_code, content=content, headers=headers or {})
        self._mock_responses.append(resp)

    def get_mock_client(self) -> httpx.Client:
        """Return an httpx.Client using the mock transport."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.mock_calls.append(request)
            if self._mock_responses:
                return self._mock_responses.pop(0)
            return httpx.Response(200, json={"result": []})

        return httpx.Client(transport=httpx.MockTransport(handler))

    def assert_query_result(
        self, result: QueryResult, expected_rows: list[dict[str, Any]]
    ) -> None:
        """Assert that a QueryResult matches a list of dicts."""
        self.assertEqual(len(result.rows), len(expected_rows))
        # Convert QueryResult rows (tuples) to list of dicts for comparison
        actual_dicts = []
        for row in result.rows:
            actual_dicts.append(dict(zip(result.columns, row, strict=False)))
        self.assertEqual(actual_dicts, expected_rows)

    def assert_write_result(self, result: WriteResult, affected_rows: int) -> None:
        """Assert that a WriteResult has the expected number of affected rows."""
        self.assertEqual(result.affected_rows, affected_rows)

    def assert_last_request(self, method: str, path_suffix: str) -> None:
        """Assert that the last HTTP request used the given method and path."""
        self.assertTrue(self.mock_calls, "No HTTP calls were made")
        last = self.mock_calls[-1]
        self.assertEqual(last.method, method.upper())
        self.assertTrue(
            str(last.url).endswith(path_suffix),
            f"Expected URL to end with {path_suffix}, got {last.url}",
        )
