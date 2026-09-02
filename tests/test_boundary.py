"""Regression guards for the three boundaries stated in README (공개 경계).

The project is publishable *because* of what it refuses to do:

    R1  it does not filter notices by item code, budget or institution
    R2  it does not produce judgements (scores, recommendations, rankings)
    R3  it does not read attachments, and never persists attachment URLs

Those are the properties a future change is most likely to break by accident,
so they are asserted here rather than left to review attention.

Every guard below is behavioural: it drives the real client and collector and
inspects what they request and write. Nothing here scans source text for banned
words -- such a check flags the prose that *describes* the rule as readily as
code that breaks it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from g2b_daily.api import BASE_URL, G2BClient
from g2b_daily.collector import collect_snapshot, write_snapshot


# The exact query parameters the notice-list operation needs. Anything beyond
# this set narrows the collection, which is R1. Kept as a literal on purpose:
# adding a parameter should require editing this line and saying why.
ALLOWED_QUERY_KEYS = {
    "serviceKey",
    "pageNo",
    "numOfRows",
    "type",
    "inqryDiv",
    "inqryBgnDt",
    "inqryEndDt",
}

# The exact keys a snapshot record may carry. A judgement would arrive as a new
# key (score, rank, recommended, ...), so the whitelist catches R2 breaks even
# when they are spelled in a language this file does not anticipate.
ALLOWED_RECORD_KEYS = {
    "schemaVersion",
    "snapshotDate",
    "collectedAt",
    "observedVia",
    "attachmentCount",
    "bidNtceNo",
    "bidNtceOrd",
    "bidNtceNm",
    "ntceKindNm",
    "bidNtceDt",
    "rgstDt",
    "chgDt",
    "bidBeginDt",
    "bidClseDt",
    "opengDt",
    "ntceInsttCd",
    "ntceInsttNm",
    "dminsttCd",
    "dminsttNm",
    "presmptPrce",
    "asignBdgtAmt",
    "bfSpecRgstNo",
}


def response(items: list[dict[str, object]], total: int) -> dict[str, object]:
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
            "body": {
                "items": {"item": items},
                "numOfRows": "100",
                "pageNo": "1",
                "totalCount": str(total),
            },
        }
    }


class RecordingClient:
    """A G2BClient whose requested URLs are captured instead of sent."""

    def __init__(self, items: list[dict[str, object]]) -> None:
        self.urls: list[str] = []
        self._items = items
        self.client = G2BClient("test-key", page_size=100, fetcher=self._fetch)

    def _fetch(self, url: str) -> dict[str, object]:
        self.urls.append(url)
        return response(self._items, len(self._items))

    def query_keys(self) -> set[str]:
        keys: set[str] = set()
        for url in self.urls:
            keys.update(parse_qs(urlparse(url).query, keep_blank_values=True))
        return keys


def notice(no: str, **extra: Any) -> dict[str, Any]:
    item = {"bidNtceNo": no, "bidNtceOrd": "000", "ntceKindNm": "등록공고"}
    item.update(extra)
    return item


class NoFilteringTests(unittest.TestCase):
    """R1 — the collection is not narrowed."""

    def test_request_carries_no_filter_parameter(self) -> None:
        recorder = RecordingClient([notice("N1")])
        recorder.client.fetch_window(
            inquiry_div="1", begin="202609010000", end="202609012359"
        )

        unexpected = recorder.query_keys() - ALLOWED_QUERY_KEYS
        self.assertEqual(
            unexpected,
            set(),
            "R1: the request narrows the collection with "
            f"{sorted(unexpected)}. Filtering by item code, budget or "
            "institution is what this project refuses to do.",
        )

    def test_every_notice_in_the_window_is_kept(self) -> None:
        """A notice is never dropped for being small, unusual or off-topic."""
        items = [
            notice("N1", asignBdgtAmt="1000", pubPrcrmntClsfcNo="00000000"),
            notice("N2", asignBdgtAmt=None, ntceInsttNm="어느 기관"),
            notice("N3", bidNtceNm="관심 없는 용역"),
        ]
        recorder = RecordingClient(items)
        records, counts = collect_snapshot(
            recorder.client, date(2026, 9, 1)
        )

        self.assertEqual({record["bidNtceNo"] for record in records},
                         {"N1", "N2", "N3"})
        self.assertEqual(counts, {"registered": 3, "changed": 3})


class NoJudgementTests(unittest.TestCase):
    """R2 — the snapshot states facts and nothing else."""

    def test_record_carries_no_field_outside_the_declared_schema(self) -> None:
        recorder = RecordingClient([notice("N1", presmptPrce="500")])
        records, _ = collect_snapshot(recorder.client, date(2026, 9, 1))

        self.assertTrue(records)
        for record in records:
            unexpected = set(record) - ALLOWED_RECORD_KEYS
            self.assertEqual(
                unexpected,
                set(),
                f"R2: the record gained {sorted(unexpected)}. A score, rank or "
                "recommendation is a judgement, not a fact the API reported.",
            )

    def test_notice_status_is_preserved_verbatim(self) -> None:
        """'취소공고' stays the API's own word; it is not reclassified."""
        recorder = RecordingClient([notice("N1", ntceKindNm="취소공고")])
        records, _ = collect_snapshot(recorder.client, date(2026, 9, 1))

        self.assertEqual(records[0]["ntceKindNm"], "취소공고")


