import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from fund_data import fetch_catalog, fetch_fund, init_db, load_frame, refresh_fund, save_bundle
from metrics import adjust_nav, analyze


class CoreApi:
    def __init__(self, cumulative_nav=1.0, index_close=None, index_date="2026-09-09", fee="0.15%"):
        self.cumulative_nav = cumulative_nav
        self.index_close = index_close
        self.index_date = index_date
        self.fee = fee

    def fund_overview_em(self, symbol):
        return pd.DataFrame({"基金简称": ["测试基金"], "基金代码": [symbol]})

    def fund_open_fund_info_em(self, symbol, indicator):
        if indicator == "单位净值走势":
            return pd.DataFrame({"净值日期": ["2026-09-09"], "单位净值": [1.0]})
        if indicator == "累计净值走势":
            return pd.DataFrame({"净值日期": ["2026-09-09"], "累计净值": [self.cumulative_nav]})
        if indicator == "分红送配详情":
            return pd.DataFrame(columns=["除息日", "每份分红"])
        return pd.DataFrame(columns=["拆分折算日", "拆分折算比例"])

    def stock_zh_index_daily_em(self, symbol, start_date, end_date):
        return pd.DataFrame({"date": [self.index_date], "close": [self.index_close]})

    def fund_individual_detail_info_xq(self, symbol):
        return pd.DataFrame({"费用类型": ["管理费"], "条件或名称": ["每年"], "费用": [self.fee]})


class OptionalApi(CoreApi):
    def __init__(self, holding_code="000001", holding_name="平安银行", industry_category="金融", asset_category="股票", fee_type="管理费", fee_condition="每年", **kwargs):
        super().__init__(**kwargs)
        self.holding_code = holding_code
        self.holding_name = holding_name
        self.industry_category = industry_category
        self.asset_category = asset_category
        self.fee_type = fee_type
        self.fee_condition = fee_condition

    def fund_portfolio_hold_em(self, symbol, date):
        return pd.DataFrame({"序号": [1], "股票代码": [self.holding_code], "股票名称": [self.holding_name], "占净值比例": [10], "持仓市值": [100], "季度": ["2026年2季度"]})

    def fund_portfolio_industry_allocation_em(self, symbol, date):
        return pd.DataFrame({"行业类别": [self.industry_category], "占净值比例": [10], "截止时间": ["2026-06-30"]})

    def fund_individual_detail_hold_xq(self, symbol, date):
        return pd.DataFrame({"资产类型": [self.asset_category], "仓位占比": [80]})

    def fund_individual_detail_info_xq(self, symbol):
        return pd.DataFrame({"费用类型": [self.fee_type], "条件或名称": [self.fee_condition], "费用": [self.fee]})


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


