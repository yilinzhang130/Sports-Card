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
def _cached_forward_prospects() -> pd.DataFrame:
    return queries.forward_prospects()


@st.cache_data(ttl=300)
def _cached_backtest() -> pd.DataFrame:
    return queries.backtest_nav()


@st.cache_data(ttl=300)
def _cached_health() -> dict[str, pd.DataFrame]:
    return queries.data_health()


@st.cache_data(ttl=300)
def _cached_grading_ev() -> pd.DataFrame:
    return queries.grading_ev_leaderboard()


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


def _compute_uplift(stardom_df: pd.DataFrame) -> pd.Series:
    """Counterfactual hedonic_v2 fitted-price uplift (%): predict with
    stardom_premium=row.premium vs. 0 on a stub modern-rookie feature row.
    Returns NaN series if the saved model file is absent."""
    import numpy as np
    import pandas as pd

    from sportscards.factors.hedonic import MODEL_PATH, load_model, predict

    if not MODEL_PATH.exists():
        return pd.Series([float("nan")] * len(stardom_df), index=stardom_df.index)
    model, enc, _ = load_model()
    base = _stub_feature_row()
    out = []
    for prem in stardom_df["premium"].astype(float):
        with_ = base.copy()
        with_["stardom_premium"] = prem
        with_["stardom_premium_x_is_rookie"] = prem * with_["is_rookie"]
        with_["has_stardom_score"] = True
        without = base.copy()
        without["stardom_premium"] = 0.0
        without["stardom_premium_x_is_rookie"] = 0.0
        without["has_stardom_score"] = False
        log_with = predict(model, enc, pd.DataFrame([with_]))[0]
        log_without = predict(model, enc, pd.DataFrame([without]))[0]
        out.append(100.0 * (float(np.exp(log_with - log_without)) - 1.0))
    return pd.Series(out, index=stardom_df.index)


def _stub_feature_row() -> dict:
    return {
        "log_pop_psa10": 4.0,
        "log_pop_psa9_or_better": 4.5,
        "parallel_tier": 2,
        "print_run_log": 3.0,
        "slab_grade": 10.0,
        "player_age_at_sale": 22.0,
        "years_since_draft": 1,
        "draft_pick": 10,
        "is_rookie": 1,
        "has_auto": 0,
        "has_patch": 0,
        "is_one_of_one": 0,
        "era_modern": 1,
        "set_tier": "flagship",
        "team_market": "standard",
        "slab_grader": "PSA",
        "stardom_premium": 0.0,
        "has_stardom_score": False,
        "stardom_premium_x_is_rookie": 0.0,
    }


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
    df = df.copy()
    df["card_fair_value_uplift_pct"] = _compute_uplift(df)
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


def _forward_prospects_tab() -> None:
    st.header("Forward Prospects")
    st.caption(
        "Pre-draft PRISM scores for current-season NCAA prospects. "
        "Premium = pairwise percentile − mock-draft consensus percentile. "
        "Positive = market under-prices the prospect."
    )
    try:
        df = _cached_forward_prospects()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    if df.empty:
        st.info(
            "No forecasts yet. Run "
            "`sportscards scouting score-class --draft-year YYYY --season 2025-26` "
            "to populate."
        )
        return
    st.dataframe(df, use_container_width=True)


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


def _grading_ev_tab() -> None:
    st.header("Grading EV — raw → PSA 10 optionality")
    try:
        df = _cached_grading_ev()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    if df.empty:
        st.write("No grading-EV rows yet — run `sportscards ev compute`.")
        return

    def _highlight(v):
        if pd.isna(v):
            return ""
        if v > 0.30:
            return "background-color: #d4edda"
        if v < 0:
            return "background-color: #f8d7da"
        return ""

    st.dataframe(
        df.style.map(_highlight, subset=["ev_per_dollar"]),
        use_container_width=True,
    )
    st.caption("Small sample_size = noisier gem_rate estimate; treat <20 as speculative.")


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
    tabs = st.tabs(
        [
            "Market",
            "Mispricing",
            "Prospects",
            "Forward Prospects",
            "Portfolio",
            "Grading EV",
            "Data Health",
        ]
    )
    with tabs[0]:
        _market_tab()
    with tabs[1]:
        _mispricing_tab()
    with tabs[2]:
        _prospect_tab()
    with tabs[3]:
        _forward_prospects_tab()
    with tabs[4]:
        _portfolio_tab()
    with tabs[5]:
        _grading_ev_tab()
    with tabs[6]:
        _health_tab()


if __name__ == "__main__":
    render_dashboard()
