"""Add a fixed round-trip delay for phones on this Mac's Internet Sharing Wi-Fi.

macOS dummynet pipes, attached through pf to the Internet Sharing bridge
(bridge100 by default), hold the phone's packets for the requested time.  All
phone traffic is delayed, DNS included, so every app sees the same added RTT.
The 0 ms level still passes through a 0 ms pipe so all levels share one path.

The rules live in their own anchor under Apple's "com.apple/*" anchor point
and use their own pipe numbers; the rest of the pf configuration is untouched.
pf is enabled with a reference token (pfctl -E) that `off` releases, so pf
stays on if Internet Sharing or another service still holds it.

Privileged commands run through sudo; --dry-run prints them instead.  The
script cannot see whether the delay reached the phone, so check every level
with `ping` (adb over USB) before measuring.

  python3 tools/hotspot_delay.py on --delay-ms 100 --ping 1.1.1.1
  python3 tools/hotspot_delay.py ping 1.1.1.1
  python3 tools/hotspot_delay.py status
  python3 tools/hotspot_delay.py off
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
from statistics import median
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
ANCHOR = "com.apple/stocklab-delay"
PIPE_IN, PIPE_OUT = 17101, 17102
STATE_PATH = ROOT / "captures" / ".hotspot-delay.json"
MAX_DELAY_MS = 1000
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}")
_PING_TIME = re.compile(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms")
_TOKEN = re.compile(r"Token\s*:\s*(\d+)")

Command = tuple[list[str], str | None]


def on_commands(delay_ms: int, interface: str, direction: str) -> list[Command]:
    """dnctl/pfctl steps that set the pipes and load the anchor's dummynet rules.

    `in` holds the whole delay on the phone's uplink, which pf always sees as
    traffic arriving on the bridge.  `both` splits it across uplink and downlink.
    """
    if direction == "in":
        delays = [(PIPE_IN, "in", delay_ms)]
    else:
        up = delay_ms // 2
        delays = [(PIPE_IN, "in", up), (PIPE_OUT, "out", delay_ms - up)]
    commands: list[Command] = [
        (["dnctl", "pipe", str(pipe), "config", "delay", str(ms)], None) for pipe, _, ms in delays
    ]
    rules = "".join(f"dummynet {way} quick on {interface} all pipe {pipe}\n" for pipe, way, _ in delays)
    commands.append((["pfctl", "-a", ANCHOR, "-f", "-"], rules))
    return commands


def off_commands(token: str | None) -> list[Command]:
    commands: list[Command] = [
        (["pfctl", "-a", ANCHOR, "-F", "dummynet"], None),
        (["dnctl", "pipe", "delete", str(PIPE_IN)], None),
        (["dnctl", "pipe", "delete", str(PIPE_OUT)], None),
    ]
    if token:
        commands.append((["pfctl", "-X", token], None))
    return commands


def status_commands() -> list[Command]:
    return [
        (["pfctl", "-a", ANCHOR, "-s", "dummynet"], None),
        (["dnctl", "pipe", "show", str(PIPE_IN)], None),
        (["dnctl", "pipe", "show", str(PIPE_OUT)], None),
    ]


def ping_command(adb: str, host: str, count: int) -> list[str]:
    if not _HOST.fullmatch(host):
        raise ValueError(f"not a host name or IP address: {host!r}")
    return [adb, "shell", "ping", "-c", str(count), "-i", "0.2", host]


def parse_token(output: str) -> str | None:
    match = _TOKEN.search(output)
    return match.group(1) if match else None


def parse_ping(output: str) -> list[float]:
    return [float(value) for value in _PING_TIME.findall(output)]


def _show(command: Command, privileged: bool = True) -> str:
    argv, stdin = command
    text = shlex.join((["sudo"] if privileged else []) + argv)
    if stdin is not None:
        text = f"printf %s {shlex.quote(stdin)} | {text}"
    return text


def _run(command: Command, *, check: bool = True) -> subprocess.CompletedProcess:
    argv, stdin = command
    prefix = [] if os.geteuid() == 0 else ["sudo"]
    result = subprocess.run(prefix + argv, input=stdin, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise SystemExit(f"failed: {_show(command)}\n{result.stderr.strip() or result.stdout.strip()}")
    return result


def _boot_epoch() -> int | None:
    result = subprocess.run(["sysctl", "-n", "kern.boottime"], text=True, capture_output=True)
    match = re.search(r"sec = (\d+)", result.stdout)
    return int(match.group(1)) if match else None


def _load_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read {STATE_PATH}: {exc}; check `status`, then delete the file")
    boot = _boot_epoch()
    if state.get("token") and boot is not None and state.get("enabled_epoch", 0) < boot:
        state.pop("token")  # pf references do not survive a restart
    return state


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _ping(adb: str, host: str, count: int, dry_run: bool) -> None:
    command = ping_command(adb, host, count)
    if dry_run:
        print(_show((command, None), privileged=False))
        return
    result = subprocess.run(command, text=True, capture_output=True)
    times = parse_ping(result.stdout)
    if not times:
        raise SystemExit(f"ping {host}: no replies (adb exit {result.returncode})\n"
                         f"{(result.stderr or result.stdout).strip()}")
    print(f"ping {host}: {len(times)}/{count} replies, median {median(times):.1f} ms, "
          f"min {min(times):.1f} ms, max {max(times):.1f} ms")
    print(f"  → record ping_rtt_ms = {median(times):.1f} for this block in the ledger")


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    default_adb = ROOT / "platform-tools" / "adb"
    common.add_argument("--adb", default=str(default_adb) if default_adb.exists() else "adb",
                        help="adb used for ping (default: the repository's platform-tools/adb if present)")
    pinging = argparse.ArgumentParser(add_help=False)
    pinging.add_argument("--count", type=int, default=10, help="ping count, 1–100 (default %(default)s)")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    on = commands.add_parser("on", parents=[common, pinging],
                             help="set the added round-trip delay (0 keeps the same path with no delay)")
    on.add_argument("--delay-ms", type=int, required=True, help=f"added round-trip delay, 0–{MAX_DELAY_MS} ms")
    on.add_argument("--interface", default="bridge100", help="Internet Sharing interface (default %(default)s)")
    on.add_argument("--direction", choices=("in", "both"), default="in",
                    help="hold the whole delay on the uplink (in, default) or split it across both directions")
    on.add_argument("--ping", metavar="HOST", help="after applying, ping HOST from the phone via adb")
    ping = commands.add_parser("ping", parents=[common, pinging],
                               help="ping HOST from the phone via adb and print the median RTT")
    ping.add_argument("host")
    commands.add_parser("status", parents=[common], help="show this script's pf rules and pipes")
    commands.add_parser("off", parents=[common], help="remove the rules and pipes and release the pf reference")
    args = parser.parse_args(argv)

    if args.command in ("on", "ping") and not 1 <= args.count <= 100:
        parser.error("--count must be 1–100")
    host = args.ping if args.command == "on" else getattr(args, "host", None)
    if host is not None and not _HOST.fullmatch(host):
        parser.error(f"not a host name or IP address: {host!r}")

    if args.command == "on":
        if not 0 <= args.delay_ms <= MAX_DELAY_MS:
            parser.error(f"--delay-ms must be 0–{MAX_DELAY_MS}")
        if not re.fullmatch(r"[a-z]+[0-9]+", args.interface):
            parser.error(f"not an interface name: {args.interface!r}")
        steps = on_commands(args.delay_ms, args.interface, args.direction)
        state = {} if args.dry_run else _load_state()
        if args.dry_run:
            for step in steps + [(["pfctl", "-E"], None)]:
                print(_show(step))
        else:
            if subprocess.run(["ifconfig", args.interface], capture_output=True).returncode != 0:
                raise SystemExit(f"interface {args.interface} not found: turn on Internet Sharing over Wi-Fi, "
                                 "or pass --interface (see `ifconfig -l`)")
            for step in steps:
                _run(step)
            if not state.get("token"):
                enabled = _run((["pfctl", "-E"], None))
                state["token"] = parse_token(enabled.stdout + enabled.stderr)
                state["enabled_epoch"] = int(datetime.now().timestamp())
                if not state["token"]:
                    print("warning: pfctl -E printed no token; `off` cannot release the pf reference",
                          file=sys.stderr)
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            state.update({"delay_ms": args.delay_ms, "direction": args.direction,
                          "interface": args.interface, "applied_at": stamp})
            _save_state(state)
            print(f"{stamp} added RTT {args.delay_ms} ms on {args.interface} ({args.direction})")
        if args.ping:
            _ping(args.adb, args.ping, args.count, args.dry_run)
    elif args.command == "ping":
        _ping(args.adb, args.host, args.count, args.dry_run)
    elif args.command == "status":
        state = _load_state()
        print(f"state: {json.dumps(state) if state else 'none'}")
        for step in status_commands():
            if args.dry_run:
                print(_show(step))
                continue
            result = _run(step, check=False)
            print(f"$ {_show(step)}\n{(result.stdout or result.stderr).strip() or '(empty)'}")
    else:
        state = {} if args.dry_run else _load_state()
        for step in off_commands(state.get("token")):
            if args.dry_run:
                print(_show(step))
                continue
            result = _run(step, check=False)
            if result.returncode != 0:
                print(f"note: {_show(step)}: {(result.stderr or result.stdout).strip()}", file=sys.stderr)
        if not args.dry_run:
            STATE_PATH.unlink(missing_ok=True)
            print("delay removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
