from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from g2b_daily.api import CollectionError, G2BClient
from g2b_daily.collector import collect_snapshot, write_snapshot


def response(items: list[dict[str, object]], total: int) -> dict[str, object]:
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
            "body": {
                "items": {"item": items},
                "numOfRows": "1",
                "pageNo": "1",
                "totalCount": str(total),
            },
        }
    }


class CollectorTests(unittest.TestCase):
    def test_collects_registered_and_changed_without_persisting_urls(self) -> None:
        registered = [
            {
                "bidNtceNo": "N1",
                "bidNtceOrd": "000",
                "bidNtceNm": "first",
                "ntceKindNm": "등록공고",
                "ntceSpecDocUrl1": "https://attachment.invalid/one",
            },
            {
                "bidNtceNo": "N2",
                "bidNtceOrd": "000",
                "bidNtceNm": "second",
                "ntceKindNm": "등록공고",
            },
        ]
        changed = [
            {
                "bidNtceNo": "N1",
                "bidNtceOrd": "000",
                "bidNtceNm": "first",
                "ntceKindNm": "취소공고",
                "ntceSpecDocUrl1": "https://attachment.invalid/one",
                "ntceSpecDocUrl2": "https://attachment.invalid/two",
            }
        ]

        def fetcher(url: str) -> dict[str, object]:
            query = parse_qs(urlparse(url).query)
            inquiry = query["inqryDiv"][0]
            page = int(query["pageNo"][0])
            rows = registered if inquiry == "1" else changed
            page_items = rows[page - 1 : page]
            return response(page_items, len(rows))

        client = G2BClient("encoded%2Bkey", page_size=1, fetcher=fetcher)
        records, counts = collect_snapshot(
            client,
            date(2026, 9, 1),
            collected_at=datetime(
                2026, 9, 2, 0, 30, tzinfo=timezone(timedelta(hours=9))
            ),
        )

        self.assertEqual(counts, {"registered": 2, "changed": 1})
        self.assertEqual(len(records), 2)
        first = next(record for record in records if record["bidNtceNo"] == "N1")
        self.assertEqual(first["ntceKindNm"], "취소공고")
        self.assertEqual(first["observedVia"], ["changed", "registered"])
        self.assertEqual(first["attachmentCount"], 2)
        self.assertNotIn("ntceSpecDocUrl1", first)
        self.assertNotIn("attachment.invalid", json.dumps(first))

    def test_rejects_incomplete_pagination(self) -> None:
        def fetcher(url: str) -> dict[str, object]:
            return response([], 1)

        client = G2BClient("key", page_size=100, fetcher=fetcher)
        with self.assertRaisesRegex(CollectionError, "pagination incomplete"):
            client.fetch_window(
                inquiry_div="1",
                begin="202609010000",
                end="202609012359",
            )

    def test_rejects_api_error(self) -> None:
        def fetcher(url: str) -> dict[str, object]:
            return {
                "header": {"resultCode": "30", "resultMsg": "bad key"},
                "body": {},
            }

        client = G2BClient("key", fetcher=fetcher)
        with self.assertRaisesRegex(CollectionError, "data.go.kr error 30"):
            client.fetch_window(
                inquiry_div="3",
                begin="202609010000",
                end="202609012359",
            )

    def test_snapshot_is_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "2026-09-01.jsonl"
            write_snapshot([{"schemaVersion": 1}], path)
            with self.assertRaises(FileExistsError):
                write_snapshot([{"schemaVersion": 2}], path)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"schemaVersion": 1},
            )


if __name__ == "__main__":
    unittest.main()
