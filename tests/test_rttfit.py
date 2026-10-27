"""Checks for the RTT-step slope fit, its ledger, and the hotspot delay commands."""

import csv
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock

from stocklab import db, rttfit
from tools import fit_rtt_slope, hotspot_delay, make_rtt_ledger


def ledger_text(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=make_rtt_ledger.FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def session(toss=(600, 3), kis=(500, 1), *, levels=(0, 100, 200, 300), rounds=4, ping=lambda level: 12 + level,
            outcome=lambda row: "completed", noise=lambda row: 0.0):
    """A planned ledger and observations following elapsed = base + k * added RTT."""
    plan = make_rtt_ledger.plan("unit-rtt", list(levels), rounds)
    observations = []
    for row in plan:
        row["outcome"] = outcome(row)
        if row["position"] == 1 and ping is not None:
            row["ping_rtt_ms"] = ping(row["added_rtt_ms"])
        base, k = toss if row["app"] == "toss" else kis
        if row["outcome"] in ("completed", "timeout", "error"):
            observations.append({
                "app": row["app"], "scenario": "search_result_to_detail_quote", "run_id": row["run_id"],
                "network_type": row["network_type"], "device": "SM-S926N", "stock_code": "005930",
                "keyboard_state": "hidden", "capture_method": "screen_recording", "timing_basis": "manual",
                "start_state": "warm", "market_phase": "open", "run_order": row["position"],
                "status": row["outcome"],
                "elapsed_ms": 10000 if row["outcome"] == "timeout" else base + k * row["added_rtt_ms"] + noise(row),
            })
    return rttfit.read_ledger(ledger_text(plan)), observations


class FitTests(unittest.TestCase):
    def test_recovers_planted_slopes_intercepts_and_difference(self):
        ledger, observations = session()
        result = rttfit.fit_session(observations, ledger)
        toss, kis = result["apps"]["toss"]["fit"], result["apps"]["kis"]["fit"]
        self.assertAlmostEqual(toss["slope_k"], 3.0)
        self.assertAlmostEqual(toss["intercept_ms"], 600.0)
        self.assertAlmostEqual(kis["slope_k"], 1.0)
        self.assertAlmostEqual(result["difference"]["fit"]["slope_k"], 2.0)
        self.assertEqual((toss["observations"], toss["blocks"]), (32, 16))
        self.assertEqual(toss["bootstrap_95_ci"]["slope_k"], [3.0, 3.0])
        self.assertEqual([level["median_ms"] for level in result["apps"]["kis"]["levels"]], [500, 600, 700, 800])
        self.assertFalse(result["preliminary"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["conditions"]["stock_code"], "005930")
        json.dumps(result)

    def test_noisy_slope_stays_near_truth_inside_its_interval(self):
        noise = lambda row: random.Random(f"{row['run_id']}/{row['app']}").gauss(0, 60)
        ledger, observations = session(noise=noise)
        fit = rttfit.fit_session(observations, ledger)["apps"]["toss"]["fit"]
        low, high = fit["bootstrap_95_ci"]["slope_k"]
        self.assertLess(abs(fit["slope_k"] - 3.0), 0.3)
        self.assertLess(low, fit["slope_k"])
        self.assertGreater(high, fit["slope_k"])
        self.assertGreater(fit["residual_sd_ms"], 30)

    def test_base_rtt_splits_intercept_into_network_and_rest(self):
        ledger, observations = session()
        fit = rttfit.fit_session(observations, ledger, base_rtt={"toss": 20.0})["apps"]["toss"]["fit"]
        self.assertAlmostEqual(fit["network_share_ms"], 60.0)
        self.assertAlmostEqual(fit["non_network_ms"], 540.0)
        self.assertEqual(fit["bootstrap_95_ci"]["network_share_ms"], [60.0, 60.0])
        with self.assertRaisesRegex(ValueError, "base_rtt"):
            rttfit.fit_session(observations, ledger, base_rtt={"toss": 0.0})

    def test_noncompleted_attempts_are_counted_excluded_and_flagged(self):
        timeout = lambda row: "timeout" if row["added_rtt_ms"] == 300 and row["app"] == "toss" else "completed"
        ledger, observations = session(outcome=timeout)
        result = rttfit.fit_session(observations, ledger)
        self.assertEqual(result["apps"]["toss"]["fit"]["observations"], 24)
        self.assertAlmostEqual(result["apps"]["toss"]["fit"]["slope_k"], 3.0)
        attempts = {(a["app"], a["added_rtt_ms"]): a for a in result["attempts"]}
        self.assertEqual(attempts[("toss", 300)]["timeout"], 8)
        self.assertEqual(result["warnings"], [{"code": "noncompleted_excluded", "detail": "8"}])
        self.assertEqual({e["reason"] for e in result["exclusions"]}, {"noncompleted"})
        self.assertTrue(result["preliminary"])  # no complete pairs at 300 ms

    def test_ledger_mismatches_and_unknown_runs_are_excluded(self):
        ledger, observations = session()
        observations[0]["status"] = "error"
        observations[1]["network_type"] = "wifi"
        observations.append({**observations[2], "run_id": "elsewhere"})
        result = rttfit.fit_session(observations, ledger)
        reasons = sorted(e["reason"] for e in result["exclusions"])
        self.assertEqual(reasons, ["ledger_status_mismatch", "not_in_ledger"])
        self.assertEqual([w["code"] for w in result["warnings"]], ["ledger_status_mismatch", "network_type_mismatch"])
        self.assertEqual(result["apps"]["toss"]["fit"]["observations"] + result["apps"]["kis"]["fit"]["observations"], 63)

    def test_mixed_conditions_are_refused(self):
        ledger, observations = session()
        observations[5]["stock_code"] = "000660"
        with self.assertRaisesRegex(ValueError, "stock_code"):
            rttfit.fit_session(observations, ledger)

    def test_ping_verification_flags_doubled_or_missing_delay(self):
        ledger, observations = session(ping=lambda level: 12 + 2 * level)
        result = rttfit.fit_session(observations, ledger)
        self.assertEqual(result["verification"]["baseline_ping_ms"], 12)
        self.assertEqual([row["measured_added_ms"] for row in result["verification"]["levels"]], [0, 200, 400, 600])
        self.assertIn({"code": "rtt_verification_mismatch", "detail": "100, 200, 300"}, result["warnings"])
        ledger, observations = session(ping=None)
        codes = [w["code"] for w in rttfit.fit_session(observations, ledger)["warnings"]]
        self.assertEqual(codes, ["missing_ping"])

    def test_few_pairs_per_level_are_preliminary_without_interval(self):
        ledger, observations = session(rounds=2)
        result = rttfit.fit_session(observations, ledger)
        self.assertTrue(result["preliminary"])
        self.assertIsNotNone(result["apps"]["toss"]["fit"]["bootstrap_95_ci"])  # 8 blocks
        ledger, observations = session(levels=(0, 100), rounds=2)
        fit = rttfit.fit_session(observations, ledger)["apps"]["toss"]["fit"]
        self.assertIsNone(fit["bootstrap_95_ci"])  # 4 blocks
        self.assertAlmostEqual(fit["slope_k"], 3.0)

    def test_markdown_names_scope_and_preliminary_state(self):
        ledger, observations = session(rounds=2)
        markdown = rttfit.render_markdown(rttfit.fit_session(observations, ledger, base_rtt={"kis": 15.0}))
        self.assertIn("해석 범위", markdown)
        self.assertIn("예비 결과", markdown)
        self.assertIn("| k (ms/ms) | 3.00 | 1.00 | 2.00 |", markdown)
        self.assertIn("| 망 몫(ms) | - | 15.0 |", markdown)


class LedgerTests(unittest.TestCase):
    def test_plan_balances_levels_and_app_order(self):
        rows = make_rtt_ledger.plan("unit-rtt", [0, 100, 200, 300], 4)
        self.assertEqual(len(rows), 64)
        firsts = [row for row in rows if row["position"] == 1]
        for round_number in range(1, 5):
            levels = sorted(r["added_rtt_ms"] for r in firsts if r["round"] == round_number)
            self.assertEqual(levels, [0, 100, 200, 300])
        for level in (0, 100, 200, 300):
            self.assertEqual(sum(r["app"] == "toss" for r in firsts if r["added_rtt_ms"] == level), 2)
        pairs = {}
        for row in rows:
            pairs.setdefault(row["run_id"], set()).add(row["app"])
        self.assertTrue(all(apps == {"toss", "kis"} for apps in pairs.values()))
        self.assertEqual(rows, make_rtt_ledger.plan("unit-rtt", [0, 100, 200, 300], 4))

    def test_command_rejects_plans_without_zero_level(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            make_rtt_ledger.main(["--session-id", "unit-rtt", "--levels", "50,100"])

    def test_read_ledger_requires_outcomes_and_one_level_per_block(self):
        rows = make_rtt_ledger.plan("unit-rtt", [0, 100], 2)
        with self.assertRaisesRegex(ValueError, "outcome"):
            rttfit.read_ledger(ledger_text(rows))
        for row in rows:
            row["outcome"] = "completed"
        rows[1]["added_rtt_ms"] = 999
        with self.assertRaisesRegex(ValueError, "블록"):
            rttfit.read_ledger(ledger_text(rows))
        single = make_rtt_ledger.plan("unit-rtt", [0, 100], 2)
        for row in single:
            row["outcome"], row["added_rtt_ms"] = "completed", 0
        with self.assertRaisesRegex(ValueError, "두 개 이상"):
            rttfit.read_ledger(ledger_text(single))


class CommandLineTests(unittest.TestCase):
    def test_fit_command_reads_session_db_and_writes_reports(self):
        ledger_rows = make_rtt_ledger.plan("unit-rtt", [0, 100, 200], 2)
        for row in ledger_rows:
            row["outcome"] = "completed"
        _, observations = session(levels=(0, 100, 200), rounds=2)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "ledger.csv").write_text(ledger_text(ledger_rows), encoding="utf-8")
            connection = db.connect(folder / "rtt.sqlite3")
            db.insert_many(connection, [db.normalize(row) for row in observations])
            db.insert_many(connection, [db.normalize({**observations[0], "run_id": "demo"}, source="demo")])
            connection.close()
            out, data = folder / "report.md", folder / "report.json"
            arguments = ["--db", str(folder / "rtt.sqlite3"), "--ledger", str(folder / "ledger.csv"),
                         "--base-rtt", "toss=20", "--out", str(out), "--json", str(data)]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(fit_rtt_slope.main(arguments), 0)
            result = json.loads(data.read_text(encoding="utf-8"))
            self.assertAlmostEqual(result["apps"]["toss"]["fit"]["slope_k"], 3.0)
            self.assertEqual(result["exclusions"], [])  # the demo row never reaches the fit
            self.assertIn("기본 망에서의 대략적 분해", out.read_text(encoding="utf-8"))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                fit_rtt_slope.main(arguments)


class HotspotDelayTests(unittest.TestCase):
    def test_on_commands_hold_delay_on_uplink_or_split_it(self):
        [pipe, load] = hotspot_delay.on_commands(100, "bridge100", "in")
        self.assertEqual(pipe, (["dnctl", "pipe", "17101", "config", "delay", "100"], None))
        self.assertEqual(load, (["pfctl", "-a", "com.apple/stocklab-delay", "-f", "-"],
                                "dummynet in quick on bridge100 all pipe 17101\n"))
        up, down, load = hotspot_delay.on_commands(101, "bridge100", "both")
        self.assertEqual((up[0][-1], down[0][-1]), ("50", "51"))
        self.assertIn("dummynet out quick on bridge100 all pipe 17102\n", load[1])

    def test_off_releases_only_a_known_token(self):
        self.assertEqual(hotspot_delay.off_commands("123")[-1], (["pfctl", "-X", "123"], None))
        self.assertNotIn("-X", [arg for argv, _ in hotspot_delay.off_commands(None) for arg in argv])

    def test_parses_pf_token_and_android_ping_output(self):
        self.assertEqual(hotspot_delay.parse_token("pf enabled\nToken : 13018266795237867271\n"),
                         "13018266795237867271")
        output = ("64 bytes from 1.1.1.1: icmp_seq=1 ttl=56 time=112 ms\n"
                  "64 bytes from 1.1.1.1: icmp_seq=2 ttl=56 time=109.4 ms\n")
        self.assertEqual(hotspot_delay.parse_ping(output), [112.0, 109.4])

    def test_dry_run_prints_commands_without_running_anything(self):
        with mock.patch.object(hotspot_delay.subprocess, "run") as run, \
                mock.patch.object(hotspot_delay, "STATE_PATH", Path(tempfile.gettempdir()) / "never-written.json"):
            output = io.StringIO()
            with redirect_stdout(output):
                hotspot_delay.main(["on", "--delay-ms", "200", "--ping", "1.1.1.1", "--dry-run", "--adb", "adb"])
                hotspot_delay.main(["off", "--dry-run"])
            run.assert_not_called()
            self.assertFalse(hotspot_delay.STATE_PATH.exists())
        text = output.getvalue()
        self.assertIn("sudo dnctl pipe 17101 config delay 200", text)
        self.assertIn("adb shell ping -c 10 -i 0.2 1.1.1.1", text)
        self.assertIn("sudo pfctl -a com.apple/stocklab-delay -F dummynet", text)

    def test_rejects_unsafe_host_and_out_of_range_delay(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                hotspot_delay.main(["ping", "1.1.1.1;reboot", "--dry-run"])
            with self.assertRaises(SystemExit):
                hotspot_delay.main(["on", "--delay-ms", "5000", "--dry-run"])
        with self.assertRaises(ValueError):
            hotspot_delay.ping_command("adb", "$(id)", 3)


if __name__ == "__main__":
    unittest.main()
