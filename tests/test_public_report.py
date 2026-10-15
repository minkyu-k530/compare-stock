"""Checks for the aggregate-only public portfolio export."""

import csv
from pathlib import Path
import tempfile
import unittest

from stocklab import db
from tools.build_public_report import (
    read_analysis, read_ledger_outcomes, render_report, write_report,
)


class PublicReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "observations.sqlite3"

    def insert(self, app, source, elapsed_ms, **extra):
        row = db.normalize({
            "app": app,
            "scenario": "search_result_to_detail_quote",
            "run_id": "pair-1",
            "device": "Galaxy S24+",
            "network_type": "Wi-Fi",
            "elapsed_ms": elapsed_ms,
            **extra,
        }, source=source)
        connection = db.connect(self.database)
        try:
            db.insert_many(connection, [row])
        finally:
            connection.close()

    def test_empty_and_demo_only_reports_show_no_measured_result(self):
        connection = db.connect(self.database)
        connection.close()
        empty = render_report(read_analysis(self.database))
        self.assertIn("실측 결과 없음", empty)
        self.insert("toss", "demo", 10)
        self.insert("kis", "demo", 20)
        demo_only = render_report(read_analysis(self.database))
        self.assertIn("실측 결과 없음", demo_only)
        self.assertNotIn("10.0 ms", demo_only)

    def test_real_pair_is_aggregated_without_private_or_free_text(self):
        secret = "PRIV123<script>alert(1)</script>"
        for app, latency in (("toss", 100), ("kis", 120)):
            self.insert(
                app, "manual", latency, run_id=secret,
                scenario=secret, device=secret, network_type=secret,
                completion_criterion=secret, app_version=secret,
                protocol_observation=secret, notes=secret,
            )
        self.insert("toss", "demo", 1, run_id="demo-pair")
        self.insert("kis", "demo", 2, run_id="demo-pair")
        result = read_analysis(self.database)
        self.assertEqual(result["input_rows"], 2)
        self.assertEqual(result["total_paired_runs"], 1)
        html = render_report(result)
        self.assertIn("완료 비교 쌍 1개", html)
        self.assertIn("100.0 ms", html)
        self.assertIn("120.0 ms", html)
        self.assertIn("-20.0 ms", html)
        self.assertNotIn(secret, html)
        self.assertNotIn("demo-pair", html)
        self.assertIn("예비 결과 · 유효 20쌍 미만", html)
        self.assertIn("순서 미기록 1쌍", html)
        self.assertIn("조건 미기록: 종목 코드, 키보드 상태", html)
        self.assertIn("계획된 시도 대장 미포함", html)
        self.assertIn("촬영 실패(invalid_capture)와 미시작(not_started) 건수가 이 보고서에 포함되지 않았습니다", html)
        self.assertIn("Content-Security-Policy", html)
        output = Path(self.temporary.name) / "public.html"
        write_report(output, html)
        self.assertEqual(output.read_text(encoding="utf-8"), html)

    def test_missing_database_is_not_created(self):
        with self.assertRaisesRegex(ValueError, "찾을 수 없습니다"):
            read_analysis(self.database)
        self.assertFalse(self.database.exists())

    def test_ledger_counts_only_outcomes_and_rejects_unfinished_or_wrong_size(self):
        connection = db.connect(self.database)
        connection.close()
        ledger = Path(self.temporary.name) / "main.csv"
        outcomes = (["completed"] * 43 + ["timeout"] * 4 + ["error"] * 2
                    + ["invalid_capture"] * 3 + ["not_started"] * 8)

        def save(rows):
            with ledger.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("outcome", "video_file", "notes"))
                writer.writeheader()
                for outcome in rows:
                    writer.writerow({"outcome": outcome, "video_file": "SECRET_VIDEO.mp4",
                                     "notes": "SECRET_ACCOUNT"})

        save(outcomes)
        counts = read_ledger_outcomes(ledger)
        self.assertEqual(counts, {
            "completed": 43, "timeout": 4, "error": 2,
            "invalid_capture": 3, "not_started": 8,
        })
        html = render_report(read_analysis(self.database), counts)
        self.assertIn("계획 60회 · 시작 52회 · 완료 43회", html)
        self.assertIn("촬영 실패 3회 · 미시작 8회", html)
        self.assertNotIn("SECRET_VIDEO", html)
        self.assertNotIn("SECRET_ACCOUNT", html)
        self.assertNotIn("계획된 시도 대장 미포함", html)
        save(outcomes[:-1])
        with self.assertRaisesRegex(ValueError, "60행"):
            read_ledger_outcomes(ledger)
        save(outcomes[:-1] + [""])
        with self.assertRaisesRegex(ValueError, "outcome"):
            read_ledger_outcomes(ledger)

    def test_quality_summary_omits_versions_and_duplicate_run_ids(self):
        pairs = (
            ("run-secret-a", "1.0-secret", "2.0-secret", 1, 2),
            ("run-secret-b", "1.1-secret", "", 4, 3),
            ("run-secret-c", "1.1-secret", "2.0-secret", 2, 2),
        )
        for run_id, toss_version, kis_version, toss_order, kis_order in pairs:
            self.insert("toss", "manual", 100, run_id=run_id,
                        app_version=toss_version, run_order=toss_order)
            self.insert("kis", "manual", 120, run_id=run_id,
                        app_version=kis_version, run_order=kis_order)
        html = render_report(read_analysis(self.database))
        self.assertIn("앱 버전 미기록 토스 0쌍 · 한국투자 1쌍", html)
        self.assertIn("버전 혼합 토스 있음 · 한국투자 없음", html)
        self.assertIn("토스 먼저 1쌍 · 한국투자 먼저 1쌍", html)
        self.assertIn("순서 중복 1쌍", html)
        for run_id, toss_version, kis_version, _, _ in pairs:
            self.assertNotIn(run_id, html)
            self.assertNotIn(toss_version, html)
            if kis_version:
                self.assertNotIn(kis_version, html)

    def test_public_report_labels_stock_and_keyboard_without_free_text(self):
        for app, elapsed in (("toss", 100), ("kis", 120)):
            self.insert(app, "manual", elapsed, stock_code="005930", keyboard_state="hidden")
        html = render_report(read_analysis(self.database))
        self.assertIn("종목 코드 005930", html)
        self.assertIn("키보드 숨김", html)
        self.assertNotIn("stock_code", html)
        with self.assertRaisesRegex(ValueError, "stock_code"):
            self.insert("toss", "manual", 100, stock_code="005930<script>")


if __name__ == "__main__":
    unittest.main()
