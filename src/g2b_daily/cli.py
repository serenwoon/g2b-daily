from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from g2b_daily.api import CollectionError, G2BClient
from g2b_daily.collector import (
    collect_snapshot,
    missing_snapshot_dates,
    previous_kst_date,
    write_snapshot,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect public G2B service-notice metadata for one KST date."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="snapshot date in YYYY-MM-DD (default: yesterday in Asia/Seoul)",
    )
    parser.add_argument(
        "--catch-up-days",
        type=int,
        default=1,
        help="without --date: collect every date that has no snapshot among "
        "the last N days ending yesterday (default: 1, yesterday only)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("snapshots"),
        help="snapshot directory (default: snapshots)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="API rows per page (default: 100)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.catch_up_days < 1:
        parser.error("--catch-up-days must be at least 1")

    if args.date:
        output_path = args.output_dir / f"{args.date.isoformat()}.jsonl"
        if output_path.exists():
            print(f"snapshot already exists; leaving it unchanged: {output_path}")
            return 0
        targets = [args.date]
    else:
        targets = missing_snapshot_dates(
            args.output_dir, previous_kst_date(), args.catch_up_days
        )
        if not targets:
            print(
                f"no snapshot is missing in the last {args.catch_up_days} day(s); "
                "nothing to collect"
            )
            return 0

    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not service_key:
        print("DATA_GO_KR_SERVICE_KEY is required", file=sys.stderr)
        return 2

    # Oldest first, stopping at the first failure: the dates already written
    # stay written, and the next run picks up from the date that failed.
    for target_date in targets:
        output_path = args.output_dir / f"{target_date.isoformat()}.jsonl"
        try:
            client = G2BClient(service_key, page_size=args.page_size)
            records, counts = collect_snapshot(client, target_date)
            write_snapshot(records, output_path)
        except (CollectionError, FileExistsError, ValueError) as exc:
            print(f"collection failed for {target_date}: {exc}", file=sys.stderr)
            return 1

        print(
            f"wrote {len(records)} unique notices to {output_path} "
            f"(registered={counts['registered']}, changed={counts['changed']})"
        )
    return 0
