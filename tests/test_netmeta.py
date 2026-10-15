"""Checks for the unencrypted packet-metadata analyzer, using synthetic captures."""

import ipaddress
import json
from pathlib import Path
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
import io

from stocklab import netmeta
from tools import analyze_pcap

T0 = 1_792_000_000.0
DEVICE = "10.215.173.1"
RESOLVER = "10.215.173.2"
FIN, SYN, RST, PSH, ACK = 0x01, 0x02, 0x04, 0x08, 0x10


# ---------------------------------------------------------------- builders

def ipv4(src, dst, proto, l4):
    header = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(l4), 0, 0, 64, proto, 0,
        ipaddress.IPv4Address(src).packed, ipaddress.IPv4Address(dst).packed,
    )
    return header + l4


def ipv6(src, dst, proto, l4):
    return struct.pack(
        "!IHBB16s16s", 6 << 28, len(l4), proto, 64,
        ipaddress.IPv6Address(src).packed, ipaddress.IPv6Address(dst).packed,
    ) + l4


def ip(src, dst, proto, l4):
    return (ipv6 if ":" in src else ipv4)(src, dst, proto, l4)


def tcp(sport, dport, seq, flags, payload=b""):
    return struct.pack("!HHIIBBHHH", sport, dport, seq, 0, 5 << 4, flags, 65535, 0, 0) + payload


def udp(sport, dport, payload):
    return struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload


def pcap(frames, linktype=101):
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, linktype)
    for ts, frame in frames:
        seconds = int(ts)
        micros = round((ts - seconds) * 1e6)
        out += struct.pack("<IIII", seconds, micros, len(frame), len(frame)) + frame
    return out


def pcapng(frames, linktype=101):
    def block(kind, body):
        body += b"\0" * (-len(body) % 4)
        return struct.pack("<II", kind, len(body) + 12) + body + struct.pack("<I", len(body) + 12)

    out = block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    tsresol = struct.pack("<HHB", 9, 1, 9) + b"\0\0\0" + struct.pack("<HH", 0, 0)  # nanoseconds
    out += block(1, struct.pack("<HHI", linktype, 0, 262144) + tsresol)
    for ts, frame in frames:
        ticks = round(ts * 1e9)
        out += block(6, struct.pack("<IIIII", 0, ticks >> 32, ticks & 0xFFFFFFFF, len(frame), len(frame)) + frame)
    return out


def extension(kind, data):
    return struct.pack("!HH", kind, len(data)) + data


def client_hello(sni=None, alpn=(), versions=(0x0A0A, 0x0304, 0x0303), psk=False, early=False,
                 session_id=b"\x22" * 32):
    extensions = b""
    if sni:
        name = sni.encode()
        entry = b"\x00" + struct.pack("!H", len(name)) + name
        extensions += extension(0, struct.pack("!H", len(entry)) + entry)
    if alpn:
        names = b"".join(bytes([len(p)]) + p.encode() for p in alpn)
        extensions += extension(16, struct.pack("!H", len(names)) + names)
    if versions:
        raw = b"".join(struct.pack("!H", v) for v in versions)
        extensions += extension(43, bytes([len(raw)]) + raw)
    if early:
        extensions += extension(42, b"")
    if psk:
        extensions += extension(41, b"\x00\x00\x00\x00")
    body = (b"\x03\x03" + b"\x11" * 32 + bytes([len(session_id)]) + session_id
            + b"\x00\x02\x13\x01" + b"\x01\x00" + struct.pack("!H", len(extensions)) + extensions)
    message = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack("!H", len(message)) + message


def server_hello(version=0x0304, alpn=None, psk=False, session_id=b"\x22" * 32, retry=False):
    extensions = b""
    if version == 0x0304:
        extensions += extension(43, struct.pack("!H", 0x0304))
    if alpn:
        name = bytes([len(alpn)]) + alpn.encode()
        extensions += extension(16, struct.pack("!H", len(name)) + name)
    if psk:
        extensions += extension(41, b"\x00\x00")
    random = netmeta._HRR_RANDOM if retry else b"\x33" * 32
    body = (b"\x03\x03" + random + bytes([len(session_id)]) + session_id + b"\x13\x01\x00"
            + struct.pack("!H", len(extensions)) + extensions)
    message = b"\x02" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x03" + struct.pack("!H", len(message)) + message


def record(kind, size):
    return bytes([kind]) + b"\x03\x03" + struct.pack("!H", size) + b"\x00" * size


def app_data(size):
    return record(23, size)


