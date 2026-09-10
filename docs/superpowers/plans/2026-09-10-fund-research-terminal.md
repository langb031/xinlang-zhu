# Personal Fund Research Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small local Streamlit application that researches Chinese public funds and gives 博时新兴成长混合（050009）a complete performance, risk, holdings, manager, and fee page.

**Architecture:** Keep three Python files: `fund_data.py` fetches, validates, and transactionally caches DataFrames in SQLite; `metrics.py` performs pure calculations; `app.py` renders the UI. Cache secondary disclosures as pandas JSON in one SQLite table instead of creating a normalized database intended for hypothetical future queries.

**Tech Stack:** Python 3.11, Streamlit, AKShare, pandas, SQLite through `sqlite3`, and standard-library `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-10-fund-research-terminal-design.md`

## Global Constraints

- Run locally for one user with Python 3.11.
- Keep `050009` as the acceptance case while allowing another six-digit open-fund code.
- Only explicit button clicks may access the network.
- Preserve old cached data when an upstream call is empty, malformed, or unavailable.
- Retain source date, disclosure period, source name, and local fetch time.
- Use `暂无数据` or `样本不足` for missing results; never replace missing data with zero.
- Label 沪深300 as `市场参照`, not as the fund's official composite benchmark.
- Display `数据与统计仅供个人研究，过往表现不代表未来收益。`
- Do not add accounts, cloud deployment, a separate API, an ORM, scheduled jobs, real-time estimates, trading, portfolios, news, AI predictions, ranking, or backtesting.

## Ponytail Decisions

- One generic `cache` table replaces nine normalized tables. Add normalized tables only when cross-fund SQL analysis becomes a real requirement.
- Streamlit's native charts replace Plotly. Add Plotly only when native charts cannot express a requested chart.
- Plain functions replace repository, source-adapter, service, protocol, and custom-exception layers. Split `fund_data.py` only after a second data provider is actually added.
- `unittest` replaces pytest and Streamlit UI-test scaffolding. Test calculations and data-loss prevention; visually smoke-test the thin UI once.
- The first version stores fee conditions as disclosed text instead of parsing every Chinese fee sentence into numeric ranges. Parse them only when fee calculation becomes a requested feature.
- AKShare returns full history for the chosen NAV endpoint, so refreshes merge by date locally instead of pretending a server-side incremental request exists.

These cuts change internal implementation only. The approved 050009 page, calculations, dates, update behavior, and failure protection remain in scope.

## Files

```text
wind/
├── .gitignore
├── requirements.txt
├── README.md
├── app.py
├── fund_data.py
├── metrics.py
├── data/
│   └── fund_metadata.json
└── tests/
    └── test_core.py
```

---

### Task 1: Minimal Project and Transactional Cache

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `fund_data.py`
- Create: `tests/__init__.py`
- Create: `tests/test_core.py`

**Interfaces:**
- Produces: `init_db(path: Path)`, `save_bundle(path: Path, key: str, bundle: Bundle)`, `load_frame(path: Path, key: str, dataset: str) -> pd.DataFrame`, and `latest_update(path: Path, key: str, successful_only: bool = False) -> dict | None`.
- `Bundle` is `dict[str, tuple[pd.DataFrame, str, str]]`; each value is `(frame, as_of, source)`.

- [ ] **Step 1: Add only the required dependencies and ignores**

```text
# requirements.txt
akshare>=1.18.94
pandas>=2.2
streamlit>=1.49
```

```gitignore
# .gitignore
.venv/
__pycache__/
*.py[cod]
.superpowers/
data/fund_research.sqlite3
*.log
```

- [ ] **Step 2: Create the local environment**

Run:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Expected: all commands exit 0.

- [ ] **Step 3: Write the failing cache round-trip test**

```python
# tests/test_core.py
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from fund_data import init_db, load_frame, save_bundle


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
```

- [ ] **Step 4: Run the test and confirm it fails**

Run: `.venv/bin/python -m unittest tests.test_core -v`

Expected: FAIL importing the missing functions from `fund_data.py`.

- [ ] **Step 5: Implement the two-table cache**

