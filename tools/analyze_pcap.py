"""Compare unencrypted network metadata from per-app packet captures.

Each capture should hold one app's traffic, e.g. a PCAPdroid capture with the
app filter set to Toss or to the KIS app.  Only headers and plaintext TLS/QUIC/
DNS fields are read; payloads are never decrypted or written to the report.

Example:
  python3 tools/analyze_pcap.py toss=captures/net/toss-01.pcap kis=captures/net/kis-01.pcap \\
      --marks toss=captures/net/toss-01.marks --marks kis=captures/net/kis-01.marks \\
      --out captures/net/report-01.md --json captures/net/report-01.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stocklab import netmeta  # noqa: E402


def _split_label(value: str) -> tuple[str, Path]:
    label, separator, path = value.partition("=")
    if not separator:
        return Path(value).stem, Path(value)
    if not label or not path:
        raise argparse.ArgumentTypeError(f"use LABEL=PATH: {value!r}")
    return label, Path(path)


def _write_new(path: Path, text: str, force: bool) -> None:
    path = path.expanduser()
    if path.exists() and not force:
        raise SystemExit(f"refusing to overwrite {path}; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("captures", nargs="+", type=_split_label,
                        help="LABEL=PATH (or PATH) of a .pcap/.pcapng holding one app's traffic")
    parser.add_argument("--marks", action="append", type=_split_label, default=[],
                        help="LABEL=PATH of tap times from tools/tap_metronome.py; other activity is shown as background")
    parser.add_argument("--idle-gap", type=float, default=netmeta.DEFAULT_IDLE_GAP_S,
                        help="seconds of client silence that separate actions (default %(default)s)")
    parser.add_argument("--max-window", type=float, default=netmeta.DEFAULT_MAX_WINDOW_S,
                        help="longest response window per action in seconds (default %(default)s)")
    parser.add_argument("--chain-slack", type=float, default=netmeta.DEFAULT_CHAIN_SLACK_S,
                        help="seconds after a response within which a new request may depend on it "
                             "(serial round-trip depth, default %(default)s)")
    parser.add_argument("--tz", default="Asia/Seoul", help="time zone for report times and HH:MM:SS marks")
    parser.add_argument("--out", type=Path, help="write the Markdown report here instead of stdout")
    parser.add_argument("--json", type=Path, help="also write the full JSON summary here")
    parser.add_argument("--force", action="store_true", help="overwrite existing --out/--json files")
    args = parser.parse_args(argv)

    if args.idle_gap <= 0 or args.max_window <= 0:
        parser.error("--idle-gap and --max-window must be positive")
    if args.chain_slack < 0:
        parser.error("--chain-slack must not be negative")
    try:
        tz = netmeta.resolve_timezone(args.tz)
    except ValueError as exc:
        parser.error(str(exc))
    labels = [label for label, _ in args.captures]
    if len(set(labels)) != len(labels):
        parser.error("capture labels must be unique")
    marks_by_label = {}
    for label, path in args.marks:
        if label not in labels:
            parser.error(f"--marks label {label!r} does not match a capture label")
        marks_by_label[label] = path

    reports = []
    for label, path in args.captures:
        path = path.expanduser()
        if not path.is_file():
            parser.error(f"capture not found: {path}")
        try:
            packets = netmeta.read_capture(path.read_bytes())
        except ValueError as exc:
            parser.error(f"{path}: {exc}")
        if not packets:
            parser.error(f"{path}: no TCP/UDP packets found")
        marks = None
        if label in marks_by_label:
            marks_path = marks_by_label[label].expanduser()
            if not marks_path.is_file():
                parser.error(f"marks file not found: {marks_path}")
            try:
                marks = netmeta.parse_marks(marks_path.read_text(encoding="utf-8"),
                                            reference_ts=packets[0].ts, tz=tz)
            except ValueError as exc:
                parser.error(f"{marks_path}: {exc}")
        reports.append(netmeta.analyze_packets(
            packets, label, source=path.name, marks=marks,
            idle_gap=args.idle_gap, max_window=args.max_window, chain_slack=args.chain_slack,
        ))

    markdown = netmeta.render_markdown(reports, tz)
    if args.out:
        _write_new(args.out, markdown, args.force)
        print(f"report: {args.out}")
    else:
        sys.stdout.write(markdown)
    if args.json:
        _write_new(args.json, json.dumps(reports, ensure_ascii=False, indent=2) + "\n", args.force)
        print(f"json: {args.json}", file=sys.stderr if not args.out else sys.stdout)
    for report in reports:
        if report["marks"] and report["marks"]["missed"]:
            print(f"warning: {report['label']}: {report['marks']['missed']} marked taps had no matching "
                  "network action; check clock sync or --idle-gap", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