class NoOriginalDocumentTests(unittest.TestCase):
    """R3 — attachments are counted, never fetched or stored."""

    def test_client_requests_nothing_but_the_notice_list_operation(self) -> None:
        recorder = RecordingClient(
            [notice("N1", ntceSpecDocUrl1="https://attachment.invalid/one")]
        )
        collect_snapshot(recorder.client, date(2026, 9, 1))

        self.assertTrue(recorder.urls)
        for url in recorder.urls:
            self.assertTrue(
                url.startswith(BASE_URL),
                f"R3: the client requested {url!r}, which is not the notice "
                "list operation. Attachments must never be fetched.",
            )

    def test_written_snapshot_holds_no_url_and_no_officer_contact(self) -> None:
        item = notice(
            "N1",
            ntceInsttOfclNm="홍길동",
            ntceInsttOfclTelNo="02-000-0000",
            ntceInsttOfclEmailAdrs="officer@institution.invalid",
            **{f"ntceSpecDocUrl{index}": f"https://attachment.invalid/{index}"
               for index in range(1, 11)},
        )
        recorder = RecordingClient([item])
        records, _ = collect_snapshot(recorder.client, date(2026, 9, 1))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "2026-09-01.jsonl"
            write_snapshot(records, path)
            written = path.read_text(encoding="utf-8")

        for leaked in (
            "attachment.invalid",
            "홍길동",
            "02-000-0000",
            "officer@institution.invalid",
        ):
            self.assertNotIn(
                leaked,
                written,
                f"R3: {leaked!r} reached the snapshot. Only the attachment "
                "count leaves the collector.",
            )
        self.assertEqual(json.loads(written)["attachmentCount"], 10)


class GuardsAreLiveTests(unittest.TestCase):
    """The guards above pass because the code holds, not because they are inert."""

    def test_an_added_query_parameter_would_be_caught(self) -> None:
        recorder = RecordingClient([notice("N1")])
        recorder.urls.append(f"{BASE_URL}?bidNtceNm=%ED%8A%B9%EC%A0%95")
        self.assertNotEqual(recorder.query_keys() - ALLOWED_QUERY_KEYS, set())

    def test_an_added_record_field_would_be_caught(self) -> None:
        self.assertNotEqual({"score"} - ALLOWED_RECORD_KEYS, set())


if __name__ == "__main__":
    unittest.main()
