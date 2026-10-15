"""Summarize unencrypted network metadata from one app's packet capture.

Input is a classic PCAP or PCAPNG file, for example from PCAPdroid on an
unrooted Android phone (VPN mode, one target app per capture) or from tcpdump
on a Mac hotspot.  The module reads only what is visible without decryption:

* transports: TCP, UDP, QUIC long-header versions, plaintext DNS
* TLS ClientHello/ServerHello fields: SNI, offered ALPN, negotiated version,
  PSK/session resumption, HelloRetryRequest, early-data offers
* connection setup timing, connection reuse, and data-burst timing per action

It never decrypts application data, and its report holds no payload bytes,
HTTP paths, or device addresses.  It cannot tell the HTTP version inside
TLS 1.3 or QUIC, whether a long-lived stream is WebSocket, SSE or gRPC, server
processing time, cache hits, or *why* one app is faster.  Traffic patterns are
heuristics and are labelled as such in the report.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone, tzinfo
import ipaddress
import math
import re
from statistics import median
import struct
from typing import Any

FIN, SYN, RST, ACK = 0x01, 0x02, 0x04, 0x10

LINKTYPE_NULL = 0
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_LOOP = 108
LINKTYPE_LINUX_SLL = 113
LINKTYPE_IPV4 = 228
LINKTYPE_IPV6 = 229
LINKTYPE_LINUX_SLL2 = 276
_RAW_LINKTYPES = frozenset({LINKTYPE_RAW, 12, 14, LINKTYPE_IPV4, LINKTYPE_IPV6})

_PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", 1e-6),
    b"\xa1\xb2\xc3\xd4": (">", 1e-6),
    b"\x4d\x3c\xb2\xa1": ("<", 1e-9),
    b"\xa1\xb2\x3c\x4d": (">", 1e-9),
}
_PCAPNG_SHB = b"\x0a\x0d\x0d\x0a"
# PCAPdroid "extensions"/trailer: magic, uid, 20-byte app name after the IP packet.
_PCAPDROID_MAGICS = ((b"\x01\x07\x20\x21", "big"), (b"\x21\x20\x07\x01", "little"))

#: Server data separated by more than this gap starts a new burst.
BURST_GAP_S = 0.25
#: Small client data this soon after server data counts as a reaction (window
#: update, ping/pong) rather than a new request.
REACTION_SLACK_S = 0.10
SMALL_CONTROL_BYTES = 80
#: Default quiet period that separates user actions.
DEFAULT_IDLE_GAP_S = 2.0
#: Default upper bound of the response window after an action starts.
DEFAULT_MAX_WINDOW_S = 8.0
#: A marked tap matches the first action that starts this long before/after it.
MARK_BEFORE_S = 1.0
MARK_AFTER_S = 2.5
#: Server-initiated bursts needed before a connection is called push-like.
PUSH_MIN_UNSOLICITED = 3
PUSH_MIN_DURATION_S = 3.0

_KEEP_SEGMENTS = 48
_KEEP_DATAGRAMS = 64
_SERVER_PORTS = frozenset({53, 80, 123, 443, 853, 1883, 3478, 5222, 5228, 8080, 8443, 8883})
_HRR_RANDOM = bytes.fromhex(
    "cf21ad74e59a6111be1d8c021e65b891c2a211167abb8c5e079e09e2c8a8339c"
)
_TLS_VERSIONS = {0x0301: "TLS 1.0", 0x0302: "TLS 1.1", 0x0303: "TLS 1.2", 0x0304: "TLS 1.3"}
_HTTP_METHODS = (b"GET ", b"POST ", b"PUT ", b"HEAD ", b"DELETE ", b"OPTIONS ", b"PATCH ", b"CONNECT ")
_QUIC_V2 = 0x6B3343CF


# --------------------------------------------------------------------------
# Capture reading


@dataclass(frozen=True, slots=True)
class Packet:
    ts: float
    src: str
    dst: str
    proto: str
    sport: int
    dport: int
    flags: int
    seq: int
    payload: bytes
    app: str | None = None


def read_capture(data: bytes) -> list[Packet]:
    """Decode TCP/UDP packets from classic PCAP or PCAPNG bytes."""
    if len(data) < 4:
        raise ValueError("capture file is empty or truncated")
    records = _pcapng_records(data) if data[:4] == _PCAPNG_SHB else _pcap_records(data)
    packets = []
    for ts, linktype, frame in records:
        packet = decode_frame(linktype, frame, ts)
        if packet is not None:
            packets.append(packet)
    packets.sort(key=lambda packet: packet.ts)
    return packets


def _pcap_records(data: bytes) -> Iterator[tuple[float, int, bytes]]:
    if data[:4] not in _PCAP_MAGICS:
        raise ValueError("not a PCAP or PCAPNG file")
    if len(data) < 24:
        raise ValueError("truncated PCAP header")
    endian, scale = _PCAP_MAGICS[data[:4]]
    linktype = struct.unpack_from(endian + "I", data, 20)[0] & 0x0FFFFFFF
    offset = 24
    while offset + 16 <= len(data):
        seconds, fraction, captured, _original = struct.unpack_from(endian + "IIII", data, offset)
        offset += 16
        if offset + captured > len(data):
            break  # the last record was cut off while copying
        yield seconds + fraction * scale, linktype, data[offset:offset + captured]
        offset += captured


def _pcapng_records(data: bytes) -> Iterator[tuple[float, int, bytes]]:
    offset, endian = 0, "<"
    interfaces: list[tuple[int, float, float]] = []
    while offset + 12 <= len(data):
        if data[offset:offset + 4] == _PCAPNG_SHB:
            byte_order = data[offset + 8:offset + 12]
            if byte_order == b"\x4d\x3c\x2b\x1a":
                endian = "<"
            elif byte_order == b"\x1a\x2b\x3c\x4d":
                endian = ">"
            else:
                raise ValueError("bad PCAPNG byte-order magic")
            interfaces = []
        block_type, length = struct.unpack_from(endian + "II", data, offset)
        if length < 12 or length % 4 or offset + length > len(data):
            break
        body = data[offset + 8:offset + length - 4]
        if block_type == 1 and len(body) >= 8:  # Interface Description Block
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            unit, ts_offset = 1e-6, 0.0
            for code, value in _pcapng_options(body[8:], endian):
                if code == 9 and value:  # if_tsresol
                    unit = 2.0 ** -(value[0] & 0x7F) if value[0] & 0x80 else 10.0 ** -value[0]
                elif code == 14 and len(value) >= 8:  # if_tsoffset
                    ts_offset = float(struct.unpack_from(endian + "q", value)[0])
            interfaces.append((linktype, unit, ts_offset))
        elif block_type in (6, 2) and len(body) >= 20:  # Enhanced / obsolete Packet Block
            if block_type == 6:
                iface, high, low, captured, _original = struct.unpack_from(endian + "IIIII", body, 0)
            else:
                iface, _drops, high, low, captured, _original = struct.unpack_from(endian + "HHIIII", body, 0)
            if iface < len(interfaces):
                linktype, unit, ts_offset = interfaces[iface]
                yield ((high << 32) | low) * unit + ts_offset, linktype, body[20:20 + captured]
        offset += length


def _pcapng_options(raw: bytes, endian: str) -> Iterator[tuple[int, bytes]]:
    position = 0
    while position + 4 <= len(raw):
        code, length = struct.unpack_from(endian + "HH", raw, position)
        position += 4
        if code == 0:
            return
        yield code, raw[position:position + length]
        position += (length + 3) & ~3


def decode_frame(linktype: int, frame: bytes, ts: float) -> Packet | None:
    """Return the TCP/UDP packet in one link-layer frame, if any."""
    if linktype == LINKTYPE_ETHERNET:
        if len(frame) >= 14:
            ethertype, offset = int.from_bytes(frame[12:14], "big"), 14
            while ethertype in (0x8100, 0x88A8) and len(frame) >= offset + 4:
                ethertype = int.from_bytes(frame[offset + 2:offset + 4], "big")
                offset += 4
            if ethertype in (0x0800, 0x86DD):
                return _decode_ip(frame, offset, ts)
        # PCAPdroid keeps the Ethernet link type but omits the fake header when
        # its trailer does not fit the snap length.
        if frame and frame[0] >> 4 in (4, 6):
            return _decode_ip(frame, 0, ts)
        return None
    if linktype in _RAW_LINKTYPES:
        return _decode_ip(frame, 0, ts)
    if linktype in (LINKTYPE_NULL, LINKTYPE_LOOP):
        return _decode_ip(frame, 4, ts)
    if linktype == LINKTYPE_LINUX_SLL and len(frame) >= 16:
        if int.from_bytes(frame[14:16], "big") in (0x0800, 0x86DD):
            return _decode_ip(frame, 16, ts)
    if linktype == LINKTYPE_LINUX_SLL2 and len(frame) >= 20:
        if int.from_bytes(frame[0:2], "big") in (0x0800, 0x86DD):
            return _decode_ip(frame, 20, ts)
    return None


def _decode_ip(frame: bytes, offset: int, ts: float) -> Packet | None:
    if len(frame) <= offset:
        return None
    version = frame[offset] >> 4
    if version == 4:
        if len(frame) < offset + 20:
            return None
        header = (frame[offset] & 0x0F) * 4
        total = int.from_bytes(frame[offset + 2:offset + 4], "big")
        if int.from_bytes(frame[offset + 6:offset + 8], "big") & 0x1FFF:
            return None  # non-first fragment
        proto = frame[offset + 9]
        src = str(ipaddress.IPv4Address(frame[offset + 12:offset + 16]))
        dst = str(ipaddress.IPv4Address(frame[offset + 16:offset + 20]))
        end = min(offset + total, len(frame)) if total else len(frame)
        l4 = offset + header
    elif version == 6:
        if len(frame) < offset + 40:
            return None
        length = int.from_bytes(frame[offset + 4:offset + 6], "big")
        proto = frame[offset + 6]
        src = str(ipaddress.IPv6Address(frame[offset + 8:offset + 24]))
        dst = str(ipaddress.IPv6Address(frame[offset + 24:offset + 40]))
        end = min(offset + 40 + length, len(frame)) if length else len(frame)
        l4 = offset + 40
        while proto in (0, 43, 44, 60):
            if l4 + 8 > end:
                return None
            if proto == 44:
                if int.from_bytes(frame[l4 + 2:l4 + 4], "big") & 0xFFF8:
                    return None
                proto, l4 = frame[l4], l4 + 8
            else:
                proto, l4 = frame[l4], l4 + (frame[l4 + 1] + 1) * 8
    else:
        return None
    app = _pcapdroid_app(frame[end:])
    if proto == 6:
        if l4 + 20 > end:
            return None
        sport, dport, seq = struct.unpack_from("!HHI", frame, l4)
        data_offset = (frame[l4 + 12] >> 4) * 4
        if data_offset < 20 or l4 + data_offset > end:
            return None
        return Packet(ts, src, dst, "tcp", sport, dport, frame[l4 + 13], seq,
                      bytes(frame[l4 + data_offset:end]), app)
    if proto == 17:
        if l4 + 8 > end:
            return None
        sport, dport, length = struct.unpack_from("!HHH", frame, l4)
        stop = min(end, l4 + length) if length >= 8 else end
        return Packet(ts, src, dst, "udp", sport, dport, 0, 0, bytes(frame[l4 + 8:stop]), app)
    return None


def _pcapdroid_app(tail: bytes) -> str | None:
    for magic, _order in _PCAPDROID_MAGICS:
        index = tail.find(magic)
        if index >= 0 and len(tail) >= index + 28:
            name = tail[index + 8:index + 28].split(b"\0", 1)[0]
            return name.decode("utf-8", "replace") or None
    return None


# --------------------------------------------------------------------------
# Connections


@dataclass
class Flow:
    proto: str
    client: tuple[str, int]
    server: tuple[str, int]
    first_ts: float
    last_ts: float
    syn_ts: float | None = None
    synack_ts: float | None = None
    client_isn: int | None = None
    server_isn: int | None = None
    closed_ts: float | None = None
    packets_up: int = 0
    packets_down: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    events: list[tuple[float, bool, int]] = field(default_factory=list)
    up_segments: list[tuple[int, float, bytes]] = field(default_factory=list)
    down_segments: list[tuple[int, float, bytes]] = field(default_factory=list)
    up_datagrams: list[tuple[float, bytes]] = field(default_factory=list)
    down_datagrams: list[tuple[float, bytes]] = field(default_factory=list)
    apps: set[str] = field(default_factory=set)
    kind: str = "other"
    name: str = ""
    tls: dict[str, Any] | None = None
    quic: dict[str, Any] | None = None
    http: dict[str, Any] | None = None
    dns: list[dict[str, Any]] = field(default_factory=list)
    setup: dict[str, Any] = field(default_factory=dict)
    triggers: list[float] = field(default_factory=list)
    bursts: list[dict[str, Any]] = field(default_factory=list)
    pattern: str = "minimal"
    push_interval_ms: float | None = None

    @property
    def opened_ts(self) -> float | None:
        """Time the client opened this connection, when the capture shows it."""
        if self.proto == "tcp":
            return self.syn_ts
        if self.quic and self.quic["handshake_observed"]:
            return self.quic["initial_ts"]
        return None

    @property
    def app_ready_ts(self) -> float | None:
        """Earliest time the client could send application data, if known."""
        if self.tls:
            return self.tls.get("client_ready_ts")
        if self.quic:
            return self.quic.get("first_response_ts")
        if self.http:
            return self.http.get("request_ts")
        return None


def _orient(packet: Packet) -> tuple[tuple[str, int], tuple[str, int]]:
    """Return (client, server) for the first packet seen on a connection."""
    a, b = (packet.src, packet.sport), (packet.dst, packet.dport)
    if packet.proto == "tcp" and packet.flags & SYN:
        return (b, a) if packet.flags & ACK else (a, b)
    a_server, b_server = packet.sport in _SERVER_PORTS, packet.dport in _SERVER_PORTS
    if b_server and not a_server:
        return a, b
    if a_server and not b_server:
        return b, a
    return (a, b) if packet.dport <= packet.sport else (b, a)


def build_flows(packets: Iterable[Packet]) -> list[Flow]:
    flows: list[Flow] = []
    index: dict[tuple, Flow] = {}
    for packet in packets:
        key = (packet.proto,) + tuple(sorted(((packet.src, packet.sport), (packet.dst, packet.dport))))
        flow = index.get(key)
        pure_syn = packet.proto == "tcp" and packet.flags & SYN and not packet.flags & ACK
        if flow is not None and pure_syn and (
            flow.syn_ts is None or flow.closed_ts is not None or packet.seq != flow.client_isn
        ):
            flow = None  # the 4-tuple was reused for a new connection
        if flow is None:
            client, server = _orient(packet)
            flow = Flow(packet.proto, client, server, packet.ts, packet.ts)
            flows.append(flow)
            index[key] = flow
        up = (packet.src, packet.sport) == flow.client
        flow.last_ts = packet.ts
        if packet.app:
            flow.apps.add(packet.app)
        if packet.proto == "tcp":
            if pure_syn and up and flow.syn_ts is None:
                flow.syn_ts, flow.client_isn = packet.ts, packet.seq
            elif packet.flags & SYN and packet.flags & ACK and not up and flow.synack_ts is None:
                flow.synack_ts, flow.server_isn = packet.ts, packet.seq
            if packet.flags & (FIN | RST) and flow.closed_ts is None:
                flow.closed_ts = packet.ts
        if up:
            flow.packets_up += 1
            flow.bytes_up += len(packet.payload)
        else:
            flow.packets_down += 1
            flow.bytes_down += len(packet.payload)
        if not packet.payload:
            continue
        flow.events.append((packet.ts, up, len(packet.payload)))
        if packet.proto == "tcp":
            segments = flow.up_segments if up else flow.down_segments
            if len(segments) < _KEEP_SEGMENTS:
                segments.append((packet.seq, packet.ts, packet.payload))
        else:
            datagrams = flow.up_datagrams if up else flow.down_datagrams
            if len(datagrams) < _KEEP_DATAGRAMS:
                datagrams.append((packet.ts, packet.payload))
    return flows


def _reassemble(segments: Sequence[tuple[int, float, bytes]], isn: int | None) -> tuple[bytes, list[tuple[int, float]]]:
    """Return the contiguous start of a TCP byte stream and (offset, ts) marks."""
    if not segments:
        return b"", []
    base = (isn + 1) & 0xFFFFFFFF if isn is not None else segments[0][0]
    pieces = sorted(((seq - base) % 2**32, ts, data) for seq, ts, data in segments)
    stream, marks = bytearray(), []
    for relative, ts, data in pieces:
        if relative >= 2**31:
            continue  # before the stream start
        if relative > len(stream):
            break  # a gap: stop at the first missing byte
        if relative + len(data) <= len(stream):
            continue  # retransmission
        marks.append((len(stream), ts))
        stream += data[len(stream) - relative:]
    return bytes(stream), marks


def _ts_at(marks: Sequence[tuple[int, float]], offset: int) -> float | None:
    position = bisect_right([start for start, _ in marks], offset) - 1
    return marks[position][1] if position >= 0 else None


# --------------------------------------------------------------------------
# TLS


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.position = data, 0

    def take(self, size: int) -> bytes:
        if self.position + size > len(self.data):
            raise ValueError("truncated field")
        chunk = self.data[self.position:self.position + size]
        self.position += size
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "big")

    def vector(self, width: int) -> bytes:
        return self.take(int.from_bytes(self.take(width), "big"))

    def left(self) -> int:
        return len(self.data) - self.position


def _extensions(raw: bytes) -> Iterator[tuple[int, bytes]]:
    reader = _Reader(raw)
    while reader.left() >= 4:
        kind = reader.u16()
        yield kind, reader.vector(2)


def _tls_version_name(value: int) -> str:
    return _TLS_VERSIONS.get(value, f"0x{value:04x}")


def _tls_records(stream: bytes) -> list[tuple[int, int, bytes]]:
    records, position = [], 0
    while position + 5 <= len(stream):
        content_type, major = stream[position], stream[position + 1]
        length = int.from_bytes(stream[position + 3:position + 5], "big")
        if content_type not in (20, 21, 22, 23) or major != 3 or length > 18432:
            break
        records.append((position, content_type, stream[position + 5:position + 5 + length]))
        position += 5 + length
    return records


def _handshake_messages(records: Sequence[tuple[int, int, bytes]]) -> list[tuple[int, bytes]]:
    buffer = bytearray()
    for _offset, content_type, body in records:
        if content_type != 22:
            break
        buffer += body
    messages, position = [], 0
    while position + 4 <= len(buffer):
        kind = buffer[position]
        length = int.from_bytes(buffer[position + 1:position + 4], "big")
        messages.append((kind, bytes(buffer[position + 4:position + 4 + length])))
        position += 4 + length
    return messages


def parse_client_hello(body: bytes) -> dict[str, Any]:
    reader = _Reader(body)
    reader.u16()
    reader.take(32)
    session_id = reader.vector(1)
    reader.vector(2)
    reader.vector(1)
    info: dict[str, Any] = {
        "sni": None, "alpn_offered": [], "versions_offered": [], "psk_offered": False,
        "early_data_offered": False, "ech": False, "ticket_offered": False,
        "_session_id": session_id,
    }
    if reader.left() < 2:
        return info
    for kind, data in _extensions(reader.vector(2)):
        try:
            if kind == 0:
                names = _Reader(_Reader(data).vector(2))
                while names.left() >= 3:
                    name_type, name = names.u8(), names.vector(2)
                    if name_type == 0:
                        info["sni"] = name.decode("ascii", "replace")
                        break
            elif kind == 16:
                protocols = _Reader(_Reader(data).vector(2))
                while protocols.left():
                    info["alpn_offered"].append(protocols.vector(1).decode("ascii", "replace"))
            elif kind == 43:
                raw = _Reader(data).vector(1)
                values = (int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2))
                info["versions_offered"] = [
                    _tls_version_name(value) for value in values if value & 0x0F0F != 0x0A0A
                ]
            elif kind == 41:
                info["psk_offered"] = True
            elif kind == 42:
                info["early_data_offered"] = True
            elif kind == 0xFE0D:
                info["ech"] = True
            elif kind == 35 and data:
                info["ticket_offered"] = True
        except ValueError:
            continue
    return info


def parse_server_hello(body: bytes) -> dict[str, Any]:
    reader = _Reader(body)
    legacy = reader.u16()
    random = reader.take(32)
    session_id = reader.vector(1)
    reader.u16()
    reader.u8()
    info: dict[str, Any] = {
        "version": _tls_version_name(legacy), "alpn_selected": None, "psk_accepted": False,
        "hello_retry": random == _HRR_RANDOM, "_server_session_id": session_id,
    }
    if reader.left() >= 2:
        for kind, data in _extensions(reader.vector(2)):
            try:
                if kind == 43 and len(data) >= 2:
                    info["version"] = _tls_version_name(int.from_bytes(data[:2], "big"))
                elif kind == 16:
                    info["alpn_selected"] = _Reader(_Reader(data).vector(2)).vector(1).decode("ascii", "replace")
                elif kind == 41:
                    info["psk_accepted"] = True
            except ValueError:
                continue
    return info


# --------------------------------------------------------------------------
# QUIC and DNS


def quic_header(payload: bytes) -> dict[str, Any] | None:
    """Classify a QUIC long header without decrypting anything."""
    if len(payload) < 7 or not payload[0] & 0x80:
        return None
    version = int.from_bytes(payload[1:5], "big")
    if version == 0:
        return {"version": 0, "type": "version_negotiation"}
    if not payload[0] & 0x40:
        return None
    bits = (payload[0] & 0x30) >> 4
    order = ("retry", "initial", "0rtt", "handshake") if version == _QUIC_V2 else ("initial", "0rtt", "handshake", "retry")
    return {"version": version, "type": order[bits]}


def _quic_version_name(version: int) -> str | None:
    if version == 1:
        return "QUIC v1"
    if version == _QUIC_V2:
        return "QUIC v2"
    if version >> 8 == 0xFF0000:
        return f"QUIC draft-{version & 0xFF}"
    raw = version.to_bytes(4, "big")
    if raw[:1] in (b"Q", b"T") and raw[1:].isdigit():
        return "gQUIC " + raw.decode("ascii")
    return None


def _dns_name(message: bytes, position: int) -> tuple[str, int]:
    labels, resume = [], None
    for _ in range(128):
        if position >= len(message):
            raise ValueError("truncated DNS name")
        length = message[position]
        if length & 0xC0 == 0xC0:
            if position + 1 >= len(message):
                raise ValueError("truncated DNS pointer")
            if resume is None:
                resume = position + 2
            position = ((length & 0x3F) << 8) | message[position + 1]
            continue
        if length == 0:
            return ".".join(labels), resume if resume is not None else position + 1
        labels.append(message[position + 1:position + 1 + length].decode("ascii", "replace"))
        position += 1 + length
    raise ValueError("DNS name loop")


def parse_dns(payload: bytes) -> dict[str, Any] | None:
    if len(payload) < 12:
        return None
    ident, flags, questions, answers_count, _authority, _additional = struct.unpack_from("!HHHHHH", payload)
    result: dict[str, Any] = {
        "id": ident, "response": bool(flags & 0x8000), "rcode": flags & 0x0F,
        "name": None, "qtype": None, "answers": [],
    }
    try:
        position = 12
        for number in range(questions):
            name, position = _dns_name(payload, position)
            if number == 0:
                result["name"] = name
                result["qtype"] = int.from_bytes(payload[position:position + 2], "big")
            position += 4
        if result["response"]:
            for _ in range(answers_count):
                _name, position = _dns_name(payload, position)
                kind, _class, _ttl, length = struct.unpack_from("!HHIH", payload, position)
                position += 10
                data = payload[position:position + length]
                position += length
                if kind == 1 and length == 4:
                    result["answers"].append(str(ipaddress.IPv4Address(data)))
                elif kind == 28 and length == 16:
                    result["answers"].append(str(ipaddress.IPv6Address(data)))
    except (ValueError, struct.error, IndexError):
        pass
    return result


# --------------------------------------------------------------------------
# Per-connection inspection


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000, 3)


def _inspect_tcp(flow: Flow) -> None:
    up, up_marks = _reassemble(flow.up_segments, flow.client_isn)
    down, down_marks = _reassemble(flow.down_segments, flow.server_isn)
    if flow.syn_ts is not None and flow.synack_ts is not None:
        flow.setup["tcp_connect_ms"] = _ms(flow.synack_ts - flow.syn_ts)
    if flow.server[1] == 853:
        flow.kind = "dot"
        return
    if len(up) >= 6 and up[0] == 22 and up[5] == 1:
        flow.kind = "tls"
        tls: dict[str, Any] = {"client_hello_ts": _ts_at(up_marks, 0)}
        records = _tls_records(up)
        messages = _handshake_messages(records)
        if messages and messages[0][0] == 1:
            try:
                tls.update(parse_client_hello(messages[0][1]))
            except ValueError:
                pass
        ready = next((offset for offset, kind, _ in records if kind == 23), None)
        tls["client_ready_ts"] = _ts_at(up_marks, ready) if ready is not None else None
        if len(down) >= 6 and down[0] == 22:
            server_messages = _handshake_messages(_tls_records(down))
            if server_messages and server_messages[0][0] == 2:
                try:
                    tls.update(parse_server_hello(server_messages[0][1]))
                    tls["server_hello_ts"] = _ts_at(down_marks, 0)
                except ValueError:
                    pass
        version = tls.get("version")
        if tls.get("hello_retry"):
            tls["resumed"] = None
        elif version == "TLS 1.3":
            tls["resumed"] = tls.get("psk_accepted", False)
        elif version is not None:
            client_id, server_id = tls.get("_session_id"), tls.get("_server_session_id")
            tls["resumed"] = bool(client_id) and client_id == server_id
        if tls.get("client_hello_ts") is not None and tls.get("server_hello_ts") is not None:
            flow.setup["tls_hello_ms"] = _ms(tls["server_hello_ts"] - tls["client_hello_ts"])
        if flow.syn_ts is not None and tls["client_ready_ts"] is not None:
            flow.setup["setup_ms"] = _ms(tls["client_ready_ts"] - flow.syn_ts)
            flow.setup["setup_basis"] = "SYN → 클라이언트 첫 암호화 레코드"
        flow.tls = tls
    elif up.startswith(_HTTP_METHODS):
        flow.kind = "http"
        head = up.split(b"\r\n\r\n", 1)[0][:8192].decode("latin-1").split("\r\n")
        headers = {}
        for line in head[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        flow.http = {
            "method": head[0].split(" ", 1)[0],
            "host": headers.get("host", "").split(":")[0] or None,
            "upgrade": headers.get("upgrade"),
            "request_ts": _ts_at(up_marks, 0),
        }
        if "tcp_connect_ms" in flow.setup:
            flow.setup["setup_ms"] = flow.setup["tcp_connect_ms"]
            flow.setup["setup_basis"] = "SYN → SYN/ACK"
    else:
        flow.kind = "tcp"


def _inspect_udp(flow: Flow) -> None:
    if flow.server[1] == 53:
        flow.kind = "dns"
        pending: dict[int, dict[str, Any]] = {}
        for ts, payload in flow.up_datagrams:
            query = parse_dns(payload)
            if query and not query["response"]:
                entry = {"ts": ts, "name": query["name"], "qtype": query["qtype"], "ms": None, "answers": []}
                flow.dns.append(entry)
                pending[query["id"]] = entry
        for ts, payload in flow.down_datagrams:
            answer = parse_dns(payload)
            if answer and answer["response"] and answer["id"] in pending:
                entry = pending.pop(answer["id"])
                entry["ms"] = _ms(ts - entry["ts"])
                entry["answers"] = answer["answers"]
                entry["rcode"] = answer["rcode"]
        return
    up_headers = [(ts, quic_header(payload)) for ts, payload in flow.up_datagrams]
    versions = {
        _quic_version_name(header["version"])
        for _, header in up_headers if header and header["version"]
    } - {None}
    if not versions:
        flow.kind = "udp"
        return
    flow.kind = "quic"
    initial_ts = next((ts for ts, header in up_headers if header and header["type"] == "initial"), None)
    first_response = next(
        (ts for ts, _ in flow.down_datagrams if initial_ts is not None and ts >= initial_ts), None
    )
    down_headers = [quic_header(payload) for _, payload in flow.down_datagrams]
    zero_rtt = any(header and header["type"] == "0rtt" for _, header in up_headers)
    flow.quic = {
        "versions": sorted(versions),
        "handshake_observed": bool(up_headers) and bool(up_headers[0][1])
        and up_headers[0][1]["type"] == "initial",
        "initial_ts": initial_ts,
        "first_response_ts": first_response,
        "zero_rtt": zero_rtt,
        "retry": any(header and header["type"] == "retry" for header in down_headers),
        "version_negotiation": any(header and header["version"] == 0 for header in down_headers),
    }
    if initial_ts is not None and first_response is not None:
        flow.setup["first_response_ms"] = _ms(first_response - initial_ts)
        flow.setup["setup_ms"] = 0.0 if zero_rtt else flow.setup["first_response_ms"]
        flow.setup["setup_basis"] = (
            "0-RTT 데이터 관찰" if zero_rtt else "Initial → 서버 첫 응답 (1-RTT 핸드셰이크 추정)"
        )


def _classify(flow: Flow) -> None:
    """Mark client requests, server bursts, and a heuristic traffic pattern."""
    down_times = [ts for ts, up, _ in flow.events if not up]
    triggers = []
    for ts, up, size in flow.events:
        if not up:
            continue
        previous = bisect_right(down_times, ts) - 1
        reactive = (
            flow.kind != "dns" and size <= SMALL_CONTROL_BYTES and previous >= 0
            and ts - down_times[previous] <= REACTION_SLACK_S
        )
        if not reactive:
            triggers.append(ts)
    if flow.syn_ts is not None:
        triggers.append(flow.syn_ts)
    flow.triggers = sorted(triggers)

    timeline = sorted([(ts, 0, 0) for ts in flow.triggers] +
                      [(ts, 1, size) for ts, up, size in flow.events if not up])
    bursts: list[dict[str, Any]] = []
    current, requested = None, False
    for ts, is_down, size in timeline:
        if not is_down:
            requested, current = True, None
            continue
        if current is not None and ts - current["end"] <= BURST_GAP_S:
            current["end"] = ts
            current["bytes"] += size
            continue
        current = {"start": ts, "end": ts, "bytes": size, "solicited": requested}
        bursts.append(current)
        requested = False
    flow.bursts = bursts
    unsolicited = sum(not burst["solicited"] for burst in bursts)
    pushed = [burst["start"] for burst in bursts if not burst["solicited"]]
    gaps = [later - earlier for earlier, later in zip(pushed, pushed[1:])]
    flow.push_interval_ms = _ms(median(gaps)) if gaps else None
    if (unsolicited >= PUSH_MIN_UNSOLICITED and unsolicited * 4 >= len(bursts)
            and flow.last_ts - flow.first_ts >= PUSH_MIN_DURATION_S):
        flow.pattern = "push_like"
    elif len(bursts) > unsolicited:
        flow.pattern = "request_response"
    else:
        flow.pattern = "minimal"


def inspect_flows(flows: Sequence[Flow]) -> None:
    for flow in flows:
        (_inspect_tcp if flow.proto == "tcp" else _inspect_udp)(flow)
    names = {}
    for flow in flows:
        for query in flow.dns:
            for address in query.get("answers", []):
                names.setdefault(address, query["name"])
    for flow in flows:
        flow.name = (
            (flow.tls or {}).get("sni") or (flow.http or {}).get("host")
            or names.get(flow.server[0]) or flow.server[0]
        )
        if flow.kind == "dns":
            flow.name = f"DNS {flow.server[0]}"
        _classify(flow)


# --------------------------------------------------------------------------
# User actions


def find_actions(flows: Sequence[Flow], *, idle_gap: float = DEFAULT_IDLE_GAP_S,
                 max_window: float = DEFAULT_MAX_WINDOW_S) -> list[dict[str, Any]]:
    """Group client requests separated by quiet periods into actions."""
    points = sorted((ts, number) for number, flow in enumerate(flows) for ts in flow.triggers)
    groups: list[dict[str, Any]] = []
    for ts, number in points:
        if groups and ts - groups[-1]["last"] <= idle_gap:
            groups[-1]["last"] = ts
            groups[-1]["flows"].add(number)
            groups[-1]["requests"] += 1
        else:
            groups.append({"start": ts, "last": ts, "flows": {number}, "requests": 1})
    actions = []
    for position, group in enumerate(groups):
        start = group["start"]
        following = groups[position + 1]["start"] if position + 1 < len(groups) else math.inf
        end = min(following, start + max_window)
        new, reused, dns, servers = [], [], [], []
        first_response = span_end = None
        bytes_down = 0
        for number in sorted(group["flows"]):
            flow = flows[number]
            responses = [b for b in flow.bursts if b["solicited"] and start <= b["start"] < end]
            flow_bytes = sum(size for ts, up, size in flow.events if not up and start <= ts < end)
            bytes_down += flow_bytes
            if responses:
                last = max(b["end"] for b in responses)
                span_end = last if span_end is None else max(span_end, last)
            if flow.kind == "dns":
                dns.extend({"name": q["name"], "ms": q["ms"]} for q in flow.dns if start <= q["ts"] < end)
                continue
            ready = flow.app_ready_ts
            application = [b for b in responses if ready is None or b["start"] > ready]
            if application:
                first = application[0]["start"]
                first_response = first if first_response is None else min(first_response, first)
            opened = flow.opened_ts
            if opened is not None and start <= opened < end:
                new.append({
                    "name": flow.name, "kind": flow.kind, "handshake_observed": True,
                    "setup_ms": flow.setup.get("setup_ms"),
                    "resumed": (flow.tls or {}).get("resumed"),
                })
            elif flow.first_ts >= start:
                new.append({"name": flow.name, "kind": flow.kind, "handshake_observed": False,
                            "setup_ms": None, "resumed": None})
            else:
                reused.append(flow.name)
            servers.append({
                "name": flow.name, "kind": flow.kind, "bytes_down": flow_bytes,
                "span_ms": _ms(max(b["end"] for b in responses) - start) if responses else None,
            })
        servers.sort(key=lambda server: -server["bytes_down"])
        actions.append({
            "index": position + 1,
            "start_ts": start,
            "requests": group["requests"],
            "new_connections": new,
            "reused_connections": sorted(set(reused)),
            "dns_queries": dns,
            "first_response_ms": _ms(first_response - start) if first_response is not None else None,
            "network_span_ms": _ms(span_end - start) if span_end is not None else None,
            "bytes_down": bytes_down,
            "servers": servers,
            "mark": None,
            "background": False,
        })
    return actions


def apply_marks(actions: list[dict[str, Any]], marks: Sequence[float], *,
                before: float = MARK_BEFORE_S, after: float = MARK_AFTER_S) -> dict[str, int]:
    """Attach each marked tap to the first action starting near it."""
    used = set()
    matched = 0
    for number, mark in enumerate(sorted(marks), start=1):
        for action in actions:
            if action["index"] in used:
                continue
            if mark - before <= action["start_ts"] <= mark + after:
                action["mark"] = number
                used.add(action["index"])
                matched += 1
                break
    for action in actions:
        action["background"] = action["mark"] is None
    return {"given": len(marks), "matched": matched, "missed": len(marks) - matched}


# --------------------------------------------------------------------------
# Report


def _median(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(median(present), 3) if present else None


def _public_flow(flow: Flow) -> dict[str, Any]:
    def public(info: dict[str, Any] | None) -> dict[str, Any] | None:
        if info is None:
            return None
        return {key: value for key, value in info.items() if not key.startswith("_") and not key.endswith("_ts")}

    return {
        "name": flow.name,
        "server_port": flow.server[1],
        "transport": flow.proto.upper(),
        "kind": flow.kind,
        "first_ts": flow.first_ts,
        "duration_s": round(flow.last_ts - flow.first_ts, 3),
        "handshake_observed": flow.opened_ts is not None,
        "bytes_up": flow.bytes_up,
        "bytes_down": flow.bytes_down,
        "packets": flow.packets_up + flow.packets_down,
        "tls": public(flow.tls),
        "quic": public(flow.quic),
        "http": public(flow.http),
        "setup": dict(flow.setup),
        "pattern": flow.pattern,
        "push_interval_ms": flow.push_interval_ms,
        "server_bursts": {
            "solicited": sum(b["solicited"] for b in flow.bursts),
            "unsolicited": sum(not b["solicited"] for b in flow.bursts),
        },
        "apps": sorted(flow.apps),
    }


def analyze_packets(packets: Sequence[Packet], label: str, *, source: str | None = None,
                    marks: Sequence[float] | None = None, idle_gap: float = DEFAULT_IDLE_GAP_S,
                    max_window: float = DEFAULT_MAX_WINDOW_S) -> dict[str, Any]:
    flows = build_flows(packets)
    inspect_flows(flows)
    actions = find_actions(flows, idle_gap=idle_gap, max_window=max_window)
    mark_summary = apply_marks(actions, marks) if marks else None
    chosen = [a for a in actions if not a["background"]] if marks else actions

    tls_flows = [f for f in flows if f.kind == "tls"]
    quic_flows = [f for f in flows if f.kind == "quic"]
    dns_queries = [q for f in flows for q in f.dns]
    opened_tls = [f for f in tls_flows if f.syn_ts is not None]
    by_name: dict[str, dict[str, Any]] = {}
    for flow in flows:
        if flow.kind == "dns":
            continue
        entry = by_name.setdefault(flow.name, {
            "name": flow.name, "kinds": Counter(), "connections": 0, "bytes_down": 0,
            "push_like": 0, "tls_versions": Counter(), "alpn_offered": Counter(),
        })
        entry["kinds"][flow.kind] += 1
        entry["connections"] += 1
        entry["bytes_down"] += flow.bytes_down
        entry["push_like"] += flow.pattern == "push_like"
        if flow.tls:
            if flow.tls.get("version"):
                entry["tls_versions"][flow.tls["version"]] += 1
            if flow.tls.get("alpn_offered"):
                entry["alpn_offered"][",".join(flow.tls["alpn_offered"])] += 1
    servers = sorted(by_name.values(), key=lambda entry: -entry["bytes_down"])
    for entry in servers:
        for key in ("kinds", "tls_versions", "alpn_offered"):
            entry[key] = dict(entry[key].most_common())

    report: dict[str, Any] = {
        "label": label,
        "source": source,
        "packets": len(packets),
        "start_ts": packets[0].ts if packets else None,
        "end_ts": packets[-1].ts if packets else None,
        "duration_s": round(packets[-1].ts - packets[0].ts, 3) if packets else 0.0,
        "apps": sorted(set().union(*(flow.apps for flow in flows))) if flows else [],
        "protocol_mix": dict(Counter(flow.kind for flow in flows).most_common()),
        "tls": {
            "connections": len(tls_flows),
            "handshakes_observed": len(opened_tls),
            "versions": dict(Counter(f.tls.get("version") or "unknown" for f in tls_flows).most_common()),
            "alpn_offered": dict(Counter(",".join(f.tls.get("alpn_offered") or []) or "(none)" for f in tls_flows).most_common()),
            "alpn_selected": dict(Counter(f.tls["alpn_selected"] for f in tls_flows if f.tls.get("alpn_selected")).most_common()),
            "resumed": sum(bool(f.tls.get("resumed")) for f in tls_flows),
            "hello_retry": sum(bool(f.tls.get("hello_retry")) for f in tls_flows),
            "early_data_offered": sum(bool(f.tls.get("early_data_offered")) for f in tls_flows),
            "ech": sum(bool(f.tls.get("ech")) for f in tls_flows),
            "median_tcp_connect_ms": _median(f.setup.get("tcp_connect_ms") for f in opened_tls),
            "median_setup_ms": _median(f.setup.get("setup_ms") for f in opened_tls),
        },
        "quic": {
            "connections": len(quic_flows),
            "versions": sorted({v for f in quic_flows for v in f.quic["versions"]}),
            "zero_rtt": sum(f.quic["zero_rtt"] for f in quic_flows),
            "median_first_response_ms": _median(f.setup.get("first_response_ms") for f in quic_flows),
        },
        "dns": {
            "queries": len(dns_queries),
            "median_ms": _median(q["ms"] for q in dns_queries),
            "dot_connections": sum(f.kind == "dot" for f in flows),
        },
        "http_plain": sorted({f.http["host"] or "(no host)" for f in flows if f.http}),
        "websocket_upgrade_plain": sorted({f.http["host"] or "(no host)" for f in flows
                                           if f.http and (f.http.get("upgrade") or "").lower() == "websocket"}),
        "push_like": sorted({f.name for f in flows if f.pattern == "push_like"}),
        "push_like_details": _push_details(flows),
        "servers": servers,
        "connections": [_public_flow(f) for f in sorted(flows, key=lambda f: f.first_ts)],
        "actions": actions,
        "marks": mark_summary,
        "action_summary": {
            "count": len(chosen),
            "with_new_connection": sum(bool(a["new_connections"]) for a in chosen),
            "median_new_connections": _median(len(a["new_connections"]) for a in chosen),
            "median_reused_connections": _median(len(a["reused_connections"]) for a in chosen),
            "median_dns_queries": _median(len(a["dns_queries"]) for a in chosen),
            "median_first_response_ms": _median(a["first_response_ms"] for a in chosen),
            "median_network_span_ms": _median(a["network_span_ms"] for a in chosen),
            "median_bytes_down": _median(a["bytes_down"] for a in chosen),
        },
        "settings": {
            "idle_gap_s": idle_gap, "max_window_s": max_window, "burst_gap_s": BURST_GAP_S,
            "reaction_slack_s": REACTION_SLACK_S, "small_control_bytes": SMALL_CONTROL_BYTES,
        },
    }
    report["observation"] = observation_text(report)
    return report


def _push_details(flows: Sequence[Flow]) -> list[dict[str, Any]]:
    """Per server name: server-initiated bursts and their median spacing."""
    grouped: dict[str, list[Flow]] = {}
    for flow in flows:
        if flow.pattern == "push_like":
            grouped.setdefault(flow.name, []).append(flow)
    details = []
    for name, members in sorted(grouped.items()):
        details.append({
            "name": name,
            "connections": len(members),
            "unsolicited_bursts": sum(not b["solicited"] for f in members for b in f.bursts),
            "median_interval_ms": _median(f.push_interval_ms for f in members),
        })
    return details


def observation_text(report: dict[str, Any]) -> str:
    """One factual sentence suitable for the dashboard's protocol_observation."""
    parts = [f"{report['label']}: 비복호화 패킷 메타데이터"]
    tls = report["tls"]
    if tls["connections"]:
        versions = "/".join(report["tls"]["versions"])
        alpn = next(iter(tls["alpn_offered"]), "(none)")
        parts.append(f"TLS/TCP 연결 {tls['connections']}개({versions}, ALPN 제안 {alpn})")
    quic = report["quic"]
    parts.append(
        f"QUIC 연결 {quic['connections']}개({', '.join(quic['versions'])}, ALPN 미확인)"
        if quic["connections"] else "QUIC 미관찰"
    )
    if report["push_like_details"]:
        intervals = ", ".join(_fmt(d["median_interval_ms"]) for d in report["push_like_details"])
        parts.append(f"서버 주도 반복 전송 추정 {len(report['push_like_details'])}곳(간격 중앙값 {intervals}ms)")
    summary = report["action_summary"]
    if summary["count"]:
        parts.append(
            f"동작 {summary['count']}회 중 신규 연결 동반 {summary['with_new_connection']}회, "
            f"첫 응답 중앙값 {_fmt(summary['median_first_response_ms'])}ms"
        )
    return "; ".join(parts)


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def resolve_timezone(name: str) -> tzinfo:
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", name)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as exc:  # ZoneInfoNotFoundError or missing tzdata
        if name == "Asia/Seoul":
            return timezone(timedelta(hours=9))
        raise ValueError(f"unknown time zone: {name}") from exc


