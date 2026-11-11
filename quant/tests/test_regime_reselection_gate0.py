"""Gate-0 diagnostics tests (stdlib only; runs in the core CI group)."""

import array
import ast
import sys

import pytest
import tempfile
import unittest
from pathlib import Path

try:
    from gate0_diagnostics import (
        annualized_vol,
        audit_provider,
        board_exposure,
        board_of,
        max_drawdown,
        pairwise_correlation_summary,
        read_bin_field,
        reconstruct_paper_series,
    )
    from protocol import (
        CANDIDATES,
        COST_SCHEDULE,
        DISCOVERY_BEAR,
        DISCOVERY_FULL,
        GATES,
        HOLDOUT,
        KNOWN_EVENT_STRESS,
    )
except ImportError:  # pragma: no cover - path bootstrap for direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code" / "research" / "regime_reselection"))
    from gate0_diagnostics import (  # noqa: E402
        annualized_vol,
        audit_provider,
        board_exposure,
        board_of,
        max_drawdown,
        pairwise_correlation_summary,
        read_bin_field,
        reconstruct_paper_series,
    )
    from protocol import (  # noqa: E402
        CANDIDATES,
        COST_SCHEDULE,
        DISCOVERY_BEAR,
        DISCOVERY_FULL,
        GATES,
        HOLDOUT,
        KNOWN_EVENT_STRESS,
    )


def test_finite_json_rejects_nan_tokens():
    from finite_values import finite_json_dumps

    with pytest.raises(ValueError):
        finite_json_dumps({"nav": float("nan")})


