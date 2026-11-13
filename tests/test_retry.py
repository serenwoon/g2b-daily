"""One stalled request must not cost the whole day.

data.go.kr occasionally fails to answer a single request: the connection times
out, or the body is not the JSON it normally is. A day's collection is ten or
more requests, so without a second try one such stall ends the run and leaves a
hole in the series.

The retry is per request and changes nothing about completeness: a page is
either fetched in full or the collection fails, exactly as before.
"""

from __future__ import annotations

import contextlib
import io
import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from g2b_daily.api import CollectionError, G2BClient


SERVICE_KEY = "super-secret-service-key"
WINDOW = {"inquiry_div": "1", "begin": "202609010000", "end": "202609012359"}


class FakeResponse:
    """What urlopen hands back: a context manager with a body to read."""

    def __init__(self, body: bytes | Exception) -> None:
        self._body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def read(self) -> bytes:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def one_notice() -> FakeResponse:
    payload = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
            "body": {
                "items": {"item": [{"bidNtceNo": "N1", "bidNtceOrd": "000"}]},
                "totalCount": "1",
            },
        }
    }
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def api_error() -> FakeResponse:
    payload = {"header": {"resultCode": "30", "resultMsg": "bad key"}, "body": {}}
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def timeout() -> URLError:
    return URLError(socket.timeout())


def http_error(code: int, reason: str) -> HTTPError:
    return HTTPError("https://apis.data.go.kr/x", code, reason, {}, None)


def run(outcomes: list[object], *, waits: tuple[float, ...] = (0, 0)):
    """Drive one window against a scripted urlopen.

    Returns (items or the CollectionError, request count, waits slept,
    what was written to stderr).
    """
    slept: list[float] = []
    stderr = io.StringIO()
    client = G2BClient(
        SERVICE_KEY, page_size=100, retry_waits=waits, sleep=slept.append
    )
    with (
        patch("g2b_daily.api.urlopen", side_effect=outcomes) as opened,
        contextlib.redirect_stderr(stderr),
    ):
        try:
            result: object = client.fetch_window(**WINDOW)
        except CollectionError as exc:
            result = exc
    return result, opened.call_count, slept, stderr.getvalue()


class TransientFailureIsRetriedTests(unittest.TestCase):
    def test_a_timed_out_request_is_tried_again_and_its_page_kept(self) -> None:
        result, requests, _, _ = run([timeout(), one_notice()])

        self.assertEqual(result, [{"bidNtceNo": "N1", "bidNtceOrd": "000"}])
        self.assertEqual(requests, 2)

    def test_a_non_json_body_is_tried_again(self) -> None:
        result, requests, _, _ = run([FakeResponse(b"<html>busy</html>"), one_notice()])

        self.assertEqual(result, [{"bidNtceNo": "N1", "bidNtceOrd": "000"}])
        self.assertEqual(requests, 2)

    def test_a_body_that_stalls_while_being_read_is_tried_again(self) -> None:
        result, requests, _, _ = run([FakeResponse(TimeoutError()), one_notice()])

        self.assertEqual(result, [{"bidNtceNo": "N1", "bidNtceOrd": "000"}])
        self.assertEqual(requests, 2)

    def test_a_server_side_http_error_is_tried_again(self) -> None:
        result, requests, _, _ = run(
            [http_error(503, "Service Unavailable"), one_notice()]
        )

        self.assertEqual(result, [{"bidNtceNo": "N1", "bidNtceOrd": "000"}])
        self.assertEqual(requests, 2)

    def test_it_waits_the_configured_time_before_each_new_try(self) -> None:
        _, _, slept, _ = run([timeout(), timeout(), one_notice()], waits=(5, 15))

        self.assertEqual(slept, [5, 15])


class RetryIsBoundedTests(unittest.TestCase):
    def test_it_gives_up_after_the_last_wait_and_says_so(self) -> None:
        result, requests, slept, _ = run(
            [timeout(), timeout(), timeout(), one_notice()], waits=(5, 15)
        )

        self.assertIsInstance(result, CollectionError)
        self.assertEqual(requests, 3)
        self.assertEqual(slept, [5, 15])
        self.assertIn("timed out", str(result))
        self.assertIn("3 attempts", str(result))

    def test_a_rejected_request_is_not_tried_again(self) -> None:
        """403 means the key or the request is wrong; asking again cannot help."""
        result, requests, slept, _ = run([http_error(403, "Forbidden"), one_notice()])

        self.assertIsInstance(result, CollectionError)
        self.assertIn("403", str(result))
        self.assertEqual(requests, 1)
        self.assertEqual(slept, [])

    def test_an_error_the_api_reported_is_not_tried_again(self) -> None:
        result, requests, slept, _ = run([api_error(), one_notice()])

        self.assertIsInstance(result, CollectionError)
        self.assertIn("data.go.kr error 30", str(result))
        self.assertEqual(requests, 1)
        self.assertEqual(slept, [])


class RetryNoticeTests(unittest.TestCase):
    """A green run that needed a second try should still show that it did."""

    def test_the_retry_is_reported_without_the_service_key(self) -> None:
        _, _, _, notice = run([timeout(), one_notice()])

        self.assertIn("timed out", notice)
        self.assertIn("retrying", notice)
        self.assertNotIn(SERVICE_KEY, notice)
        self.assertNotIn("serviceKey", notice)


if __name__ == "__main__":
    unittest.main()
