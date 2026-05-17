"""Tests for sidol.connectors.http_utils retry logic."""

import unittest

import httpx

from sidol import BaseConnectorTestCase
from sidol.connectors.http_utils import _retry_delay, request_with_retry


class RetryDelayTests(unittest.TestCase):

    def test_exponential_backoff_attempt_0(self):
        resp = httpx.Response(429)
        self.assertEqual(_retry_delay(resp, 0), 1.0)

    def test_exponential_backoff_attempt_1(self):
        resp = httpx.Response(429)
        self.assertEqual(_retry_delay(resp, 1), 2.0)

    def test_exponential_backoff_attempt_2(self):
        resp = httpx.Response(429)
        self.assertEqual(_retry_delay(resp, 2), 4.0)

    def test_retry_after_header_overrides_backoff(self):
        resp = httpx.Response(429, headers={"Retry-After": "7"})
        self.assertEqual(_retry_delay(resp, 0), 7.0)

    def test_retry_after_non_numeric_falls_back_to_backoff(self):
        resp = httpx.Response(429, headers={"Retry-After": "Mon, 01 Jan 2025 00:00:00 GMT"})
        self.assertEqual(_retry_delay(resp, 1), 2.0)


class RequestWithRetryTests(BaseConnectorTestCase):

    def test_200_succeeds_without_retry(self):
        self.mock_response(200, json_data={"ok": True})
        resp = request_with_retry(self.get_mock_client(), "GET", "http://example.com/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.mock_calls), 1)

    def test_retries_on_429_and_succeeds(self):
        self.mock_response(429, headers={"Retry-After": "0"})
        self.mock_response(200, json_data={"ok": True})
        client = self.get_mock_client()
        resp = request_with_retry(client, "GET", "http://example.com/", max_retries=3)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.mock_calls), 2)

    def test_retries_on_503_and_succeeds(self):
        self.mock_response(503, headers={"Retry-After": "0"})
        self.mock_response(503, headers={"Retry-After": "0"})
        self.mock_response(200, json_data={"ok": True})
        client = self.get_mock_client()
        resp = request_with_retry(client, "GET", "http://example.com/", max_retries=3)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.mock_calls), 3)

    def test_returns_last_error_after_max_retries_exhausted(self):
        self.mock_response(429, headers={"Retry-After": "0"})
        self.mock_response(429, headers={"Retry-After": "0"})
        self.mock_response(429, headers={"Retry-After": "0"})
        client = self.get_mock_client()
        resp = request_with_retry(client, "GET", "http://example.com/", max_retries=3)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(len(self.mock_calls), 3)

    def test_non_retryable_error_is_returned_immediately(self):
        self.mock_response(404)
        client = self.get_mock_client()
        resp = request_with_retry(client, "GET", "http://example.com/", max_retries=3)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(len(self.mock_calls), 1)

    def test_500_is_not_retried(self):
        self.mock_response(500)
        client = self.get_mock_client()
        resp = request_with_retry(client, "GET", "http://example.com/", max_retries=3)
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(len(self.mock_calls), 1)