```python
# fund_data.py
from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path
import sqlite3
from typing import TypeAlias

import pandas as pd


Bundle: TypeAlias = dict[str, tuple[pd.DataFrame, str, str]]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS cache (
          cache_key TEXT NOT NULL,
          dataset TEXT NOT NULL,
          as_of TEXT NOT NULL,
          source TEXT NOT NULL,
          fetched_at TEXT NOT NULL,
          payload TEXT NOT NULL,
          PRIMARY KEY (cache_key, dataset)
        );
        CREATE TABLE IF NOT EXISTS updates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          cache_key TEXT NOT NULL,
          finished_at TEXT NOT NULL,
          status TEXT NOT NULL,
          message TEXT NOT NULL
        );
        """)


def save_bundle(path: Path, key: str, bundle: Bundle) -> None:
    if any(frame.empty for frame, _, _ in bundle.values()):
        raise ValueError("不能用空数据覆盖缓存")
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = [
        {
            "cache_key": key,
            "dataset": dataset,
            "as_of": as_of,
            "source": source,
            "fetched_at": fetched_at,
            "payload": frame.to_json(orient="table", date_format="iso", force_ascii=False),
        }
        for dataset, (frame, as_of, source) in bundle.items()
    ]
    with connect(path) as connection:
        connection.executemany("""
        INSERT INTO cache(cache_key, dataset, as_of, source, fetched_at, payload)
        VALUES(:cache_key, :dataset, :as_of, :source, :fetched_at, :payload)
        ON CONFLICT(cache_key, dataset) DO UPDATE SET
          as_of=excluded.as_of,
          source=excluded.source,
          fetched_at=excluded.fetched_at,
          payload=excluded.payload
        """, rows)


def load_frame(path: Path, key: str, dataset: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    with connect(path) as connection:
        row = connection.execute(
            "SELECT payload FROM cache WHERE cache_key=? AND dataset=?",
            (key, dataset),
        ).fetchone()
    return pd.DataFrame() if row is None else pd.read_json(StringIO(row["payload"]), orient="table")


def record_update(path: Path, key: str, status: str, message: str) -> None:
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO updates(cache_key, finished_at, status, message) VALUES(?,?,?,?)",
            (key, datetime.now().astimezone().isoformat(timespec="seconds"), status, message),
        )


def latest_update(path: Path, key: str, successful_only: bool = False) -> dict | None:
    if not path.exists():
        return None
    with connect(path) as connection:
        sql = "SELECT * FROM updates WHERE cache_key=?"
        parameters = [key]
        if successful_only:
            sql += " AND status IN ('success','partial')"
        row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", parameters).fetchone()
    return None if row is None else dict(row)
```

- [ ] **Step 6: Run the test and commit**

Run: `.venv/bin/python -m unittest tests.test_core -v`

Expected: PASS.

```bash
git add .gitignore requirements.txt fund_data.py tests/__init__.py tests/test_core.py
git commit -m "chore: add minimal local fund cache"
```

### Task 2: Fund Calculations

**Files:**
- Create: `metrics.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces: `adjust_nav(nav, dividends, splits=None)`, `analyze(nav, index=None, manager_start_date=None) -> dict`, and `performance_series(nav, index) -> pd.DataFrame`.

- [ ] **Step 1: Add the failing calculation tests**

```python
# append to tests/test_core.py
from metrics import adjust_nav, analyze


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
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/python -m unittest tests.test_core -v`

Expected: FAIL because `metrics.py` does not exist.

- [ ] **Step 3: Implement adjusted NAV and all approved formulas**

```python
# metrics.py
from __future__ import annotations

from math import sqrt
import pandas as pd


OFFSETS = {
    "1月": pd.DateOffset(months=1),
    "3月": pd.DateOffset(months=3),
    "6月": pd.DateOffset(months=6),
    "1年": pd.DateOffset(years=1),
    "3年": pd.DateOffset(years=3),
    "5年": pd.DateOffset(years=5),
}


def adjust_nav(nav: pd.DataFrame, dividends: pd.DataFrame, splits: pd.DataFrame | None = None) -> pd.DataFrame:
    result = nav.copy()
    result["nav_date"] = pd.to_datetime(result["nav_date"])
    result["unit_nav"] = pd.to_numeric(result["unit_nav"], errors="raise")
    if result["unit_nav"].isna().any() or (result["unit_nav"] <= 0).any():
        raise ValueError("单位净值必须为正数且不能缺失")
    result = result.sort_values("nav_date").drop_duplicates("nav_date", keep="last")
    dividend_map = {}
    if not dividends.empty:
        prepared = dividends.assign(ex_date=pd.to_datetime(dividends["ex_date"]))
        dividend_map = prepared.groupby("ex_date")["dividend_per_unit"].sum().to_dict()
    split_map = {}
    if splits is not None and not splits.empty:
        prepared = splits.assign(split_date=pd.to_datetime(splits["split_date"]))
        split_map = prepared.groupby("split_date")["split_ratio"].prod().to_dict()
    result["dividend_per_unit"] = result["nav_date"].map(dividend_map).fillna(0.0)
    result["split_ratio"] = result["nav_date"].map(split_map).fillna(1.0)
    gross = (result["unit_nav"] * result["split_ratio"] + result["dividend_per_unit"]) / result["unit_nav"].shift(1)
    gross.iloc[0] = 1.0
    result["adjusted_nav"] = result.iloc[0]["unit_nav"] * gross.cumprod()
    result["nav_date"] = result["nav_date"].dt.strftime("%Y-%m-%d")
    return result.reset_index(drop=True)


