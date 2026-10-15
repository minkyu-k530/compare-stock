"""SQLite storage and strict input normalization for user-collected observations."""

from __future__ import annotations

import csv
import io
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


FIELDS = (
    "app", "scenario", "stock_code", "keyboard_state", "run_id", "capture_method", "timing_basis",
    "start_state", "market_phase", "completion_criterion", "capture_fps",
    "start_frame", "end_frame", "start_pts_ms", "end_pts_ms",
    "app_version", "run_order", "status", "elapsed_ms", "first_response_ms",
    "content_ms", "frame_jank_pct", "network_rtt_ms",
    "protocol_observation", "network_type", "device", "timestamp", "notes",
)
NUMBERS = ("capture_fps", "elapsed_ms", "first_response_ms", "content_ms",
           "frame_jank_pct", "network_rtt_ms", "start_pts_ms", "end_pts_ms")
INTEGERS = ("run_order", "start_frame", "end_frame")
REQUIRED = ("app", "scenario", "run_id", "network_type", "device")
DEFAULTS = {
    "stock_code": "unspecified",
    "keyboard_state": "unspecified",
    "capture_method": "unspecified",
    "timing_basis": "unspecified",
    "start_state": "unspecified",
    "market_phase": "unspecified",
    "completion_criterion": "unspecified",
    "status": "completed",
}
MIGRATION_COLUMNS = {
    "stock_code": "TEXT NOT NULL DEFAULT 'unspecified'",
    "keyboard_state": "TEXT NOT NULL DEFAULT 'unspecified'",
    "capture_method": "TEXT NOT NULL DEFAULT 'unspecified'",
    "timing_basis": "TEXT NOT NULL DEFAULT 'unspecified'",
    "start_state": "TEXT NOT NULL DEFAULT 'unspecified'",
    "market_phase": "TEXT NOT NULL DEFAULT 'unspecified'",
    "completion_criterion": "TEXT NOT NULL DEFAULT 'unspecified'",
    "capture_fps": "REAL",
    "start_frame": "INTEGER",
    "end_frame": "INTEGER",
    "start_pts_ms": "REAL",
    "end_pts_ms": "REAL",
    "app_version": "TEXT NOT NULL DEFAULT ''",
    "run_order": "INTEGER",
    "status": "TEXT NOT NULL DEFAULT 'completed'",
}


def validate_timing(row: dict) -> float:
    """Validate the timing evidence and return its canonical elapsed milliseconds.

    A supplied elapsed value may be rounded to the nearest millisecond. Saved
    values always come from the underlying frame or presentation timestamps.
    """
    basis = row["timing_basis"]
    if basis not in {"fixed_fps_frames", "frame_timestamps", "playback_seek", "manual", "unspecified"}:
        raise ValueError("timing_basis: 알 수 없는 시간 측정 근거입니다.")
    frames = (row["start_frame"], row["end_frame"])
    timestamps = (row["start_pts_ms"], row["end_pts_ms"])
    if basis == "fixed_fps_frames":
        if row["capture_fps"] is None or any(value is None for value in frames):
            raise ValueError("fixed_fps_frames: capture_fps, start_frame, end_frame가 필요합니다.")
        if any(value is not None for value in timestamps):
            raise ValueError("fixed_fps_frames: PTS 값은 함께 사용할 수 없습니다.")
        if frames[1] <= frames[0]:
            raise ValueError("end_frame: start_frame보다 커야 합니다.")
        calculated = (frames[1] - frames[0]) / row["capture_fps"] * 1000
    elif basis == "frame_timestamps":
        if any(value is None for value in timestamps):
            raise ValueError("frame_timestamps: start_pts_ms, end_pts_ms가 필요합니다.")
        if (frames[0] is None) != (frames[1] is None):
            raise ValueError("frame_timestamps: 시작/종료 프레임 번호를 모두 입력하거나 둘 다 비워두세요.")
        if frames[0] is not None and not 1 <= frames[0] < frames[1]:
            raise ValueError("frame_timestamps: 1부터 시작하는 프레임 번호가 증가해야 합니다.")
        if timestamps[1] <= timestamps[0]:
            raise ValueError("end_pts_ms: start_pts_ms보다 커야 합니다.")
        calculated = timestamps[1] - timestamps[0]
    else:
        if any(value is not None for value in frames + timestamps):
            raise ValueError(f"{basis}: 프레임 번호나 PTS 값 없이 elapsed_ms를 입력하세요.")
        calculated = row["elapsed_ms"]
        if calculated is None:
            raise ValueError("elapsed_ms: 필수 항목입니다.")
    if not 0 < calculated <= 300_000:
        raise ValueError("elapsed_ms: 0 초과, 300000 이하여야 합니다.")
    if basis in {"fixed_fps_frames", "frame_timestamps"}:
        supplied = row["elapsed_ms"]
        if supplied is not None and abs(supplied - calculated) > 0.500001:
            raise ValueError("elapsed_ms: 프레임/PTS 계산값과 0.5ms 넘게 다릅니다.")
        return round(calculated, 3)
    return calculated


