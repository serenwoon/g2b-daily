"""A failed request must say why it failed, and must never say with what key.

The daily run reports connection problems through CollectionError, and that
message is the only thing a red run leaves behind. "request failed" cannot be
told apart from a DNS miss, a refused connection, or a TLS handshake that never
completed -- so the reason is carried through.

The request URL is deliberately not part of any message: it carries the service
key as a query parameter, and workflow logs are public.
"""

from __future__ import annotations

import socket
import ssl
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from g2b_daily.api import CollectionError, G2BClient


SERVICE_KEY = "super-secret-service-key"


def client(timeout: int = 30) -> G2BClient:
    return G2BClient(SERVICE_KEY, page_size=1, timeout=timeout)


def failure_message(error: Exception, *, timeout: int = 30) -> str:
    """Return the CollectionError message raised when urlopen fails this way."""
    with patch("g2b_daily.api.urlopen", side_effect=error):
        try:
            client(timeout).fetch_window(
                inquiry_div="1", begin="202609010000", end="202609012359"
            )
        except CollectionError as exc:
            return str(exc)
    raise AssertionError(f"{type(error).__name__} did not raise CollectionError")


class ConnectionFailureMessageTests(unittest.TestCase):
    def test_timeout_says_it_timed_out_and_after_how_long(self) -> None:
        message = failure_message(URLError(socket.timeout()), timeout=30)
        self.assertIn("timed out", message)
        self.assertIn("30s", message)

    def test_tls_failure_names_the_handshake(self) -> None:
        message = failure_message(URLError(ssl.SSLError("WRONG_VERSION_NUMBER")))
        self.assertIn("SSLError", message)
        self.assertIn("WRONG_VERSION_NUMBER", message)

    def test_dns_failure_names_the_lookup(self) -> None:
        message = failure_message(URLError(socket.gaierror(-2, "Name or service not known")))
        self.assertIn("gaierror", message)
        self.assertIn("Name or service not known", message)

    def test_missing_reason_does_not_print_the_word_none(self) -> None:
        message = failure_message(URLError(None))
        self.assertIn("no reason given", message)
        self.assertNotIn("None", message)

    def test_string_reason_is_carried_through(self) -> None:
        self.assertIn("something odd", failure_message(URLError("something odd")))

    def test_http_error_keeps_the_status_and_gains_the_reason(self) -> None:
        error = HTTPError(
            "https://apis.data.go.kr/x", 503, "Service Unavailable", {}, None
        )
        message = failure_message(error)
        self.assertIn("503", message)
        self.assertIn("Service Unavailable", message)


class NoSecretInMessageTests(unittest.TestCase):
    """The message is the one artefact a public workflow log keeps."""

    def test_no_failure_message_carries_the_service_key(self) -> None:
        errors = [
            URLError(socket.timeout()),
            URLError(ssl.SSLError("handshake failure")),
            URLError(socket.gaierror(-2, "Name or service not known")),
            HTTPError("https://apis.data.go.kr/x", 500, "Server Error", {}, None),
        ]
        for error in errors:
            with self.subTest(error=type(error.reason).__name__):
                message = failure_message(error)
                self.assertNotIn(SERVICE_KEY, message)
                self.assertNotIn("serviceKey", message)


if __name__ == "__main__":
    unittest.main()