def _return(series: pd.Series) -> float | None:
    return None if len(series) < 2 else float(series.iloc[-1] / series.iloc[0] - 1)


def analyze(nav: pd.DataFrame, index: pd.DataFrame | None = None, manager_start_date: str | None = None) -> dict:
    ordered = nav.assign(nav_date=pd.to_datetime(nav["nav_date"])).sort_values("nav_date").reset_index(drop=True)
    values = ordered["adjusted_nav"].astype(float)
    daily = values.pct_change().dropna()
    daily_std = daily.std(ddof=1)
    running_high = values.cummax()
    drawdown = values / running_high - 1
    trough = int(drawdown.idxmin())
    peak = int(values.loc[:trough].idxmax())
    result = {
        "total_return": _return(values),
        "annualized_return": None if len(daily) < 250 else float((values.iloc[-1] / values.iloc[0]) ** (250 / len(daily)) - 1),
        "volatility": None if len(daily) < 30 else float(daily_std * sqrt(250)),
        "sharpe": None if len(daily) < 30 or daily_std <= 1e-12 else float(daily.mean() / daily_std * sqrt(250)),
        "max_drawdown": float(drawdown.loc[trough]),
        "drawdown_peak": ordered.loc[peak, "nav_date"].strftime("%Y-%m-%d"),
        "drawdown_trough": ordered.loc[trough, "nav_date"].strftime("%Y-%m-%d"),
        "drawdown_series": pd.DataFrame({"date": ordered["nav_date"], "drawdown": drawdown}),
    }
    as_of = ordered.iloc[-1]["nav_date"]
    result["period_returns"] = {
        name: (None if ordered[ordered["nav_date"] <= as_of - offset].empty else float(values.iloc[-1] / values.loc[ordered["nav_date"] <= as_of - offset].iloc[-1] - 1))
        for name, offset in OFFSETS.items()
    }
    result["period_returns"]["成立以来"] = result["total_return"]
    calendar = []
    for year, group in ordered.groupby(ordered["nav_date"].dt.year):
        label = f"{year}（年初至今）" if year == as_of.year else str(year)
        calendar.append({"year": label, "return": _return(group["adjusted_nav"])})
    result["calendar_returns"] = pd.DataFrame(calendar)
    result["manager_return"] = None
    if manager_start_date and pd.to_datetime(manager_start_date) <= as_of:
        earlier = ordered[ordered["nav_date"] <= pd.to_datetime(manager_start_date)]
        starting_value = ordered.iloc[0]["adjusted_nav"] if earlier.empty else earlier.iloc[-1]["adjusted_nav"]
        result["manager_return"] = float(values.iloc[-1] / starting_value - 1)
    comparison = performance_series(nav, index) if index is not None and not index.empty else pd.DataFrame()
    result["comparison"] = comparison
    result["excess_return"] = None if comparison.empty else float((comparison.iloc[-1]["fund"] - 1) - (comparison.iloc[-1]["csi300"] - 1))
    return result


def performance_series(nav: pd.DataFrame, index: pd.DataFrame) -> pd.DataFrame:
    fund = nav[["nav_date", "adjusted_nav"]].rename(columns={"nav_date": "date"})
    market = index[["trade_date", "close"]].rename(columns={"trade_date": "date"})
    merged = fund.merge(market, on="date", how="inner").sort_values("date")
    if merged.empty:
        return merged
    merged["fund"] = merged["adjusted_nav"] / merged.iloc[0]["adjusted_nav"]
    merged["csi300"] = merged["close"] / merged.iloc[0]["close"]
    return merged[["date", "fund", "csi300"]]
```

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m unittest tests.test_core -v`

Expected: PASS.

```bash
git add metrics.py tests/test_core.py
git commit -m "feat: calculate fund performance and risk"
```

### Task 3: Fetch, Validate, and Refresh Public Fund Data

