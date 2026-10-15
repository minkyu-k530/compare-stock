"""Run the local Stock Speed Lab API and dashboard without dependencies."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import closing, contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db
from .analysis import analyze


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DEFAULT_DB = ROOT / "data" / "stocklab.sqlite3"


def demo_rows() -> list[dict]:
    """Clearly synthetic examples to exercise the dashboard, never measurements."""
    profiles = (
        ("stock_search", 520, 600, "검색 결과에서 동일 종목이 표시됨"),
        ("quote_detail", 680, 650, "종목명과 가격이 모두 표시됨"),
        ("chart_open", 870, 930, "차트의 첫 데이터가 표시됨"),
    )
    result = []
    for scenario, toss_base, kis_base, criterion in profiles:
        for index in range(1, 9):
            wobble = ((index * 17) % 53) - 26
            for app, base in (("toss", toss_base), ("kis", kis_base)):
                elapsed = base + wobble + (index % 3) * (5 if app == "toss" else -7)
                result.append(db.normalize({
                    "app": app, "scenario": scenario, "run_id": f"sample-{index:02d}",
                    "capture_method": "screen_recording", "capture_fps": 60,
                    "start_state": "warm", "market_phase": "open",
                    "completion_criterion": criterion, "status": "completed",
                    "run_order": (1 if app == "toss" else 2) if index % 2 else (4 if app == "toss" else 3),
                    "elapsed_ms": elapsed, "first_response_ms": round(elapsed * 0.24),
                    "content_ms": round(elapsed * 0.69),
                    "frame_jank_pct": round(2 + (index % 4) * 0.7, 2),
                    "network_rtt_ms": 22 + index % 4,
                    "protocol_observation": "", "network_type": "sample-wifi",
                    "device": "sample-device", "notes": "SYNTHETIC DEMO DATA",
                }, source="demo"))
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "StockSpeedLab/0.1"

    @contextmanager
    def database(self):
        with closing(db.connect(self.server.db_path)) as connection:
            with connection:
                yield connection

    def log_message(self, format, *args):
        if not self.path.startswith("/api/health"):
            super().log_message(format, *args)

    def respond(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def json(self, status: int, payload):
        self.respond(status, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                     "application/json; charset=utf-8")

    def error(self, status: int, message: str):
        self.json(status, {"error": message})

    def selected_source(self, query):
        source = parse_qs(query).get("source", ["real"])[0]
        if source not in {"real", "demo"}:
            raise ValueError("source는 real 또는 demo여야 합니다.")
        return source

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                return self.json(200, {"ok": True})
            if parsed.path in {"/api/observations", "/api/analysis", "/api/export.csv"}:
                source = self.selected_source(parsed.query)
                with self.database() as connection:
                    rows = db.list_rows(connection, source)
                if parsed.path == "/api/observations":
                    return self.json(200, {"rows": rows, "source": source})
                if parsed.path == "/api/analysis":
                    return self.json(200, {"analysis": analyze(rows), "source": source})
                return self.respond(200, db.export_csv(rows).encode("utf-8-sig"),
                                    "text/csv; charset=utf-8")
            if parsed.path == "/api/template.csv":
                return self.respond(200, (",".join(db.FIELDS) + "\n").encode("utf-8-sig"),
                                    "text/csv; charset=utf-8")
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if parsed.path in files:
                file, mime = files[parsed.path]
                return self.respond(200, (WEB / file).read_bytes(), mime)
            self.error(404, "찾을 수 없습니다.")
        except ValueError as exc:
            self.error(400, str(exc))
        except FileNotFoundError:
            self.error(500, "화면 파일이 없습니다.")

    def read_body(self, max_bytes: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length가 올바르지 않습니다.") from exc
        if length < 1 or length > max_bytes:
            raise ValueError(f"본문은 1~{max_bytes}바이트여야 합니다.")
        return self.rfile.read(length)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/observations":
                body = self.read_body(64_000)
                try:
                    raw = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise ValueError("JSON 형식이 올바르지 않습니다.") from exc
                row = db.normalize(raw)
                with self.database() as connection:
                    identifier = db.insert_many(connection, [row])[0]
                return self.json(HTTPStatus.CREATED, {"id": identifier})
            if path == "/api/import":
                body = self.read_body(2_000_000)
                try:
                    text = body.decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise ValueError("UTF-8 CSV 파일을 사용하세요.") from exc
                rows = db.parse_csv(text)
                with self.database() as connection:
                    identifiers = db.insert_many(connection, rows)
                return self.json(HTTPStatus.CREATED, {"count": len(identifiers)})
            if path == "/api/demo":
                with self.database() as connection:
                    existing = connection.execute("SELECT COUNT(*) FROM observations WHERE source = 'demo'").fetchone()[0]
                    if not existing:
                        db.insert_many(connection, demo_rows())
                return self.json(200, {"count": existing or len(demo_rows())})
            self.error(404, "찾을 수 없습니다.")
        except ValueError as exc:
            self.error(400, str(exc))

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path == "/api/demo":
            with self.database() as connection:
                deleted = connection.execute("DELETE FROM observations WHERE source = 'demo'").rowcount
            return self.json(200, {"deleted": deleted})
        if path.startswith("/api/observations/"):
            try:
                identifier = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.error(400, "id는 숫자여야 합니다.")
            with self.database() as connection:
                deleted = connection.execute("DELETE FROM observations WHERE id = ?", (identifier,)).rowcount
            return self.json(200 if deleted else 404, {"deleted": deleted})
        self.error(404, "찾을 수 없습니다.")


def main():
    parser = argparse.ArgumentParser(description="Run a local stock-app speed comparison dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 only on a trusted LAN")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=Path(os.getenv("STOCKLAB_DB", DEFAULT_DB)))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.db_path = args.db
    with closing(db.connect(args.db)):
        pass
    print(f"Stock Speed Lab: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
