"""Pricing & Exit Signals — bid/fair/sell targets per recommended card."""

from __future__ import annotations

from datetime import date

import streamlit as st

from reports.app._components.auth import guard_localhost
from reports.app._components.ui import job_badge
from sportscards.reports.queries import (
    get_open_exit_signals,
    get_trade_targets,
    resolve_exit_signal,
)

st.set_page_config(page_title="Pricing", page_icon="💰", layout="wide")
guard_localhost()
job_badge()
st.title("💰 Pricing & Exit Signals")

st.header("Trade Targets")
as_of = st.date_input("As of date", value=date.today())
card_ids_str = st.text_input(
    "Card IDs (comma-separated)",
    help="Empty = show top 50 cards from the latest factor panel.",
)
if card_ids_str.strip():
    try:
        card_ids = [int(x.strip()) for x in card_ids_str.split(",") if x.strip()]
    except ValueError:
        st.error("Card IDs must be integers.")
        card_ids = []
else:
    card_ids = []  # Future: pull top-N from FactorPanel here

if card_ids:
    df = get_trade_targets(card_ids=card_ids, as_of=as_of)
    if df.empty:
        st.info("No trade targets for the selected cards on this date.")
    else:
        st.dataframe(
            df[["card_id", "bid_max", "fair_value", "sell_target", "stop_loss", "confidence"]],
            use_container_width=True,
            hide_index=True,
        )

st.divider()
st.header("Open Exit Signals")
signals = get_open_exit_signals()
if signals.empty:
    st.success("No unresolved exit signals.")
else:
    for _, row in signals.iterrows():
        cols = st.columns([1, 2, 3, 1])
        cols[0].write(f"#{row['id']}")
        cols[1].write(f"holding {row['holding_id']} · {row['rule_triggered']}")
        cols[2].write(row["notes"] or "")
        if cols[3].button("Resolve", key=f"resolve-{row['id']}"):
            resolve_exit_signal(int(row["id"]))
            st.rerun()