**Files:**
- Create: `data/fund_metadata.json`
- Modify: `fund_data.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Produces: `fetch_catalog(api=akshare)`, `fetch_fund(code, api=akshare) -> tuple[Bundle, list[str]]`, `refresh_catalog(path, api=akshare)`, `refresh_fund(path, code, fetcher=fetch_fund) -> dict`, and a CLI `python fund_data.py 050009`.

- [ ] **Step 1: Add the verified official facts for 050009**

```json
{
    "050009": {
    "fund_name": "博时新兴成长混合",
    "fund_type": "偏股混合型",
    "inception_date": "2007-07-06",
    "risk_level": "中高风险",
    "manager_start_dates": {"曾鹏": "2013-01-18"},
    "official_benchmark": "沪深300指数收益率×80%＋中国债券总指数收益率×20%",
    "investment_objective": "基于中国经济正处于长期稳定增长周期，本基金通过深入研究并积极投资于全市场各类行业中的新兴高速成长企业，力争为基金份额持有人获得超越业绩比较基准的投资回报。",
    "investment_scope": "股票（含存托凭证）60%-95%；债券0%-35%；现金及到期日在一年以内的政府债券合计不低于基金资产净值的5%",
    "source_url": "https://www.bosera.com/common/downloadFile.do?fileId=11F8C3AC19E096C5A00DA3C39887ADE1",
    "source_date": "2023-08-25"
  }
}
```

- [ ] **Step 2: Write the failing data-loss and validation tests**

```python
# append to tests/test_core.py
from fund_data import fetch_catalog, refresh_fund


class RefreshTests(unittest.TestCase):
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
```

- [ ] **Step 3: Run tests and confirm missing-function failures**

Run: `.venv/bin/python -m unittest tests.test_core -v`

Expected: FAIL importing `fetch_catalog` and `refresh_fund`.

- [ ] **Step 4: Implement small validation and parsing helpers**

```python
# append to fund_data.py
import argparse
import json
import re

import akshare

from metrics import adjust_nav


def need(frame: pd.DataFrame, columns: set[str], name: str, allow_empty: bool = False) -> pd.DataFrame:
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError(f"{name} 缺少字段: {', '.join(sorted(missing))}")
    if frame.empty and not allow_empty:
        raise ValueError(f"{name} 返回空数据")
    return frame.copy()


def percent(value) -> float | None:
    match = re.search(r"([0-9.]+)%", str(value))
    return None if match is None else float(match.group(1)) / 100


def amount_and_date(value) -> tuple[float | None, str | None]:
    amount = re.search(r"([0-9.]+)亿", str(value))
    date = re.search(r"(20\d{2}-\d{2}-\d{2})", str(value))
    return (None if amount is None else float(amount.group(1)) * 100_000_000, None if date is None else date.group(1))


def quarter_end(value) -> str:
    match = re.search(r"(20\d{2})年([1-4])季度", str(value))
    if match is None:
        raise ValueError(f"无法解析报告期: {value}")
    return f"{match.group(1)}-" + {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}[match.group(2)]


def cash_dividend(value) -> float:
    match = re.search(r"每份派现金([0-9.]+)元", str(value))
    if match is None:
        raise ValueError(f"无法解析每份分红: {value}")
    return float(match.group(1))


def split_ratio(value) -> float:
    match = re.search(r"1:([0-9.]+)", str(value))
    if match is None:
        raise ValueError(f"无法解析拆分比例: {value}")
    return float(match.group(1))


def fetch_catalog(api=akshare) -> pd.DataFrame:
    frame = need(api.fund_name_em(), {"基金代码", "基金简称", "基金类型"}, "基金目录")
    return frame.rename(columns={"基金代码": "fund_code", "基金简称": "fund_name", "基金类型": "fund_type"})[["fund_code", "fund_name", "fund_type"]]
```

- [ ] **Step 5: Implement core metadata and NAV fetching**

```python
def _official(code: str) -> dict:
    path = Path("data/fund_metadata.json")
    return json.loads(path.read_text(encoding="utf-8")).get(code, {})


