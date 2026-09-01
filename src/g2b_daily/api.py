from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen


BASE_URL = (
    "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/"
    "getBidPblancListInfoServc"
)


class CollectionError(RuntimeError):
    """Raised when a complete, trustworthy collection cannot be produced."""


JsonFetcher = Callable[[str], Mapping[str, Any]]


class G2BClient:
    def __init__(
        self,
        service_key: str,
        *,
        page_size: int = 100,
        timeout: int = 30,
        fetcher: JsonFetcher | None = None,
    ) -> None:
        if not service_key.strip():
            raise ValueError("service_key must not be empty")
        if not 1 <= page_size <= 9999:
            raise ValueError("page_size must be between 1 and 9999")

        # data.go.kr shows both encoded and decoded keys. Normalizing once avoids
        # double-encoding an encoded key when urlencode builds the request.
        self._service_key = unquote(service_key.strip())
        self.page_size = page_size
        self.timeout = timeout
        self._fetcher = fetcher or self._fetch_json

    def fetch_window(
        self,
        *,
        inquiry_div: str,
        begin: str,
        end: str,
    ) -> list[dict[str, Any]]:
        if inquiry_div not in {"1", "3"}:
            raise ValueError("inquiry_div must be '1' (registered) or '3' (changed)")

        first_items, total_count = self._fetch_page(
            inquiry_div=inquiry_div,
            begin=begin,
            end=end,
            page_no=1,
        )
        items = list(first_items)
        page_count = max(1, math.ceil(total_count / self.page_size))

        for page_no in range(2, page_count + 1):
            page_items, page_total = self._fetch_page(
                inquiry_div=inquiry_div,
                begin=begin,
                end=end,
                page_no=page_no,
            )
            if page_total != total_count:
                raise CollectionError(
                    "totalCount changed during pagination; snapshot was not written"
                )
            items.extend(page_items)

        if len(items) != total_count:
            raise CollectionError(
                f"pagination incomplete: expected {total_count}, received {len(items)}"
            )

        keys = [(item.get("bidNtceNo"), item.get("bidNtceOrd")) for item in items]
        if len(keys) != len(set(keys)):
            raise CollectionError(
                "duplicate notice keys detected during pagination; snapshot was not written"
            )

        return items

    def _fetch_page(
        self,
        *,
        inquiry_div: str,
        begin: str,
        end: str,
        page_no: int,
    ) -> tuple[list[dict[str, Any]], int]:
        query = urlencode(
            {
                "serviceKey": self._service_key,
                "pageNo": page_no,
                "numOfRows": self.page_size,
                "type": "json",
                "inqryDiv": inquiry_div,
                "inqryBgnDt": begin,
                "inqryEndDt": end,
            }
        )
        payload = self._fetcher(f"{BASE_URL}?{query}")
        return self._parse_page(payload)

    def _fetch_json(self, url: str) -> Mapping[str, Any]:
        request = Request(url, headers={"User-Agent": "g2b-daily/0.1"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raise CollectionError(f"data.go.kr returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise CollectionError("data.go.kr request failed") from exc

        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectionError("data.go.kr returned a non-JSON response") from exc
        if not isinstance(payload, Mapping):
            raise CollectionError("data.go.kr returned an unexpected JSON root")
        return payload

    @staticmethod
    def _parse_page(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int]:
        root = payload.get("response", payload)
        if not isinstance(root, Mapping):
            raise CollectionError("response object is missing")

        header = root.get("header")
        body = root.get("body")
        if not isinstance(header, Mapping) or not isinstance(body, Mapping):
            raise CollectionError("response header/body is missing")

        result_code = str(header.get("resultCode", ""))
        if result_code not in {"00", "0"}:
            result_msg = str(header.get("resultMsg", "unknown API error"))
            raise CollectionError(f"data.go.kr error {result_code}: {result_msg}")

        try:
            total_count = int(body.get("totalCount", 0))
        except (TypeError, ValueError) as exc:
            raise CollectionError("totalCount is not an integer") from exc
        if total_count < 0:
            raise CollectionError("totalCount must not be negative")

        item_block = body.get("items")
        if item_block in (None, ""):
            items: Any = []
        elif isinstance(item_block, Mapping):
            items = item_block.get("item", [])
        else:
            items = item_block

        if isinstance(items, Mapping):
            items = [items]
        if items is None:
            items = []
        if not isinstance(items, list) or not all(isinstance(item, Mapping) for item in items):
            raise CollectionError("response items have an unexpected shape")

        return [dict(item) for item in items], total_count
