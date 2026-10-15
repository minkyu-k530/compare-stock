"""Cue taps at a fixed interval and save the cue times as a marks file.

Run it on the Mac next to the phone while PCAPdroid captures one app.  Tap the
stock in the search results when the terminal shows TAP, go back once the
detail screen has settled, and wait for the next cue.  The marks file lets
tools/analyze_pcap.py separate these taps from background traffic.  Phone and
Mac clocks should both use automatic network time.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new marks file, e.g. captures/net/toss-01.marks")
    parser.add_argument("--count", type=int, default=10, help="number of taps (default %(default)s)")
    parser.add_argument("--interval", type=float, default=15.0, help="seconds between cues (default %(default)s)")
    parser.add_argument("--lead", type=float, default=5.0, help="seconds before the first cue (default %(default)s)")
    args = parser.parse_args(argv)
    if args.count < 1 or args.interval < 5 or args.lead < 0:
        parser.error("use --count >= 1, --interval >= 5 and --lead >= 0")
    out = args.out.expanduser()
    if out.exists():
        parser.error(f"refusing to overwrite {out}; choose a new file name")
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"{args.count} taps, every {args.interval:g}s. First cue in {args.lead:g}s. Ctrl+C stops.")
    origin = time.monotonic() + args.lead
    with out.open("x", encoding="utf-8") as marks:
        marks.write("# local wall-clock time when each TAP cue was shown\n")
        try:
            for number in range(1, args.count + 1):
                target = origin + (number - 1) * args.interval
                warned = False
                while (remaining := target - time.monotonic()) > 0:
                    if remaining <= 3 and not warned:
                        print(f"  [{number:02d}/{args.count:02d}] ready...", flush=True)
                        warned = True
                    time.sleep(min(remaining, 0.02))
                stamp = datetime.now().astimezone()
                print(f"\a[{number:02d}/{args.count:02d}] TAP NOW  {stamp:%H:%M:%S}.{stamp.microsecond // 1000:03d}",
                      flush=True)
                marks.write(stamp.isoformat(timespec="milliseconds") + "\n")
                marks.flush()
        except KeyboardInterrupt:
            print("\nstopped; cues shown so far are saved", file=sys.stderr)
    print(f"marks: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
