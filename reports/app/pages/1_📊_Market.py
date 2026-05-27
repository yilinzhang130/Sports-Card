"""Market — read-only views ported from the legacy single-file dashboard.

Tabs: Index, Mispricing, Prospects, Forward Prospects, Factor Panel.
(Data Health lives on Home.py.)
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from reports.app._components.auth import guard_localhost
from reports.app._components.ui import job_badge
from sportscards.reports import queries
from sportscards.reports.queries import TableMissing

st.set_page_config(page_title="Market", page_icon="📊", layout="wide")
guard_localhost()
job_badge()

st.title("📊 Market")


# --- cached query wrappers ---------------------------------------------------


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
def _cached_top_catalysts() -> pd.DataFrame:
    return queries.top_catalysts(days=30, limit=10)


@st.cache_data(ttl=300)
def _cached_recent_events() -> pd.DataFrame:
    return queries.recent_events(days=30, limit=100)


@st.cache_data(ttl=300)
def _cached_catalyst_sparkline(player_id: int) -> pd.DataFrame:
    return queries.player_catalyst_sparkline(player_id)


@st.cache_data(ttl=300)
def _cached_grading_ev() -> pd.DataFrame:
    return queries.grading_ev_leaderboard()


@st.cache_data(ttl=300)
def _cached_factor_panel() -> pd.DataFrame:
    return queries.factor_panel_latest()


def _placeholder(phase: str) -> None:
    st.info(f"Coming with {phase}.")


# --- tab implementations (verbatim from legacy dashboard) --------------------


def _market_tab() -> None:
    st.header("Market Overview")
    try:
        df = _cached_index()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    except Exception as e:  # pragma: no cover — defensive
        st.info(f"Index unavailable: {e}")
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
    except Exception as e:  # pragma: no cover — defensive
        st.info(f"Mispricing data unavailable: {e}")
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
    # Must include every feature in hedonic.NUMERICAL_FEATURES /
    # BOOLEAN_FEATURES / CATEGORICAL_FEATURES, otherwise the design-matrix
    # build raises KeyError when newer hedonic versions add columns.
    return {
        "log_pop_psa10": 4.0,
        "log_pop_psa9_or_better": 4.5,
        "parallel_tier": 2,
        "print_run_log": 3.0,
        "slab_grade": 10.0,
        "player_age_at_sale": 22.0,
        "years_since_draft": 1,
        "draft_pick": 10,
        "cs_momentum_pct": 0.5,
        "log_sales_count_90d": 1.5,
        "bid_ask_proxy": 0.1,
        "stardom_premium": 0.0,
        "stardom_premium_x_is_rookie": 0.0,
        "catalyst_score": 0.0,
        "catalyst_score_30d_change": 0.0,
        "is_rookie": 1,
        "has_auto": 0,
        "has_patch": 0,
        "is_one_of_one": 0,
        "era_modern": 1,
        "is_hyped": False,
        "has_stardom_score": False,
        "set_tier": "flagship",
        "team_market": "standard",
        "slab_grader": "PSA",
        "liquidity_tier": "B",
    }


def _prospects_tab() -> None:
    st.header("Prospect Board")
    try:
        df = _cached_stardom()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    except Exception as e:  # pragma: no cover — defensive
        st.info(f"Prospect data unavailable: {e}")
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


def _catalysts_tab() -> None:
    st.header("Catalysts")
    try:
        top = _cached_top_catalysts()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    st.subheader("Top 10 catalysts (last 30 days)")
    st.dataframe(top, use_container_width=True)

    st.subheader("Recent events")
    try:
        events = _cached_recent_events()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    st.dataframe(events, use_container_width=True)

    st.subheader("Player catalyst sparkline")
    if top.empty:
        st.write("No catalyst-scored players in the window.")
        return
    chosen = st.selectbox("Player", options=top["player_name"].tolist(), key="catalyst_player")
    if chosen:
        pid = int(top.loc[top["player_name"] == chosen, "player_id"].iloc[0])
        try:
            spark = _cached_catalyst_sparkline(pid)
        except TableMissing as e:
            _placeholder(e.phase)
            return
        if not spark.empty:
            fig = px.line(
                spark,
                x="as_of",
                y="catalyst_score",
                title=f"{chosen} — catalyst score",
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
    except Exception as e:  # pragma: no cover — defensive
        st.info(f"Forward prospects data unavailable: {e}")
        return
    if df.empty:
        st.info(
            "No forecasts yet. Run "
            "`sportscards scouting score-class --draft-year YYYY --season 2025-26` "
            "to populate."
        )
        return
    st.dataframe(df, use_container_width=True)


def _catalysts_tab() -> None:
    st.header("Catalysts")
    try:
        top = _cached_top_catalysts()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    st.subheader("Top 10 catalysts (last 30 days)")
    st.dataframe(top, use_container_width=True)

    st.subheader("Recent events")
    try:
        events = _cached_recent_events()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    st.dataframe(events, use_container_width=True)

    st.subheader("Player catalyst sparkline")
    if top.empty:
        st.write("No catalyst-scored players in the window.")
        return
    chosen = st.selectbox("Player", options=top["player_name"].tolist(), key="catalyst_player")
    if chosen:
        pid = int(top.loc[top["player_name"] == chosen, "player_id"].iloc[0])
        try:
            spark = _cached_catalyst_sparkline(pid)
        except TableMissing as e:
            _placeholder(e.phase)
            return
        if not spark.empty:
            fig = px.line(
                spark,
                x="as_of",
                y="catalyst_score",
                title=f"{chosen} — catalyst score",
            )
            st.plotly_chart(fig, use_container_width=True)


def _grading_ev_tab() -> None:
    st.header("Grading EV — raw → PSA 10 optionality")
    try:
        df = _cached_grading_ev()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    except Exception as e:  # pragma: no cover — defensive
        st.info(f"Grading EV unavailable: {e}")
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


def _factor_tab() -> None:
    st.header("Factor Panel — Momentum + Liquidity")
    try:
        df = _cached_factor_panel()
    except TableMissing as e:
        _placeholder(e.phase)
        return
    except Exception as e:  # pragma: no cover — defensive
        st.info(f"Factor panel data unavailable: {e}")
        return
    if df.empty:
        st.write("No factor_panel snapshot yet. Run `sportscards factor compute-panel`.")
        return
    st.caption(f"As of {df['as_of_date'].iloc[0]} — {len(df)} cards")
    st.dataframe(df, use_container_width=True)


# --- render ------------------------------------------------------------------

tab_index, tab_mispricing, tab_prospects, tab_forward, tab_catalysts, tab_factor, tab_grading = (
    st.tabs(
        [
            "Index",
            "Mispricing",
            "Prospects",
            "Forward Prospects",
            "Catalysts",
            "Factor Panel",
            "Grading EV",
        ]
    )
)
with tab_index:
    _market_tab()
with tab_mispricing:
    _mispricing_tab()
with tab_prospects:
    _prospects_tab()
with tab_forward:
    _forward_prospects_tab()
with tab_catalysts:
    _catalysts_tab()
with tab_factor:
    _factor_tab()
with tab_grading:
    _grading_ev_tab()
