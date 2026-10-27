"""Fit visible latency against added RTT for one RTT-step session.

Reads the session's own SQLite file (read-only, demo rows excluded) and the
RTT ledger from tools/make_rtt_ledger.py.  The slope k is the extra screen
time per millisecond of planned added round trip: RTT sensitivity in this
setup, not a direct count of requests or round trips.  Baseline RTT values
are optional context only; see docs/ARCHITECTURE_INFERENCE.md.

Example:
  python3 tools/fit_rtt_slope.py --db data/rtt-20261030-search.sqlite3 \\
      --ledger rtt-ledger-20261030.csv --base-rtt toss=24 --base-rtt kis=19 \\
      --out captures/rtt/report.md --json captures/rtt/report.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stocklab import rttfit  # noqa: E402


def read_observations(db_path: Path) -> list[dict]:
    """Open the session database read-only and keep only real observations."""
    path = db_path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"측정 데이터베이스를 찾을 수 없습니다: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM observations WHERE source IN ('manual', 'imported') ORDER BY id"
        )]
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"측정 데이터베이스를 읽지 못했습니다: {exc}") from exc
    finally:
        connection.close()


def _base_rtt(value: str) -> tuple[str, float]:
    app, separator, number = value.partition("=")
    try:
        rtt = float(number)
    except ValueError:
        rtt = math.nan
    if not separator or app not in rttfit.APPS or not math.isfinite(rtt) or rtt <= 0:
        raise argparse.ArgumentTypeError(f"use toss=MS or kis=MS with MS > 0: {value!r}")
    return app, rtt


def _write_new(path: Path, text: str, force: bool) -> None:
    path = path.expanduser()
    if path.exists() and not force:
        raise SystemExit(f"refusing to overwrite {path}; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, required=True, help="SQLite file of this RTT session only")
    parser.add_argument("--ledger", type=Path, required=True, help="RTT ledger CSV with every outcome filled in")
    parser.add_argument("--base-rtt", action="append", type=_base_rtt, default=[],
                        help="APP=MS optional baseline RTT context retained in the report; "
                             "does not identify network time shares or affect the fit")
    parser.add_argument("--out", type=Path, help="write the Markdown report here instead of stdout")
    parser.add_argument("--json", type=Path, help="also write the full JSON result here")
    parser.add_argument("--force", action="store_true", help="overwrite existing --out/--json files")
    args = parser.parse_args(argv)

    base = dict(args.base_rtt)
    if len(base) != len(args.base_rtt):
        parser.error("give --base-rtt at most once per app")
    ledger_path = args.ledger.expanduser()
    if not ledger_path.is_file():
        parser.error(f"ledger not found: {ledger_path}")
    try:
        ledger = rttfit.read_ledger(ledger_path.read_text(encoding="utf-8"))
        result = rttfit.fit_session(read_observations(args.db), ledger, base_rtt=base)
    except ValueError as exc:
        parser.error(str(exc))

    markdown = rttfit.render_markdown(result)
    if args.out:
        _write_new(args.out, markdown, args.force)
        print(f"report: {args.out}")
    else:
        sys.stdout.write(markdown)
    if args.json:
        _write_new(args.json, json.dumps(result, ensure_ascii=False, indent=2) + "\n", args.force)
        print(f"json: {args.json}", file=sys.stderr if not args.out else sys.stdout)
    for warning in result["warnings"]:
        print(f"warning: {warning['code']} {warning['detail']}".rstrip(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
