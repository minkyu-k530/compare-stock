import tempfile
import unittest
from pathlib import Path

from stocklab import db
from stocklab.analysis import analyze
from stocklab.server import demo_rows


BASE = {
    "app": "toss", "scenario": "stock_search", "run_id": "wifi-01",
    "elapsed_ms": 500, "network_type": "Wi-Fi", "device": "test-phone",
}


class StorageTests(unittest.TestCase):
    def test_demo_rows_are_explicitly_synthetic_and_pairable(self):
        rows = demo_rows()
        self.assertEqual(len(rows), 48)
        self.assertTrue(all(row["source"] == "demo" for row in rows))
        self.assertTrue(all(row["capture_method"] == "screen_recording" for row in rows))
        self.assertEqual(analyze(rows)["total_paired_runs"], 24)

    def test_invalid_timing_decomposition_is_rejected_before_save(self):
        with self.assertRaisesRegex(ValueError, "전체 응답시간"):
            db.normalize({**BASE, "first_response_ms": 300, "content_ms": 250})

    def test_csv_import_reports_line_and_keeps_real_separate_from_demo(self):
        csv_text = (
            "app,scenario,run_id,elapsed_ms,network_type,device\n"
            "toss,stock_search,wifi-01,500,Wi-Fi,test-phone\n"
            "kis,stock_search,wifi-01,620,Wi-Fi,test-phone\n"
        )
        rows = db.parse_csv(csv_text)
        self.assertEqual(len(rows), 2)
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "measurements.sqlite3")
            try:
                db.insert_many(connection, rows)
                db.insert_many(connection, [db.normalize(BASE, source="demo")])
                real = db.list_rows(connection, "real")
                demo = db.list_rows(connection, "demo")
                self.assertEqual(len(real), 2)
                self.assertEqual(len(demo), 1)
                self.assertEqual(next(iter(analyze(real)["scenarios"].values()))["paired_runs"], 1)
            finally:
                connection.close()
        with self.assertRaisesRegex(ValueError, "CSV 3행"):
            db.parse_csv(csv_text.replace("620", "wrong"))

    def test_capture_method_defaults_for_legacy_csv_and_is_validated(self):
        row = db.normalize(BASE)
        self.assertEqual(row["capture_method"], "unspecified")
        self.assertEqual(row["stock_code"], "unspecified")
        self.assertEqual(row["keyboard_state"], "unspecified")
        self.assertEqual(row["timing_basis"], "unspecified")
        self.assertEqual(row["start_state"], "unspecified")
        self.assertEqual(row["market_phase"], "unspecified")
        self.assertEqual(row["completion_criterion"], "unspecified")
        self.assertEqual(row["status"], "completed")
        with self.assertRaisesRegex(ValueError, "capture_method"):
            db.normalize({**BASE, "capture_method": "unknown"})

    def test_frame_and_pts_evidence_derive_canonical_elapsed(self):
        fixed = db.normalize({**BASE, "timing_basis": "fixed_fps_frames",
                              "capture_fps": 60, "start_frame": 120, "end_frame": 151,
                              "elapsed_ms": "516.667"})
        self.assertEqual(fixed["elapsed_ms"], 516.667)
        self.assertEqual(fixed["start_frame"], 120)
        pts = db.normalize({**BASE, "timing_basis": "frame_timestamps",
                            "start_pts_ms": 1000.125, "end_pts_ms": 1500.375,
                            "elapsed_ms": ""})
        self.assertEqual(pts["elapsed_ms"], 500.25)
        self.assertEqual(pts["end_pts_ms"], 1500.375)
        csv_rows = db.parse_csv(
            "app,scenario,run_id,timing_basis,capture_fps,start_frame,end_frame,network_type,device\n"
            "toss,stock_search,wifi-01,fixed_fps_frames,60,120,150,Wi-Fi,test-phone\n"
        )
        self.assertEqual(csv_rows[0]["elapsed_ms"], 500)
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "frames.sqlite3")
            try:
                db.insert_many(connection, [fixed, pts])
                saved = db.list_rows(connection)
                self.assertEqual(saved[0]["timing_basis"], "frame_timestamps")
                self.assertEqual(saved[1]["end_frame"], 151)
                exported = db.export_csv(saved)
                self.assertIn("start_pts_ms", exported.splitlines()[0])
                self.assertIn("fixed_fps_frames", exported)
            finally:
                connection.close()

    def test_pts_frame_ids_are_optional_evidence_and_round_trip_through_csv(self):
        pts = db.normalize({**BASE, "timing_basis": "frame_timestamps",
                            "capture_fps": 60, "start_frame": 120, "end_frame": 151,
                            "start_pts_ms": 1000.125, "end_pts_ms": 1200.375,
                            "elapsed_ms": ""})
        self.assertEqual(pts["elapsed_ms"], 200.25)
        self.assertEqual((pts["start_frame"], pts["end_frame"]), (120, 151))
        legacy_pts = db.normalize({**BASE, "timing_basis": "frame_timestamps",
                                   "start_pts_ms": 1000, "end_pts_ms": 1050,
                                   "elapsed_ms": ""})
        self.assertIsNone(legacy_pts["start_frame"])
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "pts.sqlite3")
            try:
                db.insert_many(connection, [pts, legacy_pts])
                saved = db.list_rows(connection)
                exported = db.export_csv(saved)
                imported = db.parse_csv(exported)
                self.assertEqual((imported[1]["start_frame"], imported[1]["end_frame"]), (120, 151))
                self.assertEqual(imported[1]["elapsed_ms"], 200.25)
                self.assertIsNone(imported[0]["start_frame"])
            finally:
                connection.close()

    def test_pts_frame_ids_must_be_a_positive_increasing_pair(self):
        evidence = {**BASE, "timing_basis": "frame_timestamps",
                    "start_pts_ms": 1000, "end_pts_ms": 1200, "elapsed_ms": ""}
        for overrides in (
            {"start_frame": 1},
            {"end_frame": 2},
            {"start_frame": 0, "end_frame": 2},
            {"start_frame": 2, "end_frame": 2},
            {"start_frame": 3, "end_frame": 2},
            {"start_frame": 1.5, "end_frame": 2},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, "frame|프레임"):
                    db.normalize({**evidence, **overrides})
        with self.assertRaisesRegex(ValueError, "CSV 2행"):
            db.parse_csv(
                "app,scenario,run_id,network_type,device,timing_basis,start_frame,start_pts_ms,end_pts_ms\n"
                "toss,stock_search,wifi-01,Wi-Fi,test-phone,frame_timestamps,4,1000,1200\n"
            )

    def test_csv_import_and_export_preserve_explicit_stock_conditions(self):
        rows = db.parse_csv(
            "app,scenario,stock_code,keyboard_state,run_id,elapsed_ms,network_type,device\n"
            "toss,search_result_to_detail_quote,005930,hidden,pair-1,100,Wi-Fi,Galaxy S24+\n"
            "kis,search_result_to_detail_quote,005930,hidden,pair-1,120,Wi-Fi,Galaxy S24+\n"
        )
        self.assertEqual([row["stock_code"] for row in rows], ["005930", "005930"])
        self.assertEqual([row["keyboard_state"] for row in rows], ["hidden", "hidden"])
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "conditions.sqlite3")
            try:
                db.insert_many(connection, rows)
                saved = db.list_rows(connection)
                self.assertEqual(analyze(saved)["total_paired_runs"], 1)
                exported = db.export_csv(saved)
                self.assertIn("stock_code,keyboard_state", exported.splitlines()[0])
                self.assertEqual(db.parse_csv(exported)[0]["stock_code"], "005930")
            finally:
                connection.close()

    def test_timing_evidence_rejects_invalid_or_conflicting_values(self):
        cases = (
            ({"timing_basis": "fixed_fps_frames", "capture_fps": 60,
              "start_frame": 10, "end_frame": 10}, "end_frame"),
            ({"timing_basis": "fixed_fps_frames", "capture_fps": 60,
              "start_frame": 10.5, "end_frame": 20}, "start_frame"),
            ({"timing_basis": "fixed_fps_frames", "start_frame": 10,
              "end_frame": 20}, "capture_fps"),
            ({"timing_basis": "fixed_fps_frames", "capture_fps": 60,
              "start_frame": 10, "end_frame": 40, "elapsed_ms": 600}, "elapsed_ms"),
            ({"timing_basis": "frame_timestamps", "start_pts_ms": 1000,
              "end_pts_ms": 900}, "end_pts_ms"),
            ({"timing_basis": "manual", "start_frame": 1, "end_frame": 2}, "manual"),
            ({"timing_basis": "playback_seek", "start_pts_ms": 1,
              "end_pts_ms": 2}, "playback_seek"),
            ({"timing_basis": "unspecified", "start_frame": 1}, "unspecified"),
            ({"timing_basis": "unknown"}, "timing_basis"),
        )
        for fields, error in cases:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, error):
                    db.normalize({**BASE, **fields})

    def test_structured_conditions_and_noncompleted_rows_are_stored(self):
        row = db.normalize({**BASE, "start_state": "cold", "market_phase": "open",
                            "stock_code": "005930", "keyboard_state": "hidden",
                            "completion_criterion": "가격 표시", "capture_fps": 120,
                            "app_version": "1.2.3", "run_order": 2, "status": "timeout",
                            "elapsed_ms": 3000})
        self.assertEqual(row["run_order"], 2)
        self.assertEqual(row["status"], "timeout")
        self.assertEqual(row["capture_fps"], 120)
        with tempfile.TemporaryDirectory() as folder:
            connection = db.connect(Path(folder) / "new.sqlite3")
            try:
                db.insert_many(connection, [row])
                saved = db.list_rows(connection)[0]
                self.assertEqual(saved["stock_code"], "005930")
                self.assertEqual(saved["keyboard_state"], "hidden")
                self.assertEqual(saved["completion_criterion"], "가격 표시")
                self.assertEqual(saved["status"], "timeout")
            finally:
                connection.close()
        for override, field in (({"capture_fps": 0}, "capture_fps"),
                                ({"stock_code": "660"}, "stock_code"),
                                ({"keyboard_state": "maybe"}, "keyboard_state"),
                                ({"run_order": 2.5}, "run_order"),
                                ({"status": "unknown"}, "status")):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    db.normalize({**BASE, **override})

    def test_existing_database_gets_capture_method_column(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("""
                CREATE TABLE observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app TEXT NOT NULL, scenario TEXT NOT NULL, run_id TEXT NOT NULL,
                    elapsed_ms REAL NOT NULL, first_response_ms REAL,
                    content_ms REAL, frame_jank_pct REAL, network_rtt_ms REAL,
                    protocol_observation TEXT NOT NULL, network_type TEXT NOT NULL,
                    device TEXT NOT NULL, timestamp TEXT NOT NULL, notes TEXT NOT NULL,
                    source TEXT NOT NULL
                )
            """)
            connection.execute("""
                INSERT INTO observations
                (app, scenario, run_id, elapsed_ms, protocol_observation,
                 network_type, device, timestamp, notes, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("toss", "stock_search", "old", 480, "", "Wi-Fi",
                  "test-phone", "2026-10-15T00:00:00+00:00", "", "manual"))
            connection.commit()
            connection.close()
            migrated = db.connect(path)
            try:
                db.insert_many(migrated, [db.normalize({**BASE, "capture_method": "screen_recording"})])
                self.assertEqual(db.list_rows(migrated)[0]["capture_method"], "screen_recording")
                self.assertEqual(db.list_rows(migrated)[1]["capture_method"], "unspecified")
                self.assertEqual(db.list_rows(migrated)[1]["stock_code"], "unspecified")
                self.assertEqual(db.list_rows(migrated)[1]["keyboard_state"], "unspecified")
                self.assertEqual(db.list_rows(migrated)[1]["timing_basis"], "unspecified")
                self.assertIsNone(db.list_rows(migrated)[1]["start_frame"])
                self.assertEqual(db.list_rows(migrated)[1]["start_state"], "unspecified")
                self.assertEqual(db.list_rows(migrated)[1]["status"], "completed")
            finally:
                migrated.close()


if __name__ == "__main__":
    unittest.main()