def dns_question(name):
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack("!HH", 1, 1)


def dns_query(ident, name):
    return struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0) + dns_question(name)


def dns_response(ident, name, address):
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + ipaddress.IPv4Address(address).packed
    return struct.pack("!HHHHHH", ident, 0x8180, 1, 1, 0, 0) + dns_question(name) + answer


def quic_long(kind_bits, size, version=1):
    first = 0xC0 | (kind_bits << 4)
    header = bytes([first]) + version.to_bytes(4, "big") + b"\x08" + b"\x01" * 8 + b"\x00"
    return header + b"\x00" * (size - len(header))


def quic_short(size):
    return b"\x40" + b"\x02" * (size - 1)


class Scenario:
    def __init__(self):
        self.frames = []

    def add(self, t, frame):
        self.frames.append((T0 + t, frame))

    def dns(self, t, ident, name, address, delay=0.020, port=40000):
        self.add(t, ip(DEVICE, RESOLVER, 17, udp(port, 53, dns_query(ident, name))))
        self.add(t + delay, ip(RESOLVER, DEVICE, 17, udp(53, port, dns_response(ident, name, address))))

    def udp_up(self, t, server, cport, payload, sport=443, client=DEVICE):
        self.add(t, ip(client, server, 17, udp(cport, sport, payload)))

    def udp_down(self, t, server, cport, payload, sport=443, client=DEVICE):
        self.add(t, ip(server, client, 17, udp(sport, cport, payload)))


class Conn:
    def __init__(self, scenario, server, cport, sport=443, client=DEVICE, cseq=1000, sseq=900000):
        self.s, self.server, self.cport, self.sport, self.client = scenario, server, cport, sport, client
        self.cseq, self.sseq = cseq, sseq

    def open(self, t, rtt=0.030):
        self.s.add(t, ip(self.client, self.server, 6, tcp(self.cport, self.sport, self.cseq, SYN)))
        self.cseq += 1
        self.s.add(t + rtt, ip(self.server, self.client, 6, tcp(self.sport, self.cport, self.sseq, SYN | ACK)))
        self.sseq += 1
        self.s.add(t + rtt + 0.001, ip(self.client, self.server, 6, tcp(self.cport, self.sport, self.cseq, ACK)))

    def up(self, t, payload, seq=None):
        seq = self.cseq if seq is None else seq
        self.s.add(t, ip(self.client, self.server, 6, tcp(self.cport, self.sport, seq, ACK | PSH, payload)))
        self.cseq = max(self.cseq, seq + len(payload))

    def down(self, t, payload):
        self.s.add(t, ip(self.server, self.client, 6, tcp(self.sport, self.cport, self.sseq, ACK | PSH, payload)))
        self.sseq += len(payload)


def toss_like():
    """Two TLS 1.3 connections (one push-like), a reused-connection action, background noise."""
    s = Scenario()
    api = Conn(s, "203.0.113.10", 50001)
    stream = Conn(s, "203.0.113.20", 50002)
    s.dns(1.000, 7, "api.toss.test", "203.0.113.10")
    api.open(1.030)
    api.up(1.062, client_hello("api.toss.test", ("h2", "http/1.1")))
    api.down(1.095, server_hello() + app_data(900))
    api.up(1.100, app_data(53) + app_data(200))
    api.down(1.150, app_data(1200))
    api.down(1.170, app_data(600))
    stream.open(1.040)
    stream.up(1.072, client_hello("stream.toss.test", ("http/1.1",)))
    stream.down(1.105, server_hello() + app_data(700))
    stream.up(1.110, app_data(53) + app_data(150))
    stream.down(1.200, app_data(300))
    for t in (3.0, 4.0, 5.0, 6.0):
        stream.down(t, app_data(120))
        stream.up(t + 0.005, app_data(20))
    api.up(12.000, app_data(300))  # background request without a tap
    api.down(12.030, app_data(100))
    api.up(20.000, app_data(180))
    stream.up(20.010, app_data(150))
    api.down(20.040, app_data(800))
    stream.down(20.060, app_data(300))
    return s


