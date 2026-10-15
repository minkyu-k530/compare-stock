"""Write the preplanned 15-block, 60-attempt study ledger as CSV."""

from __future__ import annotations

import argparse
import csv
import re
import sys


FIELDS = (
    "block",
    "position",
    "run_id",
    "app",
    "actual_local_time",
    "video_file",
    "video_sha256",
    "outcome",
    "reason",
    "capture_method",
    "timing_basis",
    "start_frame",
    "end_frame",
    "start_pts_ms",
    "end_pts_ms",
    "notes",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session-id",
        required=True,
        help="Unique session prefix, for example 20261015-wifi-search",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", args.session_id):
        parser.error("--session-id must be 3–64 ASCII letters, numbers, _ or -")

    writer = csv.DictWriter(sys.stdout, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for block in range(1, 16):
        order = ("toss", "kis", "kis", "toss") if block % 2 else (
            "kis", "toss", "toss", "kis"
        )
        for position, app in enumerate(order, start=1):
            pair = "a" if position <= 2 else "b"
            writer.writerow(
                {
                    "block": block,
                    "position": position,
                    "run_id": f"{args.session_id}-{block:02d}{pair}",
                    "app": app,
                }
            )


if __name__ == "__main__":
    main()