def _core(code: str, api) -> Bundle:
    overview = need(api.fund_overview_em(symbol=code), {"基金简称", "基金代码"}, "基金概况").iloc[0]
    size, size_date = amount_and_date(overview.get("资产规模"))
    official = _official(code)
    manager_start_dates = official.pop("manager_start_dates", {})
    live_manager = overview.get("基金经理人")
    metadata = {
        "fund_code": code,
        "fund_name": str(overview["基金简称"]),
        "fund_type": overview.get("基金类型"),
        "management_company": overview.get("基金管理人"),
        "manager": live_manager,
        "manager_start_date": manager_start_dates.get(str(live_manager)),
        "custodian": overview.get("基金托管人"),
        "asset_size_cny": size,
        "asset_size_date": size_date,
        "management_fee": percent(overview.get("管理费率")),
        "custodian_fee": percent(overview.get("托管费率")),
        "official_benchmark": overview.get("业绩比较基准"),
        **official,
    }
    unit = need(api.fund_open_fund_info_em(symbol=code, indicator="单位净值走势"), {"净值日期", "单位净值"}, "单位净值")
    cumulative = need(api.fund_open_fund_info_em(symbol=code, indicator="累计净值走势"), {"净值日期", "累计净值"}, "累计净值")
    raw_dividends = need(api.fund_open_fund_info_em(symbol=code, indicator="分红送配详情"), {"除息日", "每份分红"}, "分红", allow_empty=True)
    raw_splits = need(api.fund_open_fund_info_em(symbol=code, indicator="拆分详情"), {"拆分折算日", "拆分折算比例"}, "拆分", allow_empty=True)
    dividends = pd.DataFrame(columns=["ex_date", "dividend_per_unit"])
    if not raw_dividends.empty:
        dividends = pd.DataFrame({
            "ex_date": raw_dividends["除息日"],
            "dividend_per_unit": raw_dividends["每份分红"].map(cash_dividend),
        })
    splits = pd.DataFrame(columns=["split_date", "split_ratio"])
    if not raw_splits.empty:
        splits = pd.DataFrame({
            "split_date": raw_splits["拆分折算日"],
            "split_ratio": raw_splits["拆分折算比例"].map(split_ratio),
        })
    nav = unit.rename(columns={"净值日期": "nav_date", "单位净值": "unit_nav"})[["nav_date", "unit_nav"]]
    nav = nav.merge(cumulative.rename(columns={"净值日期": "nav_date", "累计净值": "cumulative_nav"}), on="nav_date", how="left")
    nav = adjust_nav(nav, dividends, splits)
    as_of = nav["nav_date"].max()
    return {
        "metadata": (pd.DataFrame([metadata]), size_date or as_of, "AKShare/东方财富；博时基金官方资料"),
        "nav": (nav, as_of, "AKShare/东方财富"),
    }
```

- [ ] **Step 6: Implement optional market, holdings, allocation, and fee fetching**

```python
def fetch_fund(code: str, api=akshare) -> tuple[Bundle, list[str]]:
    bundle = _core(code, api)
    warnings: list[str] = []

    def add(name: str, fetch) -> None:
        try:
            bundle[name] = fetch()
        except Exception as error:
            warnings.append(f"{name}: {error}")

    def market():
        frame = need(api.stock_zh_index_daily_em(symbol="sh000300", start_date="19900101", end_date="20500101"), {"date", "close"}, "沪深300")
        frame = frame.rename(columns={"date": "trade_date"})[["trade_date", "close"]]
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
        return frame, frame["trade_date"].max(), "AKShare/东方财富"

    def holdings():
        frame = need(api.fund_portfolio_hold_em(symbol=code, date=""), {"序号", "股票代码", "股票名称", "占净值比例", "持仓市值", "季度"}, "基金持仓")
        result = pd.DataFrame({
            "rank": frame["序号"], "security_code": frame["股票代码"].astype(str).str.zfill(6),
            "security_name": frame["股票名称"], "weight": pd.to_numeric(frame["占净值比例"]) / 100,
            "market_value_cny": pd.to_numeric(frame["持仓市值"]) * 10_000,
            "report_date": frame["季度"].map(quarter_end),
        })
        latest = result["report_date"].max()
        return result[result["report_date"] == latest], latest, "AKShare/东方财富"

    add("index", market)
    add("holdings", holdings)
    report_date = bundle.get("holdings", (pd.DataFrame(), "", ""))[1]

    if report_date:
        def industry():
            frame = need(api.fund_portfolio_industry_allocation_em(symbol=code, date=report_date[:4]), {"行业类别", "占净值比例", "截止时间"}, "行业配置")
            result = pd.DataFrame({"category": frame["行业类别"], "weight": pd.to_numeric(frame["占净值比例"]) / 100, "report_date": pd.to_datetime(frame["截止时间"]).dt.strftime("%Y-%m-%d")})
            latest = result["report_date"].max()
            return result[result["report_date"] == latest], latest, "AKShare/东方财富"

        def assets():
            frame = need(api.fund_individual_detail_hold_xq(symbol=code, date=report_date.replace("-", "")), {"资产类型", "仓位占比"}, "资产配置")
            result = pd.DataFrame({"category": frame["资产类型"], "weight": pd.to_numeric(frame["仓位占比"]) / 100})
            result["report_date"] = report_date
            return result, report_date, "AKShare/雪球基金"

        add("industry", industry)
        add("assets", assets)

    def fees():
        frame = need(api.fund_individual_detail_info_xq(symbol=code), {"费用类型", "条件或名称", "费用"}, "基金费率")
        result = frame.rename(columns={"费用类型": "fee_type", "条件或名称": "condition", "费用": "fee"})[["fee_type", "condition", "fee"]]
        return result, bundle["metadata"][1], "AKShare/雪球基金"

    add("fees", fees)
    return bundle, warnings
