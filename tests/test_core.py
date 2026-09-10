import tempfile
import unittest
from pathlib import Path

import pandas as pd

from fund_data import init_db, load_frame, save_bundle
from metrics import adjust_nav, analyze


class CacheTests(unittest.TestCase):
    def test_dataframe_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            frame = pd.DataFrame({"nav_date": ["2026-09-09"], "unit_nav": [2.0]})
            save_bundle(database, "050009", {"nav": (frame, "2026-09-09", "fixture")})
            actual = load_frame(database, "050009", "nav")
            self.assertEqual(actual.to_dict("records"), frame.to_dict("records"))

    def test_empty_refresh_cannot_replace_old_data(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            old = pd.DataFrame({"nav_date": ["2026-09-09"], "unit_nav": [2.0]})
            save_bundle(database, "050009", {"nav": (old, "2026-09-09", "fixture")})
            with self.assertRaisesRegex(ValueError, "空数据"):
                save_bundle(database, "050009", {"nav": (pd.DataFrame(), "2026-09-10", "fixture")})
            actual = load_frame(database, "050009", "nav")
            self.assertEqual(actual.iloc[0]["unit_nav"], 2.0)


class MetricTests(unittest.TestCase):
    def test_cash_dividend_is_reinvested(self):
        nav = pd.DataFrame({"nav_date": ["2026-01-01", "2026-01-02"], "unit_nav": [1.0, 0.9]})
        dividends = pd.DataFrame({"ex_date": ["2026-01-02"], "dividend_per_unit": [0.1]})
        actual = adjust_nav(nav, dividends)
        self.assertAlmostEqual(actual.iloc[-1]["adjusted_nav"], 1.0)

    def test_split_does_not_create_a_false_loss(self):
        nav = pd.DataFrame({"nav_date": ["2026-01-01", "2026-01-02"], "unit_nav": [1.0, 0.5]})
        splits = pd.DataFrame({"split_date": ["2026-01-02"], "split_ratio": [2.0]})
        actual = adjust_nav(nav, pd.DataFrame(), splits)
        self.assertAlmostEqual(actual.iloc[-1]["adjusted_nav"], 1.0)

    def test_drawdown_and_short_sample(self):
        nav = pd.DataFrame({
            "nav_date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "adjusted_nav": [1.0, 1.2, 0.9],
        })
        result = analyze(nav, manager_start_date="2026-01-01")
        self.assertAlmostEqual(result["max_drawdown"], -0.25)
        self.assertEqual(result["drawdown_peak"], "2026-01-02")
        self.assertAlmostEqual(result["manager_return"], -0.1)
        self.assertIsNone(result["volatility"])
        self.assertIsNone(result["sharpe"])

    def test_sample_thresholds(self):
        dates = pd.bdate_range("2025-01-01", periods=251)
        nav = pd.DataFrame({"nav_date": dates, "adjusted_nav": [1 + i / 1000 for i in range(251)]})
        result = analyze(nav)
        self.assertIsNotNone(result["annualized_return"])
        self.assertIsNotNone(result["volatility"])
        self.assertIsNotNone(result["sharpe"])

    def test_zero_volatility_has_no_sharpe_ratio(self):
        dates = pd.bdate_range("2026-01-01", periods=31)
        nav = pd.DataFrame({"nav_date": dates, "adjusted_nav": [1.01 ** i for i in range(31)]})
        result = analyze(nav)
        self.assertIsNone(result["sharpe"])