def normalize(value: dict, *, source: str = "manual") -> dict:
    """Return a bounded record; raise ValueError on malformed measurements."""
    if not isinstance(value, dict):
        raise ValueError("측정값은 객체여야 합니다.")
    row = {}
    for key in FIELDS:
        raw = value.get(key)
        if key in NUMBERS or key in INTEGERS:
            if raw is None or raw == "":
                row[key] = None
                continue
            if isinstance(raw, bool):
                raise ValueError(f"{key}: 숫자를 입력하세요.")
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key}: 숫자를 입력하세요.") from exc
            if not math.isfinite(number):
                raise ValueError(f"{key}: 유한한 숫자를 입력하세요.")
            if key in INTEGERS:
                if not number.is_integer():
                    raise ValueError(f"{key}: 정수여야 합니다.")
                row[key] = int(number)
            else:
                row[key] = round(number, 3)
        else:
            row[key] = "" if raw is None else str(raw).strip()
    for key, default in DEFAULTS.items():
        if not row[key]:
            row[key] = default
    for key in REQUIRED:
        if row[key] is None or row[key] == "":
            raise ValueError(f"{key}: 필수 항목입니다.")
    if row["app"] not in {"toss", "kis"}:
        raise ValueError("app: toss 또는 kis를 사용하세요.")
    if row["stock_code"] not in {"unspecified", "not_applicable"} and not re.fullmatch(r"[0-9]{6}", row["stock_code"]):
        raise ValueError("stock_code: 국내 종목 코드 6자리, not_applicable 또는 unspecified를 사용하세요.")
    if row["keyboard_state"] not in {"shown", "hidden", "not_applicable", "unspecified"}:
        raise ValueError("keyboard_state: shown, hidden, not_applicable 또는 unspecified를 사용하세요.")
    if row["capture_method"] not in {"screen_recording", "external_camera", "manual_other", "unspecified"}:
        raise ValueError("capture_method: 화면 녹화, 외부 촬영, 기타 또는 미기록 중 하나를 사용하세요.")
    if row["start_state"] not in {"warm", "cold", "unspecified"}:
        raise ValueError("start_state: warm, cold 또는 unspecified 중 하나를 사용하세요.")
    if row["market_phase"] not in {"open", "closed", "unspecified"}:
        raise ValueError("market_phase: open, closed 또는 unspecified 중 하나를 사용하세요.")
    if row["status"] not in {"completed", "timeout", "error"}:
        raise ValueError("status: completed, timeout 또는 error 중 하나를 사용하세요.")
    for key, limit in {"scenario": 80, "run_id": 80, "network_type": 40, "device": 80,
                       "completion_criterion": 160, "app_version": 80,
                       "protocol_observation": 120, "notes": 500}.items():
        if len(row[key]) > limit:
            raise ValueError(f"{key}: {limit}자 이하여야 합니다.")
    if row["capture_fps"] is not None and not 1 <= row["capture_fps"] <= 1000:
        raise ValueError("capture_fps: 1~1000 사이여야 합니다.")
    for key in ("start_frame", "end_frame"):
        if row[key] is not None and not 0 <= row[key] <= 1_000_000_000:
            raise ValueError(f"{key}: 0~1000000000 사이여야 합니다.")
    for key in ("start_pts_ms", "end_pts_ms"):
        if row[key] is not None and not 0 <= row[key] <= 1_000_000_000_000:
            raise ValueError(f"{key}: 0~1000000000000 사이여야 합니다.")
    if row["run_order"] is not None:
        if row["run_order"] not in {1, 2, 3, 4}:
            raise ValueError("run_order: 1~4 사이의 정수여야 합니다.")
    row["elapsed_ms"] = validate_timing(row)
    for key in ("first_response_ms", "content_ms"):
        if row[key] is not None and not 0 <= row[key] <= row["elapsed_ms"]:
            raise ValueError(f"{key}: 0 이상, 전체 응답시간 이하여야 합니다.")
    if (row["first_response_ms"] is not None and row["content_ms"] is not None
            and row["first_response_ms"] + row["content_ms"] > row["elapsed_ms"] + 1e-9):
        raise ValueError("first_response_ms + content_ms는 전체 응답시간 이하여야 합니다.")
    if row["frame_jank_pct"] is not None and not 0 <= row["frame_jank_pct"] <= 100:
        raise ValueError("frame_jank_pct: 0~100 사이여야 합니다.")
    if row["network_rtt_ms"] is not None and not 0 <= row["network_rtt_ms"] <= 300_000:
        raise ValueError("network_rtt_ms: 0~300000 사이여야 합니다.")
    if not row["timestamp"]:
        row["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        try:
            datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp: ISO 8601 형식을 사용하세요.") from exc
    if source not in {"manual", "imported", "demo"}:
        raise ValueError("source: 알 수 없는 값입니다.")
    row["source"] = source
    return row


def parse_csv(text: str) -> list[dict]:
    if len(text.encode("utf-8")) > 2_000_000:
        raise ValueError("CSV 파일은 2 MB 이하여야 합니다.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or any(key not in reader.fieldnames for key in REQUIRED):
        raise ValueError("CSV 헤더에 app, scenario, run_id, network_type, device가 필요합니다.")
    rows = []
    for line, raw in enumerate(reader, start=2):
        if line > 1001:
            raise ValueError("한 번에 최대 1000개 측정값을 가져올 수 있습니다.")
        if None in raw:
            raise ValueError(f"CSV {line}행: 헤더보다 많은 열이 있습니다.")
        try:
            rows.append(normalize(raw, source="imported"))
        except ValueError as exc:
            raise ValueError(f"CSV {line}행: {exc}") from exc
    if not rows:
        raise ValueError("CSV에 측정값이 없습니다.")
    return rows


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app TEXT NOT NULL, scenario TEXT NOT NULL, run_id TEXT NOT NULL,
            stock_code TEXT NOT NULL DEFAULT 'unspecified',
            keyboard_state TEXT NOT NULL DEFAULT 'unspecified',
            capture_method TEXT NOT NULL DEFAULT 'unspecified',
            timing_basis TEXT NOT NULL DEFAULT 'unspecified',
            start_state TEXT NOT NULL DEFAULT 'unspecified',
            market_phase TEXT NOT NULL DEFAULT 'unspecified',
            completion_criterion TEXT NOT NULL DEFAULT 'unspecified',
            capture_fps REAL, start_frame INTEGER, end_frame INTEGER,
            start_pts_ms REAL, end_pts_ms REAL,
            app_version TEXT NOT NULL DEFAULT '',
            run_order INTEGER, status TEXT NOT NULL DEFAULT 'completed',
            elapsed_ms REAL NOT NULL, first_response_ms REAL,
            content_ms REAL, frame_jank_pct REAL, network_rtt_ms REAL,
            protocol_observation TEXT NOT NULL, network_type TEXT NOT NULL,
            device TEXT NOT NULL, timestamp TEXT NOT NULL, notes TEXT NOT NULL,
            source TEXT NOT NULL
        )
    """)
    columns = {column[1] for column in db.execute("PRAGMA table_info(observations)")}
    for name, definition in MIGRATION_COLUMNS.items():
        if name not in columns:
            db.execute(f"ALTER TABLE observations ADD COLUMN {name} {definition}")
    db.commit()
    return db


def insert_many(db: sqlite3.Connection, rows: list[dict]) -> list[int]:
    names = FIELDS + ("source",)
    placeholders = ",".join("?" for _ in names)
    statement = f"INSERT INTO observations ({','.join(names)}) VALUES ({placeholders})"
    with db:
        cursors = [db.execute(statement, tuple(row[name] for name in names)) for row in rows]
    return [cursor.lastrowid for cursor in cursors]


def list_rows(db: sqlite3.Connection, source: str = "real") -> list[dict]:
    where = "source = 'demo'" if source == "demo" else "source != 'demo'"
    return [dict(row) for row in db.execute(
        f"SELECT * FROM observations WHERE {where} ORDER BY id DESC LIMIT 10000"
    )]


def export_csv(rows: list[dict]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=("id",) + FIELDS + ("source",), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