```

The broad `except Exception` is deliberate only around optional datasets: it preserves the usable core page when an upstream secondary endpoint changes. Core metadata and NAV exceptions still abort the refresh.
Technical error details stay in the local `updates` table; the page shows a short retry message instead of a traceback.

- [ ] **Step 7: Implement refresh functions and the one-file CLI**

```python
def refresh_catalog(path: Path, api=akshare) -> int:
    frame = fetch_catalog(api)
    save_bundle(path, "__all__", {"catalog": (frame, datetime.now().date().isoformat(), "AKShare")})
    return len(frame)


def refresh_fund(path: Path, code: str, fetcher=fetch_fund) -> dict:
    if re.fullmatch(r"\d{6}", code) is None:
        raise ValueError("基金代码必须是 6 位数字")
    try:
        bundle, warnings = fetcher(code)
        for dataset, date_column in (("nav", "nav_date"), ("index", "trade_date")):
            if dataset in bundle:
                fresh, as_of, source = bundle[dataset]
                old = load_frame(path, code, dataset)
                if not old.empty:
                    fresh = pd.concat([old, fresh], ignore_index=True).drop_duplicates(date_column, keep="last").sort_values(date_column)
                bundle[dataset] = (fresh.reset_index(drop=True), as_of, source)
        save_bundle(path, code, bundle)
        status = "partial" if warnings else "success"
        message = "；".join(warnings) if warnings else "更新完成"
    except Exception as error:
        status, message = "failed", str(error)
    record_update(path, code, status, message)
    user_message = {
        "success": "更新完成",
        "partial": "核心数据已更新，部分持仓或费率数据暂不可用。",
        "failed": "更新失败，请稍后重试；本地旧数据仍然保留。",
    }[status]
    return {"status": status, "message": user_message}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("code", nargs="?", default="050009")
    args = parser.parse_args()
    database = Path("data/fund_research.sqlite3")
    init_db(database)
    result = refresh_fund(database, args.code)
    print(f"{args.code}: {result['status']} - {result['message']}")
    raise SystemExit(0 if result["status"] in {"success", "partial"} else 1)
```

- [ ] **Step 8: Run tests and commit**

Run: `.venv/bin/python -m unittest tests.test_core -v`

Expected: PASS without network access.

```bash
git add data/fund_metadata.json fund_data.py tests/test_core.py
git commit -m "feat: fetch and safely cache public fund data"
```

### Task 4: One-Page Streamlit UI

**Files:**
- Create: `app.py`

**Interfaces:**
- Consumes: cached `metadata`, `nav`, `index`, `holdings`, `industry`, `assets`, and `fees` DataFrames.
- Produces: search/update controls, four summary cards, five tabs, source dates, last-update status, and disclaimer.

- [ ] **Step 1: Implement startup, search, and explicit network buttons**

```python
# app.py
from pathlib import Path
import re

import pandas as pd
import streamlit as st

from fund_data import init_db, latest_update, load_frame, refresh_catalog, refresh_fund
from metrics import analyze


DB = Path("data/fund_research.sqlite3")
init_db(DB)
st.set_page_config(page_title="基金研究终端", layout="wide")
st.title("基金研究终端")

query = st.text_input("基金名称或六位代码", "050009").strip()
catalog = load_frame(DB, "__all__", "catalog")
code = query if re.fullmatch(r"\d{6}", query) else None
if code is None and not catalog.empty and query:
    matches = catalog[catalog["fund_name"].str.contains(query, case=False, na=False)]
    if not matches.empty:
        choices = {f"{row.fund_code} · {row.fund_name}": row.fund_code for row in matches.itertuples()}
        code = choices[st.selectbox("匹配结果", list(choices))]

left, right = st.columns(2)
if left.button("更新当前基金", disabled=code is None):
    with st.spinner("正在更新公开数据……"):
        result = refresh_fund(DB, code)
    (st.success if result["status"] == "success" else st.warning if result["status"] == "partial" else st.error)(result["message"])
if right.button("刷新基金目录"):
    with st.spinner("正在刷新基金目录……"):
        count = refresh_catalog(DB)
    st.success(f"基金目录已更新：{count} 只")
```

- [ ] **Step 2: Implement metric formatting and empty states**

```python
def pct(value) -> str:
    return "样本不足" if value is None or pd.isna(value) else f"{value:.1%}"


def money(value) -> str:
    return "暂无数据" if value is None or pd.isna(value) else f"{value / 100_000_000:.2f}亿元"


def fee_pct(value) -> str:
    return "暂无数据" if value is None or pd.isna(value) else f"{value:.2%}"


if code is None:
    st.info("请输入六位基金代码；按名称搜索前请先刷新基金目录。")
    st.stop()

update = latest_update(DB, code)
if update and update["status"] == "failed":
    st.error("上次更新失败，请稍后重试；本地旧数据仍然保留。")
elif update and update["status"] == "partial":
    st.warning("上次仅完成核心数据更新，部分持仓或费率数据暂不可用。")

