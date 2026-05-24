"""Read-only Streamlit dashboard for sportscards-quant.

Run with: `streamlit run reports/dashboard.py` or `sportscards dashboard`.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from sportscards.reports import queries
from sportscards.reports.queries import TableMissing


@st.cache_data(ttl=300)
def _cached_index() -> pd.DataFrame:
    return queries.repeat_sales_index()


@st.cache_data(ttl=300)
def _cached_mispricing() -> dict[str, pd.DataFrame]:
    return queries.mispricing_leaderboard()


@st.cache_data(ttl=300)
def _cached_stardom() -> pd.DataFrame:
    return queries.stardom_scores()


@st.cache_data(ttl=300)
def _cached_player_prices(player_id: int) -> pd.DataFrame:
    return queries.player_price_history(player_id)


@st.cache_data(ttl=300)
def _cached_backtest() -> pd.DataFrame:
    return queries.backtest_nav()


@st.cache_data(ttl=300)
def _cached_health() -> dict[str, pd.DataFrame]:
    return queries.data_health()


def _placeholder(phase: str) -> None:
    st.info(f"Coming with {phase}.")


def _market_tab() -> None:
    st.header("Market Overview")
    try:
        df = _cached_index()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    if df.empty:
        st.write("No index data yet.")
        return
    fig = px.line(
        df,
        x="as_of",
        y="index_value",
        color="sleeve",
        title="Repeat-Sales Index",
    )
    st.plotly_chart(fig, use_container_width=True)


def _mispricing_tab() -> None:
    st.header("Mispricing Leaderboard")
    try:
        d = _cached_mispricing()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    st.subheader("Top 20 Undervalued (positive residual)")
    st.dataframe(d["undervalued"], use_container_width=True)
    st.subheader("Top 20 Overvalued (negative residual)")
    st.dataframe(d["overvalued"], use_container_width=True)


def _prospect_tab() -> None:
    st.header("Prospect Board")
    try:
        df = _cached_stardom()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    if df.empty:
        st.write("No stardom scores yet.")
        return
    st.dataframe(df, use_container_width=True)
    chosen = st.selectbox("Player price sparkline", options=df["name"].tolist())
    if chosen:
        pid = int(df.loc[df["name"] == chosen, "player_id"].iloc[0])
        try:
            prices = _cached_player_prices(pid)
        except TableMissing as e:
            _placeholder(e.phase)
            return
        if not prices.empty:
            fig = px.line(
                prices,
                x="sold_at",
                y="price_usd",
                title=f"{chosen} — recent sales",
            )
            st.plotly_chart(fig, use_container_width=True)


def _portfolio_tab() -> None:
    st.header("Portfolio")
    try:
        nav = _cached_backtest()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    if nav.empty:
        st.write("No backtest runs yet.")
        return
    fig = px.line(nav, x="as_of", y="nav", title="Latest Backtest NAV")
    st.plotly_chart(fig, use_container_width=True)


def _health_tab() -> None:
    st.header("Data Health")
    try:
        h = _cached_health()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    st.subheader("Raw vs. Clean rows (last 30 days)")
    st.dataframe(h["raw_vs_clean"], use_container_width=True)
    st.subheader("Parse failures (cumulative)")
    st.metric("failures", int(h["failures"]["n"].iloc[0]))
    st.caption("PSA quota tracking: pending instrumentation.")


def render_dashboard() -> None:
    st.set_page_config(page_title="sportscards-quant", layout="wide")
    st.title("sportscards-quant")
    tabs = st.tabs(["Market", "Mispricing", "Prospects", "Portfolio", "Data Health"])
    with tabs[0]:
        _market_tab()
    with tabs[1]:
        _mispricing_tab()
    with tabs[2]:
        _prospect_tab()
    with tabs[3]:
        _portfolio_tab()
    with tabs[4]:
        _health_tab()


if __name__ == "__main__":
    render_dashboard()