def kis_like():
    """QUIC with DNS naming, plus a resumed TLS 1.2 connection over IPv6."""
    s = Scenario()
    quote = "198.51.100.7"
    s.dns(1.000, 9, "quote.kis.test", quote)
    s.udp_up(1.020, quote, 51000, quic_long(0, 1200))
    s.udp_down(1.060, quote, 51000, quic_long(0, 1200))
    s.udp_up(1.062, quote, 51000, quic_long(2, 100))
    s.udp_up(1.063, quote, 51000, quic_short(300))
    s.udp_down(1.110, quote, 51000, quic_short(900))
    legacy = Conn(s, "2001:db8::5", 52000, client="fd00::1")
    legacy.open(30.000, rtt=0.050)
    legacy.up(30.051, client_hello("legacy.kis.test", ("h2",), versions=()))
    legacy.down(30.100, server_hello(version=0x0303, alpn="h2") + record(20, 1) + record(22, 40))
    legacy.up(30.102, record(20, 1) + record(22, 40))
    legacy.up(30.103, app_data(220))
    legacy.down(30.160, app_data(2000))
    return s


def analyze(scenario, label="test", **options):
    packets = netmeta.read_capture(pcap(scenario.frames))
    return netmeta.analyze_packets(packets, label, **options)


def flow_named(report, name):
    return next(c for c in report["connections"] if c["name"] == name)


# ---------------------------------------------------------------- tests

class CaptureFormatTests(unittest.TestCase):
    def test_pcap_and_nanosecond_pcapng_decode_the_same_packets(self):
        frames = toss_like().frames
        classic = netmeta.read_capture(pcap(frames))
        modern = netmeta.read_capture(pcapng(frames))
        self.assertEqual(len(classic), len(frames))
        self.assertEqual(len(classic), len(modern))
        for left, right in zip(classic, modern):
            self.assertAlmostEqual(left.ts, right.ts, places=5)
            self.assertEqual((left.src, left.dport, left.payload), (right.src, right.dport, right.payload))

    def test_pcapdroid_ethernet_trailer_names_the_app_and_is_not_payload(self):
        packet = ipv4(DEVICE, "203.0.113.10", 6, tcp(50001, 443, 1, ACK | PSH, b"hello"))
        trailer = b"\x00\x00" + b"\x01\x07\x20\x21" + struct.pack("!i", 10123) + b"Toss".ljust(20, b"\0") + b"\0" * 4
        frame = b"\0" * 12 + b"\x08\x00" + packet + trailer
        [decoded] = netmeta.read_capture(pcap([(T0, frame)], linktype=1))
        self.assertEqual(decoded.app, "Toss")
        self.assertEqual(decoded.payload, b"hello")

    def test_ethernet_header_omitted_by_pcapdroid_still_decodes_raw_ip(self):
        packet = ipv4(DEVICE, "203.0.113.10", 17, udp(5000, 443, b"x" * 10))
        [decoded] = netmeta.read_capture(pcap([(T0, packet)], linktype=1))
        self.assertEqual((decoded.proto, decoded.dport, len(decoded.payload)), ("udp", 443, 10))

    def test_rejects_non_capture_and_stops_at_truncated_record(self):
        with self.assertRaisesRegex(ValueError, "not a PCAP"):
            netmeta.read_capture(b"definitely not a capture")
        frames = [(T0, ipv4(DEVICE, RESOLVER, 17, udp(1, 53, b"abc"))),
                  (T0 + 1, ipv4(DEVICE, RESOLVER, 17, udp(1, 53, b"def")))]
        data = pcap(frames)
        self.assertEqual(len(netmeta.read_capture(data[:-5])), 1)