nav = load_frame(DB, code, "nav")
if nav.empty:
    st.info(f"尚未下载 {code}，请点击“更新当前基金”。")
    st.caption("数据与统计仅供个人研究，过往表现不代表未来收益。")
    st.stop()
```

- [ ] **Step 3: Implement the approved research page with native charts**

```python
metadata_frame = load_frame(DB, code, "metadata")
metadata = metadata_frame.iloc[0].to_dict()
index = load_frame(DB, code, "index")
holdings = load_frame(DB, code, "holdings")
industry = load_frame(DB, code, "industry")
assets = load_frame(DB, code, "assets")
fees = load_frame(DB, code, "fees")
result = analyze(nav, index, metadata.get("manager_start_date"))

st.header(f"{metadata.get('fund_name', code)}（{code}）")
st.write(" · ".join(filter(None, [metadata.get("fund_type"), metadata.get("risk_level"), metadata.get("management_company"), metadata.get("manager")])))
st.caption(f"净值日期：{nav['nav_date'].max()} · 最后成功更新：{(latest_update(DB, code, successful_only=True) or {}).get('finished_at', '暂无数据')}")
cards = st.columns(4)
cards[0].metric("单位净值", f"{nav.iloc[-1]['unit_nav']:.4f}")
cards[1].metric("近一年收益", pct(result["period_returns"].get("1年")))
cards[2].metric("最大回撤", pct(result["max_drawdown"]))
cards[3].metric("最近基金规模", money(metadata.get("asset_size_cny")))
st.caption(f"基金规模报告日：{metadata.get('asset_size_date') or '暂无数据'}")

overview, risk, portfolio, manager, documents = st.tabs(["总览", "业绩与风险", "持仓分析", "基金经理", "资料与费率"])
with overview:
    comparison = result["comparison"]
    if comparison.empty:
        st.info("沪深300参照暂无数据")
    else:
        st.line_chart(comparison.set_index("date")[["fund", "csi300"]].rename(columns={"fund": "基金复权净值", "csi300": "沪深300（市场参照）"}))
        st.caption(f"共同可用数据截至：{comparison['date'].max()}")
    annual = result["calendar_returns"].dropna(subset=["return"])
    st.bar_chart(annual.set_index("year")["return"])
    st.caption(f"年度收益使用净值数据截至：{nav['nav_date'].max()}；当前年度为年初至今")
    st.line_chart(nav.set_index("nav_date")[["unit_nav", "cumulative_nav", "adjusted_nav"]].rename(columns={"unit_nav": "单位净值", "cumulative_nav": "累计净值", "adjusted_nav": "复权净值"}))
    st.caption(f"净值来源：AKShare/东方财富；截至：{nav['nav_date'].max()}")
    left, right = st.columns(2)
    with left:
        st.subheader("最近资产配置")
        if assets.empty:
            st.write("暂无数据")
        else:
            st.bar_chart(assets.set_index("category")["weight"])
            st.caption(f"来源：AKShare/雪球基金；报告期：{assets['report_date'].max()}")
    with right:
        st.subheader("前十大持仓摘要")
        if holdings.empty:
            st.write("暂无数据")
        else:
            st.dataframe(holdings.head(10), hide_index=True)
            st.caption(f"来源：AKShare/东方财富；报告期：{holdings['report_date'].max()}")
with risk:
    periods = pd.DataFrame([{"区间": name, "收益": pct(value)} for name, value in result["period_returns"].items()])
    st.dataframe(periods, hide_index=True)
    st.write({
        "年化收益": pct(result["annualized_return"]),
        "年化波动": pct(result["volatility"]),
        "夏普比率（无风险利率0%）": "样本不足" if result["sharpe"] is None else f"{result['sharpe']:.2f}",
        "相对沪深300同期超额收益": pct(result["excess_return"]),
        "最大回撤区间": f"{result['drawdown_peak']} 至 {result['drawdown_trough']}",
    })
    st.line_chart(result["drawdown_series"].set_index("date"))
    st.caption(f"收益与风险数据截至：{nav['nav_date'].max()}")
with portfolio:
    report_date = "暂无数据" if holdings.empty else holdings["report_date"].max()
    st.caption(f"最近持仓报告期：{report_date}")
    st.metric("前十大持仓集中度", "暂无数据" if holdings.empty else pct(holdings.head(10)["weight"].sum()))
    for title, frame, source in (("资产配置", assets, "AKShare/雪球基金"), ("行业配置", industry, "AKShare/东方财富")):
        st.subheader(title)
        if frame.empty:
            st.write("暂无数据")
        else:
            st.bar_chart(frame.set_index("category")["weight"])
            st.caption(f"来源：{source}；报告期：{frame['report_date'].max()}")
    st.subheader("前十大持仓")
    st.dataframe(holdings.head(10), hide_index=True) if not holdings.empty else st.write("暂无数据")