def parse_marks(text: str, *, reference_ts: float | None, tz: tzinfo) -> list[float]:
    """Parse ISO datetimes or HH:MM:SS(.fff) times, one per line, '#' comments."""
    marks = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            if len(line) > 10 and line[4] == "-":
                moment = datetime.fromisoformat(line)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=tz)
            else:
                if reference_ts is None:
                    raise ValueError("time-only marks need a capture date")
                day = datetime.fromtimestamp(reference_ts, tz).date()
                moment = datetime.combine(day, dtime.fromisoformat(line), tzinfo=tz)
        except ValueError as exc:
            raise ValueError(f"marks line {number} is not a time: {raw!r}") from exc
        marks.append(moment.timestamp())
    return sorted(marks)


def _local(ts: float | None, tz: tzinfo) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


_KIND_LABELS = {
    "tls": "TLS over TCP", "quic": "QUIC (UDP)", "http": "평문 HTTP", "dns": "평문 DNS",
    "dot": "DNS over TLS", "tcp": "기타 TCP", "udp": "기타 UDP", "other": "기타",
}
_PATTERN_LABELS = {
    "push_like": "서버 주도 반복 전송", "request_response": "요청-응답", "minimal": "데이터 적음",
}


def render_markdown(reports: Sequence[dict[str, Any]], tz: tzinfo) -> str:
    lines = [
        "# 네트워크 메타데이터 비교",
        "",
        "> 해석 범위: 복호화하지 않은 패킷 헤더와 TLS·QUIC·DNS의 평문 필드만 사용했다. "
        "TLS 1.3과 QUIC 내부의 HTTP 버전, WebSocket/SSE/gRPC 여부, 서버 처리시간, 캐시 적중, "
        "속도 차이의 원인은 이 표로 확정할 수 없다. '서버 주도 반복 전송'은 타이밍 기반 추정이다. "
        "VPN 방식 캡처는 앱과 가상 인터페이스 사이의 시각이므로 절대값보다 같은 방식끼리의 상대 비교로만 읽는다.",
        "",
        "## 캡처 개요",
        "",
        "| 라벨 | 파일 | 시작(현지) | 길이(s) | 패킷 | 트레일러 앱 이름 |",
        "|---|---|---|---:|---:|---|",
    ]
    for report in reports:
        lines.append(
            f"| {report['label']} | {report['source'] or '-'} | {_local(report['start_ts'], tz)} | "
            f"{_fmt(report['duration_s'])} | {report['packets']} | {', '.join(report['apps']) or '-'} |"
        )
    lines += [
        "",
        "## 연결 방식",
        "",
        "| 항목 | " + " | ".join(r["label"] for r in reports) + " |",
        "|---|" + "---:|" * len(reports),
    ]
    rows = [
        ("연결 종류", lambda r: ", ".join(f"{_KIND_LABELS.get(k, k)} {v}" for k, v in r["protocol_mix"].items()) or "-"),
        ("TLS 버전", lambda r: ", ".join(f"{k} {v}" for k, v in r["tls"]["versions"].items()) or "-"),
        ("ALPN 제안(ClientHello)", lambda r: ", ".join(f"{k} ×{v}" for k, v in r["tls"]["alpn_offered"].items()) or "-"),
        ("ALPN 선택(TLS 1.2만 평문)", lambda r: ", ".join(f"{k} ×{v}" for k, v in r["tls"]["alpn_selected"].items()) or "확인 불가"),
        ("관찰된 TLS 핸드셰이크", lambda r: str(r["tls"]["handshakes_observed"])),
        ("세션 재개(PSK/세션 ID)", lambda r: str(r["tls"]["resumed"])),
        ("HelloRetryRequest", lambda r: str(r["tls"]["hello_retry"])),
        ("0-RTT 제안(TLS)", lambda r: str(r["tls"]["early_data_offered"])),
        ("ECH 사용(SNI 가림 가능)", lambda r: str(r["tls"]["ech"])),
        ("TCP 연결 중앙값(ms)", lambda r: _fmt(r["tls"]["median_tcp_connect_ms"])),
        ("TLS 연결 준비 중앙값(ms)", lambda r: _fmt(r["tls"]["median_setup_ms"])),
        ("QUIC 버전", lambda r: ", ".join(r["quic"]["versions"]) or "미관찰"),
        ("QUIC 첫 응답 중앙값(ms)", lambda r: _fmt(r["quic"]["median_first_response_ms"])),
        ("평문 DNS 질의 / 중앙값(ms)", lambda r: f"{r['dns']['queries']} / {_fmt(r['dns']['median_ms'])}"),
        ("서버 주도 반복 전송 추정", lambda r: ", ".join(
            f"{d['name']}({d['unsolicited_bursts']}회, 간격 중앙값 {_fmt(d['median_interval_ms'])}ms)"
            for d in r["push_like_details"]) or "없음"),
        ("평문 HTTP 호스트", lambda r: ", ".join(r["http_plain"]) or "없음"),
    ]
    for title, getter in rows:
        lines.append(f"| {title} | " + " | ".join(getter(r) for r in reports) + " |")

    lines += [
        "",
        "## 동작(탭) 단위 요약",
        "",
        "`첫 응답`은 동작의 첫 요청 패킷부터 연결 준비가 끝난 뒤 처음 도착한 응답 데이터까지, "
        "`네트워크 구간`은 그 동작에서 요청이 선행된 마지막 응답 데이터까지다. 둘 다 화면 표시 시각이 아니다.",
        "",
        "| 항목 | " + " | ".join(r["label"] for r in reports) + " |",
        "|---|" + "---:|" * len(reports),
    ]
    action_rows = [
        ("분석한 동작 수", lambda r: str(r["action_summary"]["count"])),
        ("표시 시각 매칭(일치/전체)", lambda r: f"{r['marks']['matched']}/{r['marks']['given']}" if r["marks"] else "표시 없음"),
        ("신규 연결 동반 동작", lambda r: str(r["action_summary"]["with_new_connection"])),
        ("동작당 신규 연결 중앙값", lambda r: _fmt(r["action_summary"]["median_new_connections"])),
        ("동작당 재사용 연결 중앙값", lambda r: _fmt(r["action_summary"]["median_reused_connections"])),
        ("동작당 DNS 질의 중앙값", lambda r: _fmt(r["action_summary"]["median_dns_queries"])),
        ("첫 응답 중앙값(ms)", lambda r: _fmt(r["action_summary"]["median_first_response_ms"])),
        ("네트워크 구간 중앙값(ms)", lambda r: _fmt(r["action_summary"]["median_network_span_ms"])),
        ("동작당 수신 바이트 중앙값", lambda r: _fmt(r["action_summary"]["median_bytes_down"], 0)),
    ]
    for title, getter in action_rows:
        lines.append(f"| {title} | " + " | ".join(getter(r) for r in reports) + " |")

    for report in reports:
        lines += [
            "",
            f"## {report['label']} 상세",
            "",
            "### 주요 서버 (수신 바이트 순)",
            "",
            "| 서버 이름 | 연결 종류 | 연결 수 | 수신 바이트 | TLS 버전 | ALPN 제안 | 서버 주도 전송 |",
            "|---|---|---:|---:|---|---|---:|",
        ]
        for entry in report["servers"][:10]:
            lines.append(
                f"| {entry['name']} | {', '.join(_KIND_LABELS.get(k, k) for k in entry['kinds'])} | "
                f"{entry['connections']} | {entry['bytes_down']} | {', '.join(entry['tls_versions']) or '-'} | "
                f"{'; '.join(entry['alpn_offered']) or '-'} | {entry['push_like']} |"
            )
        lines += [
            "",
            "### 동작별 기록",
            "",
            "| # | 시작(현지) | 탭 표시 | 요청 | 신규 연결 | 재사용 | DNS | 첫 응답(ms) | 네트워크 구간(ms) | 수신 바이트 | 주요 서버 |",
            "|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|",
        ]
        for action in report["actions"]:
            new = ", ".join(
                f"{c['name']}({_KIND_LABELS.get(c['kind'], c['kind'])}, {_fmt(c['setup_ms'])}ms"
                f"{', 재개' if c['resumed'] else ''})" for c in action["new_connections"]
            ) or "-"
            mark = action["mark"] if action["mark"] is not None else ("배경" if report["marks"] else "-")
            top = ", ".join(s["name"] for s in action["servers"][:3]) or "-"
            lines.append(
                f"| {action['index']} | {_local(action['start_ts'], tz)} | {mark} | {action['requests']} | {new} | "
                f"{len(action['reused_connections'])} | {len(action['dns_queries'])} | "
                f"{_fmt(action['first_response_ms'])} | {_fmt(action['network_span_ms'])} | "
                f"{action['bytes_down']} | {top} |"
            )
        lines += ["", f"`protocol_observation` 후보: {report['observation']}"]
    lines += [
        "",
        "## 설정",
        "",
        "| 항목 | 값 |",
        "|---|---|",
    ]
    settings = reports[0]["settings"] if reports else {}
    for key, value in settings.items():
        lines.append(f"| `{key}` | {value} |")
    return "\n".join(lines) + "\n"