class ConnectionMetadataTests(unittest.TestCase):
    def setUp(self):
        self.toss = analyze(toss_like(), "toss")
        self.kis = analyze(kis_like(), "kis")

    def test_tls13_fields_and_setup_timing(self):
        api = flow_named(self.toss, "api.toss.test")
        self.assertEqual(api["kind"], "tls")
        self.assertEqual(api["tls"]["sni"], "api.toss.test")
        self.assertEqual(api["tls"]["alpn_offered"], ["h2", "http/1.1"])
        self.assertEqual(api["tls"]["versions_offered"], ["TLS 1.3", "TLS 1.2"])  # GREASE removed
        self.assertEqual(api["tls"]["version"], "TLS 1.3")
        self.assertIsNone(api["tls"]["alpn_selected"])  # encrypted in TLS 1.3
        self.assertFalse(api["tls"]["resumed"])
        self.assertAlmostEqual(api["setup"]["tcp_connect_ms"], 30.0, places=2)
        self.assertAlmostEqual(api["setup"]["tls_hello_ms"], 33.0, places=2)
        self.assertAlmostEqual(api["setup"]["setup_ms"], 70.0, places=2)
        self.assertEqual(self.toss["tls"]["versions"], {"TLS 1.3": 2})

    def test_tls12_alpn_selection_and_session_resumption_are_plaintext(self):
        legacy = flow_named(self.kis, "legacy.kis.test")
        self.assertEqual(legacy["tls"]["version"], "TLS 1.2")
        self.assertEqual(legacy["tls"]["alpn_selected"], "h2")
        self.assertTrue(legacy["tls"]["resumed"])
        self.assertAlmostEqual(legacy["setup"]["setup_ms"], 103.0, places=2)

    def test_tls13_psk_and_hello_retry_flags(self):
        s = Scenario()
        resumed = Conn(s, "203.0.113.30", 50010)
        resumed.open(1.0)
        resumed.up(1.032, client_hello("r.test", ("h2",), psk=True, early=True))
        resumed.down(1.060, server_hello(psk=True))
        retried = Conn(s, "203.0.113.31", 50011)
        retried.open(2.0)
        retried.up(2.032, client_hello("hrr.test", ("h2",)))
        retried.down(2.060, server_hello(retry=True))
        report = analyze(s)
        self.assertTrue(flow_named(report, "r.test")["tls"]["resumed"])
        self.assertTrue(flow_named(report, "r.test")["tls"]["early_data_offered"])
        self.assertTrue(flow_named(report, "hrr.test")["tls"]["hello_retry"])
        self.assertIsNone(flow_named(report, "hrr.test")["tls"]["resumed"])
        self.assertEqual(report["tls"]["hello_retry"], 1)

    def test_client_hello_split_across_segments_with_retransmission(self):
        s = Scenario()
        conn = Conn(s, "203.0.113.40", 50020)
        conn.open(1.0)
        hello = client_hello("split.test", ("h2", "http/1.1"))
        first_seq = conn.cseq
        conn.up(1.040, hello[:60])
        conn.up(1.041, hello[60:])
        conn.up(1.240, hello[:60], seq=first_seq)  # retransmission arrives later
        conn.down(1.300, server_hello())
        flow = flow_named(analyze(s), "split.test")
        self.assertEqual(flow["tls"]["alpn_offered"], ["h2", "http/1.1"])
        self.assertEqual(flow["tls"]["version"], "TLS 1.3")

    def test_quic_is_detected_named_by_dns_and_not_called_http3(self):
        quic = flow_named(self.kis, "quote.kis.test")
        self.assertEqual(quic["kind"], "quic")
        self.assertEqual(quic["quic"]["versions"], ["QUIC v1"])
        self.assertTrue(quic["quic"]["handshake_observed"])
        self.assertAlmostEqual(quic["setup"]["first_response_ms"], 40.0, places=2)
        self.assertEqual(self.kis["dns"]["queries"], 1)
        self.assertAlmostEqual(self.kis["dns"]["median_ms"], 20.0, places=2)
        self.assertIn("ALPN 미확인", self.kis["observation"])
        self.assertNotIn("HTTP/3", json.dumps(self.kis, ensure_ascii=False))

    def test_quic_zero_rtt_and_v2_long_headers(self):
        s = Scenario()
        s.udp_up(1.0, "198.51.100.8", 51100, quic_long(0, 1200))
        s.udp_up(1.001, "198.51.100.8", 51100, quic_long(1, 300))  # v1 0-RTT
        s.udp_down(1.040, "198.51.100.8", 51100, quic_long(0, 1200))
        s.udp_up(2.0, "198.51.100.9", 51101, quic_long(1, 1200, version=0x6B3343CF))  # v2 Initial
        s.udp_down(2.030, "198.51.100.9", 51101, quic_long(1, 1200, version=0x6B3343CF))
        report = analyze(s)
        zero = flow_named(report, "198.51.100.8")
        self.assertTrue(zero["quic"]["zero_rtt"])
        self.assertEqual(zero["setup"]["setup_ms"], 0.0)
        self.assertEqual(flow_named(report, "198.51.100.9")["quic"]["versions"], ["QUIC v2"])
        self.assertTrue(flow_named(report, "198.51.100.9")["quic"]["handshake_observed"])

    def test_push_like_versus_request_response(self):
        self.assertEqual(flow_named(self.toss, "stream.toss.test")["pattern"], "push_like")
        self.assertEqual(flow_named(self.toss, "api.toss.test")["pattern"], "request_response")
        self.assertEqual(self.toss["push_like"], ["stream.toss.test"])
        [detail] = self.toss["push_like_details"]
        self.assertEqual(detail["unsolicited_bursts"], 4)
        self.assertAlmostEqual(detail["median_interval_ms"], 1000.0, places=2)
        self.assertIn("간격 중앙값 1000.0ms", self.toss["observation"])

    def test_plain_http_reports_host_but_never_path(self):
        s = Scenario()
        conn = Conn(s, "192.0.2.50", 50030, sport=80)
        conn.open(1.0)
        conn.up(1.031, b"GET /secret?token=abc HTTP/1.1\r\nHost: plain.test:80\r\nUpgrade: websocket\r\n\r\n")
        conn.down(1.060, b"HTTP/1.1 101 Switching Protocols\r\n\r\n")
        report = analyze(s)
        self.assertEqual(report["http_plain"], ["plain.test"])
        self.assertEqual(report["websocket_upgrade_plain"], ["plain.test"])
        text = json.dumps(report) + netmeta.render_markdown([report], netmeta.resolve_timezone("Asia/Seoul"))
        self.assertNotIn("secret", text)
        self.assertNotIn("token", text)

    def test_report_has_no_device_address(self):
        text = json.dumps([self.toss, self.kis])
        self.assertNotIn(DEVICE, text)
        self.assertNotIn("fd00::1", text)


