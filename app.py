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

metadata_frame = load_frame(DB, code, "metadata")
metadata = {} if metadata_frame.empty else metadata_frame.iloc[0].to_dict()
index = load_frame(DB, code, "index")
holdings = load_frame(DB, code, "holdings")
industry = load_frame(DB, code, "industry")
assets = load_frame(DB, code, "assets")
fees = load_frame(DB, code, "fees")
result = analyze(nav, index, metadata.get("manager_start_date"))

st.header(f"{metadata.get('fund_name', code)}（{code}）")
st.write(" · ".join(filter(None, [metadata.get("fund_type"), metadata.get("risk_level"), metadata.get("management_company"), metadata.get("manager")])) )
st.caption(f"净值日期：{nav['nav_date'].max()} · 最后成功更新：{(latest_update(DB, code, successful_only=True) or {}).get('finished_at', '暂无数据')}")
cards = st.columns(4)
cards[0].metric("单位净值", f"{nav.iloc[-1]['unit_nav']:.4f}")
cards[1].metric("近一年收益", pct(result["period_returns"].get("1年")))
cards[2].metric("最大回撤", "样本不足" if len(nav) < 2 else pct(result["max_drawdown"]))
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
    if annual.empty:
        st.info("年度收益样本不足")
    else:
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
