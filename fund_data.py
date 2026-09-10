from __future__ import annotations

import argparse
from datetime import datetime
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import re
import sqlite3
from typing import TypeAlias

import akshare
import pandas as pd

from metrics import adjust_nav


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
          cache_key TEXT NOT NULL, dataset TEXT NOT NULL, as_of TEXT NOT NULL,
          source TEXT NOT NULL, fetched_at TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (cache_key, dataset)
        );
        CREATE TABLE IF NOT EXISTS updates (
          id INTEGER PRIMARY KEY AUTOINCREMENT, cache_key TEXT NOT NULL,
          finished_at TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL
        );
        """)


def save_bundle(path: Path, key: str, bundle: Bundle) -> None:
    if any(frame.empty for frame, _, _ in bundle.values()):
        raise ValueError("不能用空数据覆盖缓存")
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = [{"cache_key": key, "dataset": dataset, "as_of": as_of,
             "source": source, "fetched_at": fetched_at,
             "payload": frame.to_json(orient="table", date_format="iso", force_ascii=False)}
            for dataset, (frame, as_of, source) in bundle.items()]
    with connect(path) as connection:
        connection.executemany("""INSERT INTO cache(cache_key,dataset,as_of,source,fetched_at,payload)
        VALUES(:cache_key,:dataset,:as_of,:source,:fetched_at,:payload)
        ON CONFLICT(cache_key,dataset) DO UPDATE SET as_of=excluded.as_of,
        source=excluded.source,fetched_at=excluded.fetched_at,payload=excluded.payload""", rows)


def load_frame(path: Path, key: str, dataset: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    with connect(path) as connection:
        row = connection.execute("SELECT payload FROM cache WHERE cache_key=? AND dataset=?", (key, dataset)).fetchone()
    return pd.DataFrame() if row is None else pd.read_json(StringIO(row["payload"]), orient="table", precise_float=True)


def record_update(path: Path, key: str, status: str, message: str) -> None:
    with connect(path) as connection:
        connection.execute("INSERT INTO updates(cache_key,finished_at,status,message) VALUES(?,?,?,?)",
                           (key, datetime.now().astimezone().isoformat(timespec="seconds"), status, message))


def latest_update(path: Path, key: str, successful_only: bool = False) -> dict | None:
    if not path.exists():
        return None
    with connect(path) as connection:
        sql = "SELECT * FROM updates WHERE cache_key=?"
        params = [key]
        if successful_only:
            sql += " AND status IN ('success','partial')"
        row = connection.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    return None if row is None else dict(row)


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
    return (None if amount is None else float(amount.group(1)) * 100_000_000,
            None if date is None else date.group(1))


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


def finite_numbers(frame: pd.DataFrame, columns: set[str], name: str, positive: bool = False) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        values = pd.to_numeric(result[column], errors="raise")
        if values.isna().any() or not values.map(isfinite).all() or (positive and (values <= 0).any()) or (not positive and (values < 0).any()):
            raise ValueError(f"{name} {column} 必须为{'正' if positive else '非负'}有限数值")
        result[column] = values
    return result


def fee_number(value) -> float:
    match = re.fullmatch(r"\s*([+]?(?:\d+(?:\.\d*)?|\.\d+))(?:[%％]|元(?:/(?:笔|年))?)?\s*", str(value))
    if match is None or not isfinite(float(match.group(1))):
        raise ValueError(f"无法解析费用: {value}")
    return float(match.group(1))


def fetch_catalog(api=akshare) -> pd.DataFrame:
    frame = need(api.fund_name_em(), {"基金代码", "基金简称", "基金类型"}, "基金目录")
    return frame.rename(columns={"基金代码": "fund_code", "基金简称": "fund_name", "基金类型": "fund_type"})[["fund_code", "fund_name", "fund_type"]]


def _official(code: str) -> dict:
    path = Path("data/fund_metadata.json")
    return json.loads(path.read_text(encoding="utf-8")).get(code, {})


def _core(code: str, api) -> Bundle:
    overview = need(api.fund_overview_em(symbol=code), {"基金简称", "基金代码"}, "基金概况").iloc[0]
    size, size_date = amount_and_date(overview.get("资产规模"))
    official = _official(code)
    has_official = bool(official)
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
    cumulative = finite_numbers(need(api.fund_open_fund_info_em(symbol=code, indicator="累计净值走势"), {"净值日期", "累计净值"}, "累计净值"), {"累计净值"}, "累计净值", positive=True)
    raw_dividends = need(api.fund_open_fund_info_em(symbol=code, indicator="分红送配详情"), {"除息日", "每份分红"}, "分红", allow_empty=True)
    raw_splits = need(api.fund_open_fund_info_em(symbol=code, indicator="拆分详情"), {"拆分折算日", "拆分折算比例"}, "拆分", allow_empty=True)
    dividends = pd.DataFrame(columns=["ex_date", "dividend_per_unit"])
    if not raw_dividends.empty:
        dividends = pd.DataFrame({"ex_date": raw_dividends["除息日"], "dividend_per_unit": raw_dividends["每份分红"].map(cash_dividend)})
    splits = pd.DataFrame(columns=["split_date", "split_ratio"])
    if not raw_splits.empty:
        splits = pd.DataFrame({"split_date": raw_splits["拆分折算日"], "split_ratio": raw_splits["拆分折算比例"].map(split_ratio)})
    nav = unit.rename(columns={"净值日期": "nav_date", "单位净值": "unit_nav"})[["nav_date", "unit_nav"]]
    nav["unit_nav"] = pd.to_numeric(nav["unit_nav"], errors="raise")
    if nav["unit_nav"].isna().any() or not nav["unit_nav"].map(isfinite).all() or (nav["unit_nav"] <= 0).any():
        raise ValueError("单位净值必须为正数且为有限数值")
    nav = nav.merge(cumulative.rename(columns={"净值日期": "nav_date", "累计净值": "cumulative_nav"}), on="nav_date", how="left")
    finite_numbers(nav, {"cumulative_nav"}, "累计净值", positive=True)
    nav = adjust_nav(nav, dividends, splits)
    as_of = nav["nav_date"].max()
    return {
        "metadata": (pd.DataFrame([metadata]), size_date or as_of, "AKShare/东方财富" + ("；博时基金官方资料" if has_official else "")),
        "nav": (nav, as_of, "AKShare/东方财富"),
    }


def fetch_fund(code: str, api=akshare) -> tuple[Bundle, list[str]]:
    bundle = _core(code, api)
    warnings: list[str] = []

    def add(name: str, fetch) -> None:
        try:
            bundle[name] = fetch()
        except Exception as error:
            warnings.append(f"{name}: {error}")

    def market():
        frame = finite_numbers(need(api.stock_zh_index_daily_em(symbol="sh000300", start_date="19900101", end_date="20500101"), {"date", "close"}, "沪深300"), {"close"}, "沪深300", positive=True)
        frame = frame.rename(columns={"date": "trade_date"})[["trade_date", "close"]]
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
        return frame, frame["trade_date"].max(), "AKShare/东方财富"

    def holdings():
        frame = finite_numbers(need(api.fund_portfolio_hold_em(symbol=code, date=""), {"序号", "股票代码", "股票名称", "占净值比例", "持仓市值", "季度"}, "基金持仓"), {"占净值比例", "持仓市值"}, "基金持仓")
        result = pd.DataFrame({"rank": frame["序号"], "security_code": frame["股票代码"].astype(str).str.zfill(6), "security_name": frame["股票名称"], "weight": pd.to_numeric(frame["占净值比例"]) / 100, "market_value_cny": pd.to_numeric(frame["持仓市值"]) * 10_000, "report_date": frame["季度"].map(quarter_end)})
        latest = result["report_date"].max()
        return result[result["report_date"] == latest], latest, "AKShare/东方财富"

    add("index", market)
    add("holdings", holdings)
    report_date = bundle.get("holdings", (pd.DataFrame(), "", ""))[1]
    if report_date:
        def industry():
            frame = finite_numbers(need(api.fund_portfolio_industry_allocation_em(symbol=code, date=report_date[:4]), {"行业类别", "占净值比例", "截止时间"}, "行业配置"), {"占净值比例"}, "行业配置")
            result = pd.DataFrame({"category": frame["行业类别"], "weight": pd.to_numeric(frame["占净值比例"]) / 100, "report_date": pd.to_datetime(frame["截止时间"]).dt.strftime("%Y-%m-%d")})
            latest = result["report_date"].max()
            return result[result["report_date"] == latest], latest, "AKShare/东方财富"

        def assets():
            frame = finite_numbers(need(api.fund_individual_detail_hold_xq(symbol=code, date=report_date.replace("-", "")), {"资产类型", "仓位占比"}, "资产配置"), {"仓位占比"}, "资产配置")
            result = pd.DataFrame({"category": frame["资产类型"], "weight": pd.to_numeric(frame["仓位占比"]) / 100})
            result["report_date"] = report_date
            return result, report_date, "AKShare/雪球基金"

        add("industry", industry)
        add("assets", assets)

    def fees():
        frame = need(api.fund_individual_detail_info_xq(symbol=code), {"费用类型", "条件或名称", "费用"}, "基金费率")
        frame["费用"].map(fee_number)
        result = frame.rename(columns={"费用类型": "fee_type", "条件或名称": "condition", "费用": "fee"})[["fee_type", "condition", "fee"]]
        return result, bundle["metadata"][1], "AKShare/雪球基金"

    add("fees", fees)
    return bundle, warnings


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
                fresh = pd.concat(([old] if not old.empty else []) + [fresh], ignore_index=True).drop_duplicates(date_column, keep="last").sort_values(date_column)
                bundle[dataset] = (fresh.reset_index(drop=True), as_of, source)
        save_bundle(path, code, bundle)
        status = "partial" if warnings else "success"
        message = "；".join(warnings) if warnings else "更新完成"
    except Exception as error:
        status, message = "failed", str(error)
    record_update(path, code, status, message)
    user_message = {"success": "更新完成", "partial": "核心数据已更新，部分持仓或费率数据暂不可用。", "failed": "更新失败，请稍后重试；本地旧数据仍然保留。"}[status]
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