class ActionTests(unittest.TestCase):
    def test_actions_separate_new_and_reused_connections(self):
        report = analyze(toss_like())
        first, background, second = report["actions"]
        self.assertEqual(sorted(c["name"] for c in first["new_connections"]),
                         ["api.toss.test", "stream.toss.test"])
        self.assertEqual(len(first["dns_queries"]), 1)
        self.assertAlmostEqual(first["first_response_ms"], 150.0, places=2)
        self.assertAlmostEqual(first["network_span_ms"], 200.0, places=2)
        self.assertEqual(second["new_connections"], [])
        self.assertEqual(second["reused_connections"], ["api.toss.test", "stream.toss.test"])
        self.assertAlmostEqual(second["first_response_ms"], 40.0, places=2)
        self.assertAlmostEqual(second["network_span_ms"], 60.0, places=2)
        self.assertEqual(background["requests"], 1)
        self.assertEqual(report["action_summary"]["count"], 3)

    def test_marks_keep_tapped_actions_and_flag_background(self):
        marks = [T0 + 0.6, T0 + 19.5, T0 + 40.0]
        report = analyze(toss_like(), marks=marks)
        self.assertEqual([a["mark"] for a in report["actions"]], [1, None, 2])
        self.assertEqual([a["background"] for a in report["actions"]], [False, True, False])
        self.assertEqual(report["marks"], {"given": 3, "matched": 2, "missed": 1})
        summary = report["action_summary"]
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["with_new_connection"], 1)
        self.assertAlmostEqual(summary["median_first_response_ms"], 95.0, places=2)

    def test_quic_action_first_response_waits_for_handshake(self):
        report = analyze(kis_like())
        first = report["actions"][0]
        self.assertEqual([c["kind"] for c in first["new_connections"]], ["quic"])
        self.assertAlmostEqual(first["first_response_ms"], 110.0, places=2)

    def test_parse_marks_accepts_iso_and_time_only(self):
        tz = netmeta.resolve_timezone("Asia/Seoul")
        text = "# cues\n2026-10-16T10:00:01.500+09:00\n10:00:03\n\n"
        reference = netmeta.datetime(2026, 10, 16, 9, 59, tzinfo=tz).timestamp()
        marks = netmeta.parse_marks(text, reference_ts=reference, tz=tz)
        self.assertEqual(marks[1] - marks[0], 1.5)
        with self.assertRaisesRegex(ValueError, "line 1"):
            netmeta.parse_marks("soon", reference_ts=reference, tz=tz)


class CommandLineTests(unittest.TestCase):
    def test_compare_two_captures_writes_markdown_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "toss.pcap").write_bytes(pcap(toss_like().frames))
            (folder / "kis.pcapng").write_bytes(pcapng(kis_like().frames))
            out, data = folder / "report.md", folder / "report.json"
            with redirect_stdout(io.StringIO()):
                code = analyze_pcap.main([
                    f"toss={folder / 'toss.pcap'}", f"kis={folder / 'kis.pcapng'}",
                    "--out", str(out), "--json", str(data),
                ])
            self.assertEqual(code, 0)
            markdown = out.read_text(encoding="utf-8")
            self.assertIn("| 항목 | toss | kis |", markdown)
            self.assertIn("해석 범위", markdown)
            self.assertEqual([r["label"] for r in json.loads(data.read_text(encoding="utf-8"))], ["toss", "kis"])
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
                analyze_pcap.main([f"toss={folder / 'toss.pcap'}", "--out", str(out)])


if __name__ == "__main__":
    unittest.main()
