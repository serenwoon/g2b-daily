from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from g2b_daily.api import CollectionError, G2BClient


KST = timezone(timedelta(hours=9), name="Asia/Seoul")
SCHEMA_VERSION = 1
INQUIRIES = (("registered", "1"), ("changed", "3"))
SOURCE_FIELDS = (
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
)


def previous_kst_date(now: datetime | None = None) -> date:
    observed = now.astimezone(KST) if now else datetime.now(KST)
    return observed.date() - timedelta(days=1)


def query_window(target_date: date) -> tuple[str, str]:
    begin = datetime.combine(target_date, time.min).strftime("%Y%m%d%H%M")
    end = datetime.combine(target_date, time(23, 59)).strftime("%Y%m%d%H%M")
    return begin, end


def collect_snapshot(
    client: G2BClient,
    target_date: date,
    *,
    collected_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    begin, end = query_window(target_date)
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    observed_via: dict[tuple[str, str], set[str]] = {}
    counts: dict[str, int] = {}

    for label, inquiry_div in INQUIRIES:
        items = client.fetch_window(
            inquiry_div=inquiry_div,
            begin=begin,
            end=end,
        )
        counts[label] = len(items)
        for item in items:
            notice_no = _required_text(item, "bidNtceNo")
            notice_order = _required_text(item, "bidNtceOrd")
            key = (notice_no, notice_order)
            merged[key] = item
            observed_via.setdefault(key, set()).add(label)

    if sum(counts.values()) == 0:
        raise CollectionError(
            "both registered and changed queries returned zero rows; snapshot was not written"
        )

    captured = (collected_at or datetime.now(KST)).astimezone(KST).isoformat(
        timespec="seconds"
    )
    records = [
        _normalize(
            item,
            target_date=target_date,
            collected_at=captured,
            observed_via=sorted(observed_via[key]),
        )
        for key, item in sorted(merged.items())
    ]
    return records, counts


def write_snapshot(records: list[dict[str, Any]], output_path: Path) -> None:
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"snapshot already exists: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _normalize(
    item: dict[str, Any],
    *,
    target_date: date,
    collected_at: str,
    observed_via: list[str],
) -> dict[str, Any]:
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotDate": target_date.isoformat(),
        "collectedAt": collected_at,
        "observedVia": observed_via,
        **{field: _optional_value(item.get(field)) for field in SOURCE_FIELDS},
        "attachmentCount": sum(
            bool(_optional_value(item.get(f"ntceSpecDocUrl{index}")))
            for index in range(1, 11)
        ),
    }
    return record


def _required_text(item: dict[str, Any], field: str) -> str:
    value = _optional_value(item.get(field))
    if value is None:
        raise CollectionError(f"required response field is empty: {field}")
    return str(value)


def _optional_value(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value
