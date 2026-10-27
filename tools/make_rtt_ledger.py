"""Write a preplanned RTT-step session ledger as CSV.

Each round runs every delay level once, in an order shuffled from the session
ID, so a slow drift over the session cannot line up with one level.  Every
block holds four attempts (two Toss/KIS pairs); a level alternates
Toss-KIS-KIS-Toss and KIS-Toss-Toss-KIS across rounds.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
import re
import sys


FIELDS = (
    "round",
    "block",
    "position",
    "run_id",
    "app",
    "added_rtt_ms",
    "network_type",
    "ping_rtt_ms",
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


def plan(session_id: str, levels: list[int], rounds: int) -> list[dict]:
    seed = int.from_bytes(hashlib.sha256(session_id.encode("ascii")).digest()[:8], "big")
    rng = random.Random(seed)
    rows, block = [], 0
    for round_number in range(1, rounds + 1):
        order = list(levels)
        rng.shuffle(order)
        for level in order:
            block += 1
            toss_first = (round_number + sorted(levels).index(level)) % 2 == 0
            apps = ("toss", "kis", "kis", "toss") if toss_first else ("kis", "toss", "toss", "kis")
            for position, app in enumerate(apps, start=1):
                rows.append({
                    "round": round_number,
                    "block": block,
                    "position": position,
                    "run_id": f"{session_id}-{block:02d}{'a' if position <= 2 else 'b'}",
                    "app": app,
                    "added_rtt_ms": level,
                    "network_type": f"mac-hotspot+{level}ms",
                })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True,
                        help="unique session prefix, for example 20261030-rtt-search")
    parser.add_argument("--levels", default="0,100,200,300",
                        help="added round-trip delays in ms, comma separated (default %(default)s)")
    parser.add_argument("--rounds", type=int, default=4,
                        help="times each level is visited (default %(default)s)")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", args.session_id):
        parser.error("--session-id must be 3–64 ASCII letters, numbers, _ or -")
    try:
        levels = [int(part) for part in args.levels.split(",")]
    except ValueError:
        parser.error("--levels must be whole milliseconds, e.g. 0,100,200,300")
    if len(set(levels)) != len(levels) or not 2 <= len(levels) <= 8:
        parser.error("--levels needs 2–8 distinct values")
    if 0 not in levels or any(not 0 <= level <= 1000 for level in levels):
        parser.error("--levels must include 0 (the ping baseline) and stay within 0–1000")
    if not 2 <= args.rounds <= 10 or len(levels) * args.rounds > 40:
        parser.error("--rounds must be 2–10 with at most 40 blocks in total")

    writer = csv.DictWriter(sys.stdout, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(plan(args.session_id, levels, args.rounds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