def write_bin(path: Path, start: int, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = array.array("f", [float(start)] + [float(v) for v in values])
    if sys.byteorder != "little":
        data.byteswap()
    path.write_bytes(data.tobytes())


def make_provider(root: Path, calendar_days, series_by_code, universe_lines):
    (root / "calendars").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text("\n".join(calendar_days) + "\n")
    (root / "instruments").mkdir(parents=True)
    (root / "instruments" / "csi300.txt").write_text("\n".join(universe_lines) + "\n")
    for code, values in series_by_code.items():
        write_bin(root / "features" / code / "close.day.bin", 0, values)


class Gate0DiagnosticsTests(unittest.TestCase):
    def test_board_classification(self):
        self.assertEqual(board_of("SH688001"), "STAR")
        self.assertEqual(board_of("SZ300001"), "ChiNext")
        self.assertEqual(board_of("BJ830001"), "BSE")
        self.assertEqual(board_of("SH600000"), "Main")
        exposure = board_exposure({"SH688001": {}, "SZ300001": {}, "SH600000": {}})
        self.assertEqual(exposure["counts"], {"STAR": 1, "ChiNext": 1, "Main": 1})
        self.assertAlmostEqual(exposure["growth_share"], 2 / 3)

    def test_paper_nav_return_and_drawdown(self):
        artifacts = [
            {
                "usage_date": "2026-10-20",
                "execution_report": {
                    "date": "2026-10-19",
                    "portfolio_value_pre_trade_open": 100.0,
                    "portfolio_value_close": 110.0,
                },
            },
            {
                "usage_date": "2026-10-21",
                "execution_report": {
                    "date": "2026-10-20",
                    "portfolio_value_pre_trade_open": 110.0,
                    "portfolio_value_close": 99.0,
                },
            },
            {
                "usage_date": "2026-10-22",
                "execution_report": {
                    "date": "2026-10-21",
                    "portfolio_value_pre_trade_open": 99.0,
                    "portfolio_value_close": 104.0,
                },
            },
        ]
        result = reconstruct_paper_series(artifacts)
        self.assertEqual(result["start"], "2026-10-19")
        self.assertEqual(result["end"], "2026-10-21")
        self.assertAlmostEqual(result["return"], 0.04)
        self.assertAlmostEqual(result["max_drawdown"], 99.0 / 110.0 - 1.0)
        self.assertGreater(result["annualized_vol"], 0)

    def test_duplicate_execution_day_fails_closed(self):
        artifacts = [
            {
                "execution_report": {
                    "date": "2026-10-19",
                    "portfolio_value_pre_trade_open": 100.0,
                    "portfolio_value_close": 101.0,
                }
            },
            {
                "execution_report": {
                    "date": "2026-10-19",
                    "portfolio_value_pre_trade_open": 101.0,
                    "portfolio_value_close": 102.0,
                }
            },
        ]
        with self.assertRaises(ValueError):
            reconstruct_paper_series(artifacts)

    def test_nonfinite_bool_and_nonpositive_nav_fail_closed(self):
        base = {"date": "2026-10-19", "portfolio_value_pre_trade_open": 100.0}
        for value in (float("nan"), float("inf"), float("-inf"), True, 0.0, -1.0):
            artifacts = [{"execution_report": {**base, "portfolio_value_close": value}}]
            with self.assertRaises(ValueError):
                reconstruct_paper_series(artifacts)

    def test_duplicate_or_unsorted_dates_fail_closed(self):
        for days in (["2026-10-20", "2026-10-19"], ["2026-10-19", "2026-10-19"]):
            artifacts = [
                {
                    "execution_report": {
                        "date": day,
                        "portfolio_value_pre_trade_open": 100.0,
                        "portfolio_value_close": 101.0,
                    }
                }
                for day in days
            ]
            with self.assertRaises(ValueError):
                reconstruct_paper_series(artifacts)

    def test_expected_sessions_missing_extra_and_incomplete(self):
        artifacts = [
            {
                "execution_report": {
                    "date": day,
                    "portfolio_value_pre_trade_open": 100.0 + i,
                    "portfolio_value_close": 101.0 + i,
                }
            }
            for i, day in enumerate(["2026-10-19", "2026-10-21"])
        ]
        expected = ["2026-10-19", "2026-10-20", "2026-10-21"]
        with self.assertRaises(ValueError):
            reconstruct_paper_series(artifacts, expected_dates=expected)
        incomplete = reconstruct_paper_series(artifacts, expected_dates=expected, allow_incomplete=True)
        self.assertEqual(incomplete["status"], "INCOMPLETE")
        self.assertEqual(incomplete["missing_dates"], ["2026-10-20"])
        with self.assertRaises(ValueError):
            reconstruct_paper_series(artifacts, expected_dates=["2026-10-19"])
        complete = [
            {
                "execution_report": {
                    "date": day,
                    "portfolio_value_pre_trade_open": pre,
                    "portfolio_value_close": close,
                }
            }
            for day, pre, close in (
                ("2026-10-19", 100.0, 101.0),
                ("2026-10-20", 99.0, 102.0),
                ("2026-10-21", 103.0, 104.0),
            )
        ]
        result = reconstruct_paper_series(complete, expected_dates=expected)
        self.assertEqual(result["status"], "PASS")

    def test_real_gate0_cli_passes_calendar_derived_expected_sessions(self):
        """AR08: helper coverage is insufficient unless the real CLI supplies expected dates."""
        import gate0_diagnostics

        tree = ast.parse(Path(gate0_diagnostics.__file__).read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "reconstruct_paper_series"
        ]
        self.assertTrue(calls)
        self.assertTrue(all(any(keyword.arg == "expected_dates" for keyword in call.keywords) for call in calls))

    def test_gate0_main_fails_closed_on_a_missing_market_session(self):
        import json
        from unittest.mock import patch

        import gate0_diagnostics

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            provider = root / "provider"
            signals = root / "signals"
            output = root / "gate0.json"
            (provider / "calendars").mkdir(parents=True)
            (provider / "calendars/day.txt").write_text("2026-10-19\n2026-10-20\n")
            (provider / "instruments").mkdir()
            (provider / "features").mkdir()
            signals.mkdir()
            for suffix in ("", "_csi500", "_csi300"):
                for usage_day, execution_day, nav in (
                    ("2026-10-20", "2026-10-19", 101.0),
                    ("2026-10-21", "2026-10-20", 102.0),
                ):
                    payload = {
                        "usage_date": usage_day,
                        "positions": {},
                        "execution_report": {
                            "date": execution_day,
                            "portfolio_value_pre_trade_open": 100.0,
                            "portfolio_value_close": nav,
                        },
                    }
                    (signals / f"{usage_day}_paper_portfolio{suffix}.json").write_text(json.dumps(payload))
            argv = [
                "gate0_diagnostics.py",
                "--signals",
                str(signals),
                "--provider",
                str(provider),
                "--start",
                "2026-10-19",
                "--end",
                "2026-10-20",
                "--output",
                str(output),
            ]
            with patch.object(sys, "argv", argv):
                gate0_diagnostics.main()
            self.assertEqual(json.loads(output.read_text())["csi500"]["status"], "PASS")
            (signals / "2026-10-21_paper_portfolio_csi500.json").unlink()
            with patch.object(sys, "argv", argv), self.assertRaisesRegex(ValueError, "missing paper sessions"):
                gate0_diagnostics.main()

    def test_max_drawdown_and_vol(self):
        self.assertAlmostEqual(max_drawdown([100, 120, 90, 110]), -0.25)
        self.assertGreater(annualized_vol([0.01, -0.01, 0.02, -0.02]), 0)
        self.assertEqual(annualized_vol([0.01]), 0.0)

    def test_bin_reader_and_coverage_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            days = [f"2026-10-{day:02d}" for day in range(1, 31)]
            values = [10 + 0.1 * i for i in range(len(days))]
            stale_values = values[:10]
            make_provider(
                root,
                days,
                {"sh600000": values, "sh600001": stale_values},
                ["sh600000\t2026-10-01\t2026-10-30", "sh600001\t2026-10-01\t2026-10-30"],
            )
            series = read_bin_field(root, "SH600000", "close")
            self.assertEqual(len(series), len(days))
            self.assertAlmostEqual(series["2026-10-01"], 10.0)
            audit = audit_provider(root, markets=("csi300",))
            self.assertEqual(audit["calendar_end"], "2026-10-30")
            self.assertEqual(audit["markets"]["csi300"]["members"], 2)
            self.assertEqual(audit["markets"]["csi300"]["missing_features"], 0)
            self.assertEqual(audit["markets"]["csi300"]["stale_last_close"], 1)
            self.assertEqual(audit["markets"]["csi300"]["stale_still_member"], 1)

    def test_pairwise_correlation_detects_identical_series(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            days = [f"2026-09-{day:02d}" for day in range(1, 29)]
            pattern = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01] * 5
            values = [10.0]
            for ret in pattern[: len(days) - 1]:
                values.append(values[-1] * (1 + ret))
            twin = [value * 2 for value in values]
            make_provider(
                root,
                days,
                {"sh600000": values, "sh600001": twin},
                ["sh600000\t2026-09-01\t2026-09-28", "sh600001\t2026-09-01\t2026-09-28"],
            )
            summary = pairwise_correlation_summary(root, ["SH600000", "SH600001"], days[-1])
            self.assertEqual(summary["n_pairs"], 1)
            self.assertAlmostEqual(summary["max"], 1.0, places=6)
            self.assertEqual(summary["pairs_over_0_7"], 1)

    def test_protocol_periods_are_disjoint_and_ordered(self):
        self.assertLess(DISCOVERY_FULL[0], DISCOVERY_FULL[1])
        self.assertLess(DISCOVERY_BEAR[0], DISCOVERY_BEAR[1])
        self.assertLess(DISCOVERY_FULL[1], HOLDOUT[0])
        self.assertLess(HOLDOUT[1], KNOWN_EVENT_STRESS[0])
        self.assertEqual(COST_SCHEDULE[0][0], "2018-01-01")
        self.assertGreaterEqual(COST_SCHEDULE[1][0], "2023-08-28")
        self.assertIn("S01", CANDIDATES)
        self.assertIn("S17", CANDIDATES)
        self.assertIn("S18", CANDIDATES)
        self.assertEqual(GATES["SHADOW_WEEKS"], 4)


if __name__ == "__main__":
    unittest.main()