class RefreshTests(unittest.TestCase):
    def test_fetch_accepts_current_per_ten_dividend_schema(self):
        class CurrentDividendApi(CoreApi):
            def fund_open_fund_info_em(self, symbol, indicator):
                if indicator == "单位净值走势":
                    return pd.DataFrame({"净值日期": ["2026-09-08", "2026-09-09"], "单位净值": [1.0, 0.9]})
                if indicator == "累计净值走势":
                    return pd.DataFrame({"净值日期": ["2026-09-08", "2026-09-09"], "累计净值": [1.0, 1.0]})
                if indicator == "分红送配详情":
                    return pd.DataFrame({"年份": ["2026年"], "权益登记日": ["2026-09-09"], "除息日": ["2026-09-09"], "每10份分红": ["每10份派现金1.0000元"], "分红发放日": ["2026-09-11"]})
                return super().fund_open_fund_info_em(symbol, indicator)

        bundle, _ = fetch_fund("050009", CurrentDividendApi())

        self.assertAlmostEqual(bundle["nav"][0].iloc[-1]["adjusted_nav"], 1.0)

    def test_fetch_accepts_current_empty_split_schema(self):
        class CurrentEmptySplitApi(CoreApi):
            def fund_open_fund_info_em(self, symbol, indicator):
                if indicator == "拆分详情":
                    return pd.DataFrame()
                return super().fund_open_fund_info_em(symbol, indicator)

        bundle, _ = fetch_fund("050009", CurrentEmptySplitApi())

        self.assertFalse(bundle["nav"][0].empty)

    def test_failed_refresh_preserves_old_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            old = pd.DataFrame({"nav_date": ["2026-09-08"], "unit_nav": [1.9]})
            save_bundle(database, "050009", {"nav": (old, "2026-09-08", "fixture")})

            def broken_fetcher(code):
                raise RuntimeError("network down")

            result = refresh_fund(database, "050009", fetcher=broken_fetcher)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(load_frame(database, "050009", "nav").iloc[0]["unit_nav"], 1.9)

    def test_catalog_rejects_renamed_columns(self):
        class ChangedApi:
            def fund_name_em(self):
                return pd.DataFrame({"代码": ["050009"]})

        with self.assertRaisesRegex(ValueError, "基金代码"):
            fetch_catalog(ChangedApi())

    def test_repeated_refresh_merges_nav_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            calls = iter([1.0, 1.1])

            def fetcher(code):
                value = next(calls)
                nav = pd.DataFrame({"nav_date": ["2026-09-09"], "unit_nav": [value]})
                return {"nav": (nav, "2026-09-09", "fixture")}, []

            refresh_fund(database, "050009", fetcher=fetcher)
            refresh_fund(database, "050009", fetcher=fetcher)
            actual = load_frame(database, "050009", "nav")
            self.assertEqual(len(actual), 1)
            self.assertEqual(actual.iloc[0]["unit_nav"], 1.1)

    def test_fetch_rejects_non_finite_unit_nav(self):
        class Api:
            def fund_overview_em(self, symbol):
                return pd.DataFrame({"基金简称": ["测试基金"], "基金代码": [symbol]})

            def fund_open_fund_info_em(self, symbol, indicator):
                if indicator == "单位净值走势":
                    return pd.DataFrame({"净值日期": ["2026-09-09"], "单位净值": [float("inf")]})
                if indicator == "累计净值走势":
                    return pd.DataFrame({"净值日期": ["2026-09-09"], "累计净值": [1.0]})
                if indicator == "分红送配详情":
                    return pd.DataFrame(columns=["除息日", "每份分红"])
                return pd.DataFrame(columns=["拆分折算日", "拆分折算比例"])

        with self.assertRaisesRegex(ValueError, "单位净值"):
            fetch_fund("050009", Api())

    def test_non_finite_cumulative_nav_preserves_old_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            old = pd.DataFrame({"nav_date": ["2026-09-08"], "unit_nav": [1.9]})
            save_bundle(database, "050009", {"nav": (old, "2026-09-08", "fixture")})

            result = refresh_fund(database, "050009", fetcher=lambda code: fetch_fund(code, CoreApi(float("inf"))))

            self.assertEqual(result["status"], "failed")
            self.assertEqual(load_frame(database, "050009", "nav").to_dict("records"), old.to_dict("records"))

    def test_non_finite_optional_index_is_warning_and_preserves_old_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            old = pd.DataFrame({"trade_date": ["2026-09-08"], "close": [3500.0]})
            save_bundle(database, "050009", {"index": (old, "2026-09-08", "fixture")})

            result = refresh_fund(database, "050009", fetcher=lambda code: fetch_fund(code, CoreApi(index_close=float("inf"))))

            self.assertEqual(result["status"], "partial")
            self.assertEqual(load_frame(database, "050009", "index").to_dict("records"), old.to_dict("records"))

    def test_first_refresh_deduplicates_nav_and_index(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)

            def fetcher(code):
                nav = pd.DataFrame({"nav_date": ["2026-09-09", "2026-09-09"], "unit_nav": [1.0, 1.1]})
                index = pd.DataFrame({"trade_date": ["2026-09-09", "2026-09-09"], "close": [3500.0, 3510.0]})
                return {"nav": (nav, "2026-09-09", "fixture"), "index": (index, "2026-09-09", "fixture")}, []

            result = refresh_fund(database, "050009", fetcher=fetcher)

            self.assertEqual(result["status"], "success")
            self.assertEqual(load_frame(database, "050009", "nav").to_dict("records"), [{"nav_date": "2026-09-09", "unit_nav": 1.1}])
            self.assertEqual(load_frame(database, "050009", "index").to_dict("records"), [{"trade_date": "2026-09-09", "close": 3510.0}])

    def test_generic_fund_metadata_uses_only_live_source(self):
        bundle, warnings = fetch_fund("999999", CoreApi())

        self.assertTrue(warnings)
        self.assertEqual(bundle["metadata"][2], "AKShare/东方财富")

    def test_malformed_optional_fee_is_warning(self):
        bundle, warnings = fetch_fund("050009", CoreApi(fee="1not-a-fee"))

        self.assertNotIn("fees", bundle)
        self.assertTrue(any(warning.startswith("fees:") for warning in warnings))

    def test_malformed_index_date_is_warning_and_preserves_old_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fund.sqlite3"
            init_db(database)
            old = pd.DataFrame({"trade_date": ["2026-09-08"], "close": [3500.0]})
            save_bundle(database, "050009", {"index": (old, "2026-09-08", "fixture")})

            result = refresh_fund(database, "050009", fetcher=lambda code: fetch_fund(code, CoreApi(index_close=3510.0, index_date=None)))

            self.assertEqual(result["status"], "partial")
            self.assertFalse(load_frame(database, "050009", "nav").empty)
            self.assertEqual(load_frame(database, "050009", "index").to_dict("records"), old.to_dict("records"))

    def test_malformed_optional_identifiers_are_warnings(self):
        for api, dataset in (
            (OptionalApi(holding_code=" "), "holdings"),
            (OptionalApi(industry_category=None), "industry"),
            (OptionalApi(asset_category=""), "assets"),
            (OptionalApi(fee_type=None), "fees"),
            (OptionalApi(fee_condition=" "), "fees"),
        ):
            with self.subTest(dataset=dataset):
                bundle, warnings = fetch_fund("050009", api)
                self.assertNotIn(dataset, bundle)
                self.assertTrue(any(warning.startswith(f"{dataset}:") for warning in warnings))


