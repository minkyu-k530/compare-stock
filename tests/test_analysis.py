"""Behavioral tests for matched mobile UI measurements."""

import json
import unittest

from stocklab.analysis import analyze


def observation(app, scenario="quote", run_id="1", elapsed_ms=100, **extra):
    return {
        "app": app,
        "scenario": scenario,
        "run_id": run_id,
        "elapsed_ms": elapsed_ms,
        "device": "Pixel 8",
        "network_type": "5G",
        **extra,
    }


def grouped(result, scenario="quote", capture_method="unspecified", start_state="unspecified",
            market_phase="unspecified", completion_criterion="unspecified", capture_fps=None,
            timing_basis="unspecified", device="Pixel 8", network_type="5G",
            stock_code="unspecified", keyboard_state="unspecified"):
    key = json.dumps([scenario, stock_code, keyboard_state, capture_method, timing_basis, start_state, market_phase,
                      completion_criterion, capture_fps, device, network_type],
                     ensure_ascii=False, separators=(",", ":"))
    return result["scenarios"][key]


class AnalysisTests(unittest.TestCase):
    def test_stock_code_and_keyboard_state_are_independent_pairing_conditions(self):
        rows = []
        conditions = (
            ("005930", "hidden", 100, 120),
            ("000660", "hidden", 200, 230),
            ("005930", "shown", 300, 330),
            ("unspecified", "unspecified", 400, 440),
        )
        for code, keyboard, toss_ms, kis_ms in conditions:
            rows.extend((
                observation("toss", run_id="same", elapsed_ms=toss_ms,
                            stock_code=code, keyboard_state=keyboard),
                observation("kis", run_id="same", elapsed_ms=kis_ms,
                            stock_code=code, keyboard_state=keyboard),
            ))
        rows.extend((
            observation("toss", run_id="mismatch", stock_code="005930",
                        keyboard_state="hidden"),
            observation("kis", run_id="mismatch", stock_code="000660",
                        keyboard_state="shown"),
        ))
        result = analyze(rows)
        self.assertEqual(result["total_paired_runs"], 4)
        self.assertEqual(result["analysis_group_count"], 5)
        for code, keyboard, toss_ms, kis_ms in conditions:
            group = grouped(result, stock_code=code, keyboard_state=keyboard)
            self.assertEqual(group["paired_runs"], 1)
            self.assertEqual(group["metrics"]["elapsed_ms"]["toss_minus_kis"]["median"], toss_ms - kis_ms)
            self.assertEqual(group["quality_checks"]["condition_completeness"], {
                "stock_code_recorded": code != "unspecified",
                "keyboard_state_recorded": keyboard != "unspecified",
            })
        self.assertEqual(
            grouped(result, stock_code="005930", keyboard_state="hidden")["excluded_groups"][0]["reason"],
            "missing_counterpart",
        )

    def test_paired_latency_direction_percentiles_and_reproducible_ci(self):
        rows = []
        for run in range(1, 11):
            rows.extend(
                [
                    observation("toss", run_id=str(run), elapsed_ms=run * 10 + 100),
                    observation("kis", run_id=str(run), elapsed_ms=run * 10 + 200),
                ]
            )

        result = analyze(rows)
        elapsed = grouped(result)["metrics"]["elapsed_ms"]
        self.assertEqual(result["total_paired_runs"], 10)
        self.assertEqual(elapsed["toss"]["median"], 155)
        self.assertIsNone(elapsed["toss"]["p95"])
        self.assertEqual(elapsed["toss_minus_kis"]["median"], -100)
        self.assertEqual(elapsed["toss_minus_kis"]["toss_lower_runs"], 10)
        self.assertEqual(elapsed["toss_minus_kis"]["bootstrap_95_ci"], [-100, -100])
        self.assertEqual(analyze(rows), result)
        json.dumps(result, allow_nan=False)

    def test_p95_is_reported_only_at_twenty_completed_pairs(self):
        rows = [observation(app, run_id=str(run), elapsed_ms=100 + run + offset)
                for run in range(20)
                for app, offset in (("toss", 0), ("kis", 20))]
        self.assertIsNone(grouped(analyze(rows[:-2]))["metrics"]["elapsed_ms"]["toss"]["p95"])
        twenty = grouped(analyze(rows))["metrics"]["elapsed_ms"]
        self.assertAlmostEqual(twenty["toss"]["p95"], 118.05)
        self.assertAlmostEqual(twenty["kis"]["p95"], 138.05)

    def test_quality_checks_use_only_completed_pairs_and_report_recording_issues(self):
        rows = [
            observation("toss", run_id="a", app_version="1.0", run_order=1),
            observation("kis", run_id="a", app_version="2.0", run_order=2),
            observation("toss", run_id="b", app_version="1.1", run_order=4),
            observation("kis", run_id="b", app_version="2.0", run_order=3),
            observation("toss", run_id="c", app_version="", run_order=None),
            observation("kis", run_id="c", app_version="2.0", run_order=None),
            observation("toss", run_id="d", app_version="1.1", run_order=2),
            observation("kis", run_id="d", app_version="", run_order=2),
            observation("toss", run_id="timeout", app_version="9.9", run_order=1,
                        status="timeout"),
            observation("kis", run_id="timeout", app_version="9.9", run_order=2),
        ]
        group = grouped(analyze(rows))
        quality = group["quality_checks"]
        self.assertEqual(group["paired_runs"], 4)
        self.assertEqual(quality["app_versions"]["toss"], {
            "counts": {"1.0": 1, "1.1": 2}, "missing": 1, "mixed": True,
        })
        self.assertEqual(quality["app_versions"]["kis"], {
            "counts": {"2.0": 3}, "missing": 1, "mixed": False,
        })
        self.assertEqual(quality["run_order"], {
            "toss_first": 1, "kis_first": 1, "unrecorded": 1,
            "duplicate": 1, "duplicate_run_ids": ["d"],
        })
        self.assertEqual(quality["warnings"], [
            "mixed_toss_versions", "missing_run_order", "duplicate_run_order",
            "missing_stock_code", "missing_keyboard_state",
        ])

    def test_quality_checks_report_kis_version_mix_separately(self):
        rows = [
            observation("toss", run_id="a", app_version="1.0", run_order=1),
            observation("kis", run_id="a", app_version="2.0", run_order=2),
            observation("toss", run_id="b", app_version="1.0", run_order=4),
            observation("kis", run_id="b", app_version="2.1", run_order=3),
        ]
        quality = grouped(analyze(rows))["quality_checks"]
        self.assertFalse(quality["app_versions"]["toss"]["mixed"])
        self.assertTrue(quality["app_versions"]["kis"]["mixed"])
        self.assertEqual(quality["warnings"], [
            "mixed_kis_versions", "missing_stock_code", "missing_keyboard_state",
        ])

    def test_unpaired_duplicate_and_mismatched_conditions_are_excluded(self):
        rows = [
            observation("toss", run_id="missing"),
            observation("toss", run_id="duplicate"),
            observation("toss", run_id="duplicate", elapsed_ms=105),
            observation("kis", run_id="duplicate", elapsed_ms=120),
            observation("toss", run_id="different_device"),
            observation("kis", run_id="different_device", device="iPhone 16"),
            observation("toss", run_id="valid", elapsed_ms=90),
            observation("kis", run_id="valid", elapsed_ms=120),
            observation("toss", scenario="order", run_id="valid", elapsed_ms=250),
            observation("kis", scenario="order", run_id="valid", elapsed_ms=240),
        ]

        result = analyze(rows)
        quote = grouped(result)
        self.assertEqual(quote["paired_runs"], 1)
        self.assertEqual(grouped(result, scenario="order")["paired_runs"], 1)
        self.assertEqual(result["analysis_group_count"], 3)
        self.assertEqual(grouped(result, device="iPhone 16")["paired_runs"], 0)
        self.assertEqual(
            {group["run_id"]: group["reason"] for group in quote["excluded_groups"]},
            {
                "missing": "missing_counterpart",
                "duplicate": "duplicate_or_ambiguous",
                "different_device": "missing_counterpart",
            },
        )
        self.assertEqual(
            quote["metrics"]["elapsed_ms"]["toss_minus_kis"]["bootstrap_95_ci"],
            None,
        )

    def test_optional_metrics_only_use_complete_pairs_and_remainder_is_neutral(self):
        rows = [
            observation(
                "toss", run_id="1", elapsed_ms="100", first_response_ms="30",
                content_ms="40", frame_jank_pct="10", protocol_observation="HTTP/2",
                timestamp="2026-10-15T10:30:00+09:00",
            ),
            observation(
                "kis", run_id="1", elapsed_ms="120", first_response_ms="50",
                content_ms="40", frame_jank_pct="20", protocol_observation="HTTP/2",
            ),
            observation("toss", run_id="2", elapsed_ms=100, frame_jank_pct=15),
            observation("kis", run_id="2", elapsed_ms=110),
        ]

        result = grouped(analyze(rows))
        self.assertEqual(result["metrics"]["elapsed_ms"]["paired_runs"], 2)
        self.assertEqual(result["metrics"]["first_response_ms"]["paired_runs"], 1)
        self.assertEqual(result["metrics"]["frame_jank_pct"]["paired_runs"], 1)
        self.assertNotIn("network_rtt_ms", result["metrics"])
        self.assertEqual(result["metrics"]["unattributed_ms"]["toss"]["median"], 30)
        self.assertEqual(result["entered_protocol_observations"]["toss"], ["HTTP/2"])

    def test_capture_methods_have_separate_metrics_and_pairs(self):
        rows = [
            observation("toss", run_id="1", elapsed_ms=100,
                        capture_method="screen_recording"),
            observation("kis", run_id="1", elapsed_ms=120,
                        capture_method="screen_recording"),
            observation("toss", run_id="1", elapsed_ms=90,
                        capture_method="external_camera"),
            observation("kis", run_id="1", elapsed_ms=130,
                        capture_method="external_camera"),
            observation("toss", run_id="2", elapsed_ms=105,
                        capture_method="screen_recording"),
            observation("kis", run_id="2", elapsed_ms=140,
                        capture_method="external_camera"),
            observation("toss", run_id="3", elapsed_ms=75),
            observation("kis", run_id="3", elapsed_ms=80,
                        capture_method=""),
        ]

        result = analyze(rows)
        self.assertEqual(result["scenario_count"], 1)
        self.assertEqual(result["analysis_group_count"], 3)
        self.assertEqual(result["total_paired_runs"], 3)
        screen = grouped(result, capture_method="screen_recording")
        camera = grouped(result, capture_method="external_camera")
        legacy = grouped(result)
        for group, method in ((screen, "screen_recording"),
                              (camera, "external_camera"),
                              (legacy, "unspecified")):
            self.assertEqual(group["scenario"], "quote")
            self.assertEqual(group["capture_method"], method)
            self.assertEqual(group["paired_runs"], 1)
        self.assertEqual(screen["metrics"]["elapsed_ms"]["toss_minus_kis"]["median"], -20)
        self.assertEqual(camera["metrics"]["elapsed_ms"]["toss_minus_kis"]["median"], -40)
        self.assertEqual(legacy["metrics"]["elapsed_ms"]["toss_minus_kis"]["median"], -5)
        self.assertEqual(screen["excluded_groups"][0]["run_id"], "2")
        self.assertEqual(camera["excluded_groups"][0]["run_id"], "2")
        self.assertEqual(screen["excluded_groups"][0]["reason"], "missing_counterpart")
        self.assertEqual(camera["excluded_groups"][0]["reason"], "missing_counterpart")

    def test_timing_basis_device_and_network_do_not_pool_latency(self):
        rows = [
            observation("toss", run_id="a", timing_basis="fixed_fps_frames",
                        capture_fps=60, start_frame=0, end_frame=6),
            observation("kis", run_id="a", elapsed_ms=None,
                        timing_basis="fixed_fps_frames", capture_fps=60,
                        start_frame=0, end_frame=7),
            observation("toss", run_id="b", elapsed_ms=90, timing_basis="playback_seek"),
            observation("kis", run_id="b", elapsed_ms=110, timing_basis="playback_seek"),
            observation("toss", run_id="c", elapsed_ms=80, device="iPhone 16"),
            observation("kis", run_id="c", elapsed_ms=100, device="iPhone 16"),
            observation("toss", run_id="d", elapsed_ms=70, network_type="Wi-Fi"),
            observation("kis", run_id="d", elapsed_ms=90, network_type="Wi-Fi"),
        ]
        result = analyze(rows)
        self.assertEqual(result["analysis_group_count"], 4)
        self.assertEqual(result["total_paired_runs"], 4)
        fixed = grouped(result, timing_basis="fixed_fps_frames", capture_fps=60.0)
        seek = grouped(result, timing_basis="playback_seek")
        iphone = grouped(result, device="iPhone 16")
        wifi = grouped(result, network_type="Wi-Fi")
        self.assertEqual(fixed["metrics"]["elapsed_ms"]["toss_minus_kis"]["median"], -16.667)
        self.assertEqual(seek["paired_runs"], 1)
        self.assertEqual(iphone["paired_runs"], 1)
        self.assertEqual(wifi["paired_runs"], 1)

    def test_study_conditions_and_failures_are_not_pooled_into_latency(self):
        rows = [
            observation("toss", run_id="a", elapsed_ms=90, start_state="warm",
                        market_phase="open", completion_criterion="가격 표시", capture_fps=60),
            observation("kis", run_id="a", elapsed_ms=120, start_state="warm",
                        market_phase="open", completion_criterion="가격 표시", capture_fps=60),
            observation("toss", run_id="b", elapsed_ms=3000, start_state="warm",
                        market_phase="open", completion_criterion="가격 표시", capture_fps=60,
                        status="timeout"),
            observation("kis", run_id="b", elapsed_ms=130, start_state="warm",
                        market_phase="open", completion_criterion="가격 표시", capture_fps=60),
            observation("toss", run_id="a", elapsed_ms=50, start_state="cold",
                        market_phase="open", completion_criterion="가격 표시", capture_fps=60),
            observation("kis", run_id="a", elapsed_ms=80, start_state="cold",
                        market_phase="open", completion_criterion="가격 표시", capture_fps=60),
        ]
        result = analyze(rows)
        warm = grouped(result, start_state="warm", market_phase="open",
                       completion_criterion="가격 표시", capture_fps=60.0)
        cold = grouped(result, start_state="cold", market_phase="open",
                       completion_criterion="가격 표시", capture_fps=60.0)
        self.assertEqual(result["scenario_count"], 1)
        self.assertEqual(result["analysis_group_count"], 2)
        self.assertEqual(result["total_paired_runs"], 2)
        self.assertEqual(result["noncompleted_observations"], 1)
        self.assertEqual(warm["metrics"]["elapsed_ms"]["paired_runs"], 1)
        self.assertEqual(warm["status_counts"]["toss"], {"completed": 1, "timeout": 1, "error": 0})
        self.assertEqual(warm["excluded_groups"][0]["reason"], "noncompleted")
        self.assertEqual(cold["paired_runs"], 1)

    def test_rejects_invalid_measurements_with_row_context(self):
        invalid_fields = [
            ({"elapsed_ms": "nan"}, "elapsed_ms"),
            ({"elapsed_ms": 0}, "elapsed_ms"),
            ({"frame_jank_pct": 101}, "frame_jank_pct"),
            ({"first_response_ms": 80, "content_ms": 30}, "content_ms"),
            ({"timestamp": "yesterday"}, "timestamp"),
            ({"app": "other"}, "app"),
            ({"start_state": "maybe"}, "start_state"),
            ({"market_phase": "maybe"}, "market_phase"),
            ({"capture_fps": 0}, "capture_fps"),
            ({"run_order": 2.5}, "run_order"),
            ({"timing_basis": "unknown"}, "timing_basis"),
            ({"timing_basis": "fixed_fps_frames", "capture_fps": 60,
              "start_frame": 10, "end_frame": 20}, "elapsed_ms"),
            ({"status": "maybe"}, "status"),
            ({"stock_code": "5930"}, "stock_code"),
            ({"keyboard_state": "visible"}, "keyboard_state"),
        ]
        for override, field in invalid_fields:
            with self.subTest(field=field, override=override):
                with self.assertRaisesRegex(ValueError, rf"row 1: .*{field}"):
                    row = observation("toss")
                    row.update(override)
                    analyze([row])

    def test_empty_dataset_is_serializable(self):
        result = analyze([])
        self.assertEqual(result["input_rows"], 0)
        self.assertEqual(result["scenarios"], {})
        json.dumps(result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
