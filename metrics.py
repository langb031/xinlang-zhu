from __future__ import annotations

from math import isfinite, sqrt
import pandas as pd


OFFSETS = {
    "1月": pd.DateOffset(months=1), "3月": pd.DateOffset(months=3),
    "6月": pd.DateOffset(months=6), "1年": pd.DateOffset(years=1),
    "3年": pd.DateOffset(years=3), "5年": pd.DateOffset(years=5),
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
        values = pd.to_numeric(prepared["dividend_per_unit"], errors="raise")
        if values.isna().any() or not values.map(isfinite).all():
            raise ValueError("分红必须为有限数值")
        prepared["dividend_per_unit"] = values
        dividend_map = prepared.groupby("ex_date")["dividend_per_unit"].sum().to_dict()
    split_map = {}
    if splits is not None and not splits.empty:
        prepared = splits.assign(split_date=pd.to_datetime(splits["split_date"]))
        values = pd.to_numeric(prepared["split_ratio"], errors="raise")
        if values.isna().any() or not values.map(isfinite).all() or (values <= 0).any():
            raise ValueError("拆分比例必须为正数且为有限数值")
        prepared["split_ratio"] = values
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
    values = pd.to_numeric(ordered["adjusted_nav"], errors="raise")
    if values.isna().any() or not values.map(isfinite).all() or (values <= 0).any():
        raise ValueError("复权净值必须为正数且不能缺失")
    daily = values.pct_change(fill_method=None).dropna()
    daily_std = daily.std(ddof=1)
    running_high = values.cummax(); drawdown = values / running_high - 1
    trough = int(drawdown.idxmin()); peak = int(values.loc[:trough].idxmax())
    result = {"total_return": _return(values), "annualized_return": None if len(daily) < 250 else float((values.iloc[-1] / values.iloc[0]) ** (250 / len(daily)) - 1), "volatility": None if len(daily) < 30 else float(daily_std * sqrt(250)), "sharpe": None if len(daily) < 30 or daily_std <= 1e-12 else float(daily.mean() / daily_std * sqrt(250)), "max_drawdown": float(drawdown.loc[trough]), "drawdown_peak": ordered.loc[peak, "nav_date"].strftime("%Y-%m-%d"), "drawdown_trough": ordered.loc[trough, "nav_date"].strftime("%Y-%m-%d"), "drawdown_series": pd.DataFrame({"date": ordered["nav_date"], "drawdown": drawdown})}
    as_of = ordered.iloc[-1]["nav_date"]
    result["period_returns"] = {name: (None if ordered[ordered["nav_date"] <= as_of - offset].empty else float(values.iloc[-1] / values.loc[ordered["nav_date"] <= as_of - offset].iloc[-1] - 1)) for name, offset in OFFSETS.items()}
    result["period_returns"]["成立以来"] = result["total_return"]
    result["calendar_returns"] = pd.DataFrame([{"year": f"{year}（年初至今）" if year == as_of.year else str(year), "return": _return(group["adjusted_nav"])} for year, group in ordered.groupby(ordered["nav_date"].dt.year)])
    result["manager_return"] = None
    if manager_start_date and pd.to_datetime(manager_start_date) <= as_of:
        earlier = ordered[ordered["nav_date"] <= pd.to_datetime(manager_start_date)]
        if not earlier.empty:
            starting_value = earlier.iloc[-1]["adjusted_nav"]
            result["manager_return"] = float(values.iloc[-1] / starting_value - 1)
    comparison = performance_series(nav, index) if index is not None and not index.empty else pd.DataFrame()
    result["comparison"] = comparison; result["excess_return"] = None if comparison.empty else float((comparison.iloc[-1]["fund"] - 1) - (comparison.iloc[-1]["csi300"] - 1))
    return result


def performance_series(nav: pd.DataFrame, index: pd.DataFrame) -> pd.DataFrame:
    fund = nav[["nav_date", "adjusted_nav"]].rename(columns={"nav_date": "date"}); market = index[["trade_date", "close"]].rename(columns={"trade_date": "date"})
    merged = fund.merge(market, on="date", how="inner").sort_values("date")
    if merged.empty: return merged
    merged["fund"] = merged["adjusted_nav"] / merged.iloc[0]["adjusted_nav"]; merged["csi300"] = merged["close"] / merged.iloc[0]["close"]
    return merged[["date", "fund", "csi300"]]
