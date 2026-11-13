"""A day that failed must be picked up by a later run.

The daily run used to ask for yesterday only. When that one run failed, the day
stayed missing for good, because the next run asked for the next day. With
--catch-up-days the run looks back over the last N days and collects every date
that still has no snapshot, oldest first.

Existing snapshots are never rewritten, and a date that fails still fails the
run -- but the dates collected before it are kept.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from g2b_daily.cli import main
from g2b_daily.collector import missing_snapshot_dates


YESTERDAY = date(2026, 11, 12)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def api(*, refuse: str | None = None):
    """A stand-in for urlopen that answers every window with one notice.

    `refuse` is a YYYYMMDD day the API rejects outright (HTTP 403, which is
    not retried), to stand for a date whose collection fails.
    """

    def urlopen(request, timeout=None):
        query = parse_qs(urlparse(request.full_url).query)
        day = query["inqryBgnDt"][0][:8]
        if day == refuse:
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        payload = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
                "body": {
                    "items": {"item": [{"bidNtceNo": f"N{day}", "bidNtceOrd": "000"}]},
                    "totalCount": "1",
                },
            }
        }
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    return urlopen


def run_cli(directory: Path, *args: str, refuse: str | None = None):
    """Run the CLI against a temp directory. Returns (exit code, stdout, stderr)."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with (
        patch("g2b_daily.api.urlopen", side_effect=api(refuse=refuse)),
        patch("g2b_daily.cli.previous_kst_date", return_value=YESTERDAY),
        patch.dict(os.environ, {"DATA_GO_KR_SERVICE_KEY": "test-key"}),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        code = main(["--output-dir", str(directory), *args])
    return code, stdout.getvalue(), stderr.getvalue()


def snapshot_names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


class MissingSnapshotDatesTests(unittest.TestCase):
    def test_lists_the_dates_without_a_snapshot_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "2026-11-10.jsonl").write_text("", encoding="utf-8")
            (directory / "2026-11-12.jsonl").write_text("", encoding="utf-8")

            missing = missing_snapshot_dates(directory, YESTERDAY, 4)

        self.assertEqual(missing, [date(2026, 11, 9), date(2026, 11, 11)])

    def test_looks_no_further_back_than_the_given_number_of_days(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            missing = missing_snapshot_dates(Path(name), YESTERDAY, 2)

        self.assertEqual(missing, [date(2026, 11, 11), date(2026, 11, 12)])

    def test_a_directory_that_does_not_exist_yet_is_all_missing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            missing = missing_snapshot_dates(Path(name) / "snapshots", YESTERDAY, 1)

        self.assertEqual(missing, [YESTERDAY])


class CatchUpRunTests(unittest.TestCase):
    def test_collects_every_missing_date_and_leaves_existing_ones_alone(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            kept = directory / "2026-11-11.jsonl"
            kept.write_text("already here\n", encoding="utf-8")

            code, _, _ = run_cli(directory, "--catch-up-days", "3")

            self.assertEqual(code, 0)
            self.assertEqual(
                snapshot_names(directory),
                ["2026-11-10.jsonl", "2026-11-11.jsonl", "2026-11-12.jsonl"],
            )
            self.assertEqual(kept.read_text(encoding="utf-8"), "already here\n")
            filled = json.loads(
                (directory / "2026-11-10.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(filled["snapshotDate"], "2026-11-10")
            self.assertEqual(filled["bidNtceNo"], "N20261110")

    def test_without_the_option_only_yesterday_is_collected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)

            code, _, _ = run_cli(directory)

            self.assertEqual(code, 0)
            self.assertEqual(snapshot_names(directory), ["2026-11-12.jsonl"])

    def test_nothing_missing_is_a_success_that_requests_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            for day in ("2026-11-11", "2026-11-12"):
                (directory / f"{day}.jsonl").write_text("", encoding="utf-8")

            code, stdout, _ = run_cli(
                directory, "--catch-up-days", "2", refuse="20261112"
            )

            self.assertEqual(code, 0)
            self.assertIn("nothing to collect", stdout)

    def test_a_failed_date_fails_the_run_but_keeps_the_dates_before_it(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)

            code, _, stderr = run_cli(
                directory, "--catch-up-days", "3", refuse="20261111"
            )

            self.assertEqual(code, 1)
            self.assertEqual(snapshot_names(directory), ["2026-11-10.jsonl"])
            self.assertIn("2026-11-11", stderr)
            self.assertIn("403", stderr)

    def test_a_window_shorter_than_one_day_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            with self.assertRaises(SystemExit):
                run_cli(directory, "--catch-up-days", "0")

            self.assertEqual(snapshot_names(directory), [])


if __name__ == "__main__":
    unittest.main()