class AppTests(unittest.TestCase):
    def test_non_empty_tables_do_not_render_streamlit_internals(self):
        from streamlit.testing.v1 import AppTest

        app = Path(__file__).parents[1] / "app.py"
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                database = Path("data/fund_research.sqlite3")
                init_db(database)
                metadata = pd.DataFrame([{"fund_name": "测试基金"}])
                nav = pd.DataFrame({
                    "nav_date": ["2026-09-08", "2026-09-09"],
                    "unit_nav": [1.0, 1.1],
                    "cumulative_nav": [1.0, 1.1],
                    "adjusted_nav": [1.0, 1.1],
                })
                holdings = pd.DataFrame([{
                    "rank": 1, "security_code": "000001", "security_name": "测试股票",
                    "weight": 0.1, "market_value_cny": 100.0, "report_date": "2026-06-30",
                }])
                fees = pd.DataFrame([{"fee_type": "管理费", "condition": "每年", "fee": "0.15%"}])
                save_bundle(database, "050009", {
                    "metadata": (metadata, "2026-09-09", "fixture"),
                    "nav": (nav, "2026-09-09", "fixture"),
                    "holdings": (holdings, "2026-06-30", "fixture"),
                    "fees": (fees, "2026-09-09", "fixture"),
                })

                page = AppTest.from_file(str(app), default_timeout=20).run()

                self.assertFalse(page.exception)
                self.assertEqual(page.get("help_info"), [])
            finally:
                os.chdir(original_directory)

    def test_missing_size_date_displays_no_data(self):
        from streamlit.testing.v1 import AppTest

        app = Path(__file__).parents[1] / "app.py"
        original_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                database = Path("data/fund_research.sqlite3")
                init_db(database)
                metadata = pd.DataFrame([{"fund_name": "测试基金", "asset_size_date": float("nan")}])
                nav = pd.DataFrame({
                    "nav_date": ["2026-09-08", "2026-09-09"],
                    "unit_nav": [1.0, 1.1],
                    "cumulative_nav": [1.0, 1.1],
                    "adjusted_nav": [1.0, 1.1],
                })
                save_bundle(database, "050009", {
                    "metadata": (metadata, "2026-09-09", "fixture"),
                    "nav": (nav, "2026-09-09", "fixture"),
                })

                page = AppTest.from_file(str(app), default_timeout=20).run()

                self.assertFalse(page.exception)
                self.assertIn("基金规模报告日：暂无数据", [caption.value for caption in page.caption])
            finally:
                os.chdir(original_directory)


class MetricTests(unittest.TestCase):
    def test_invalid_corporate_actions_are_rejected(self):
        nav = pd.DataFrame({"nav_date": ["2026-01-01"], "unit_nav": [1.0]})
        with self.assertRaises(ValueError):
            adjust_nav(nav, pd.DataFrame({"ex_date": ["2026-01-01"], "dividend_per_unit": [None]}))
        with self.assertRaises(ValueError):
            adjust_nav(nav, pd.DataFrame(), pd.DataFrame({"split_date": ["2026-01-01"], "split_ratio": [0]}))

    def test_invalid_adjusted_nav_is_rejected(self):
        nav = pd.DataFrame({"nav_date": ["2026-01-01", "2026-01-02"], "adjusted_nav": [1.0, None]})
        with self.assertRaises(ValueError):
            analyze(nav)

    def test_manager_return_requires_start_nav(self):
        nav = pd.DataFrame({"nav_date": ["2026-01-10", "2026-01-11"], "adjusted_nav": [1.0, 1.1]})
        self.assertIsNone(analyze(nav, manager_start_date="2026-01-01")["manager_return"])

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