with manager:
    st.write(metadata.get("manager") or "暂无数据")
    st.caption(f"任职起始日：{metadata.get('manager_start_date') or '暂无数据'}；来源：AKShare/东方财富及博时基金官方资料")
    st.metric("任职期收益", pct(result["manager_return"]))
with documents:
    st.write(f"投资目标：{metadata.get('investment_objective') or '暂无数据'}")
    st.write(f"官方业绩比较基准：{metadata.get('official_benchmark') or '暂无数据'}")
    st.write(f"投资范围：{metadata.get('investment_scope') or '暂无数据'}")
    st.write(f"管理费：{fee_pct(metadata.get('management_fee'))}；托管费：{fee_pct(metadata.get('custodian_fee'))}")
    st.caption(f"官方资料日期：{metadata.get('source_date') or '暂无数据'}；交易费率来源：AKShare/雪球基金，来自最近手动更新")
    st.dataframe(fees, hide_index=True) if not fees.empty else st.write("暂无数据")
    if metadata.get("source_url"):
        st.link_button("查看官方产品资料", metadata["source_url"])

st.divider()
st.caption("数据与统计仅供个人研究，过往表现不代表未来收益。")
```

- [ ] **Step 4: Run the smallest UI checks and commit**

Run:

```bash
.venv/bin/python -m py_compile app.py fund_data.py metrics.py
.venv/bin/python -m unittest tests.test_core -v
```

Expected: compilation succeeds and all tests PASS.

```bash
git add app.py
git commit -m "feat: add local fund research page"
```

### Task 5: Beginner Handoff and Live 050009 Acceptance

**Files:**
- Create: `README.md`
- Modify: `fund_data.py` only if the live API exposes a verified current field-name change.
- Modify: `app.py` only if visual acceptance exposes a blocking display defect.

**Interfaces:**
- Produces: beginner setup/start/update instructions and verified local 050009 data.

- [ ] **Step 1: Write the beginner README**

````markdown
# 个人公募基金研究终端

## 这是什么
这是一个在自己电脑上运行的公募基金研究页，默认研究博时新兴成长混合（050009）。

## 数据与风险说明
数据通过 AKShare 接入公开网页，接口可能变化；持仓和规模来自定期披露，不是实时数据。本工具不构成投资建议。

## 第一次安装
```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 启动
```bash
.venv/bin/streamlit run app.py
```

## 第一次研究 050009
保留搜索框中的 `050009`，点击“更新当前基金”。

## 更新数据
再次点击“更新当前基金”。失败时页面继续使用上一次成功缓存。

## 常见问题
- `样本不足`：现有数据不足以可靠计算该指标。
- `暂无数据`：公开接口没有返回该项。
- `partial`：核心净值可用，但某项定期披露暂时不可用。

## 运行测试
```bash
.venv/bin/python -m unittest tests.test_core -v
```
````

- [ ] **Step 2: Run all deterministic checks**

Run:

```bash
.venv/bin/python -m py_compile app.py fund_data.py metrics.py
.venv/bin/python -m unittest tests.test_core -v
```

Expected: both commands exit 0.

- [ ] **Step 3: Fetch and verify the live acceptance fund**

Run:

```bash
.venv/bin/python fund_data.py 050009
```

Expected: exit 0 with `050009: success` or `050009: partial`, and `data/fund_research.sqlite3` contains non-empty `metadata` and `nav` cache rows. A temporary upstream failure is not acceptance; retain the error and retry after the endpoint recovers.

- [ ] **Step 4: Open the app and visually verify the approved page**

Run: `.venv/bin/streamlit run app.py`

Verify:

- `050009` opens without a Python exception.
- Four summary cards and five tabs appear.
- Net value date, holding report period, and last update time appear.
- 沪深300 is labeled `市场参照`.
- Missing secondary data says `暂无数据` rather than `0`.
- The official benchmark, official link, and disclaimer appear.

- [ ] **Step 5: Verify ignored runtime files and commit**

Run:

```bash
git status --short
git diff --check
```

Expected: `.venv/`, `.superpowers/`, Python caches, and `data/fund_research.sqlite3` are absent from Git status; `git diff --check` is silent.

```bash
git add README.md
git commit -m "docs: explain local fund research workflow"
```

## Completion Gate

- [ ] Standard-library tests pass.
- [ ] A live 050009 refresh produces usable metadata and NAV.
- [ ] Repeating the refresh replaces cached datasets transactionally without duplicate rows.
- [ ] A simulated failed refresh leaves the previous NAV intact.
- [ ] The local page shows the approved five-tab research experience and no investment recommendation.
