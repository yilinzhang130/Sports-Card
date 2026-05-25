"""Portfolio — target weights, overrides, holdings ledger."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as _pd
import pandas as pd
import streamlit as st

from reports.app._components.audit import write_audit
from reports.app._components.auth import guard_localhost
from reports.app._components.holdings import (
    add_holding,
    current_weights,
    list_holdings,
    mark_sold,
)
from reports.app._components.overrides import list_overrides, set_override
from reports.app._components.ui import confirm_toggle, job_badge
from sportscards.db.session import session_scope
from sportscards.portfolio.adapters import load_anchors, load_mispricing, load_stardom
from sportscards.portfolio.construction import (
    AllocationConfig,
    UniverseSnapshot,
    apply_overrides,
    build_portfolio,
)
from sportscards.reports import queries

st.set_page_config(page_title="Portfolio", page_icon="💼", layout="wide")
guard_localhost()
job_badge()
st.title("💼 Portfolio")


# --- Target weights ----------------------------------------------------------
st.header("Target weights")
try:
    now = _pd.Timestamp.utcnow()
    with session_scope() as s:
        anchors = load_anchors(s)
        try:
            mispricing = load_mispricing(s, now)
        except Exception:
            mispricing = None
        try:
            stardom = load_stardom(s, now)
        except Exception:
            stardom = None
        positions = build_portfolio(
            UniverseSnapshot(anchors_df=anchors, factor_df=mispricing, prospect_df=stardom),
            AllocationConfig(total_aum_usd=1_000_000.0),
        )
        positions = apply_overrides(positions, s)

    df = pd.DataFrame(
        [
            {
                "card_id": p.card_id,
                "sleeve": p.sleeve,
                "target_weight_pct": p.target_weight_pct,
                "target_usd_value": p.target_usd_value,
                "signal_source": p.signal_source,
                "is_override": "OVR" if p.is_override else "",
            }
            for p in positions
        ]
    )
    if df.empty:
        st.info("No anchors loaded yet — seed the universe first.")
    else:
        st.dataframe(df, use_container_width=True)

        st.subheader("Apply override")
        with st.form("override_form"):
            card_id = st.number_input("card_id", min_value=1, step=1)
            weight = st.number_input("Target weight (e.g. 0.05 = 5%)", 0.0, 1.0, 0.05, 0.01)
            reason = st.text_input("Reason", value="manual")
            ok = confirm_toggle("confirm_override")
            submitted = st.form_submit_button("Apply", disabled=not ok)
            if submitted:
                set_override(int(card_id), Decimal(str(weight)), reason=reason)
                st.success(f"override set for card_id={card_id}")
                st.rerun()

        existing = list_overrides()
        if existing:
            st.caption("Current overrides")
            st.dataframe(pd.DataFrame(existing), use_container_width=True)
except Exception as e:  # pragma: no cover — defensive
    st.warning(f"target weights unavailable: {e}")


# --- NAV chart ---------------------------------------------------------------
st.header("Backtest NAV")
try:
    nav = queries.backtest_nav()
    if nav.empty:
        st.info("No backtest runs yet.")
    else:
        st.line_chart(nav.set_index("as_of")["nav"])
except queries.TableMissing as e:
    st.info(f"Backtest NAV unavailable yet — {e.phase}.")
except Exception as e:  # pragma: no cover
    st.warning(f"NAV unavailable: {e}")


# --- Holdings ledger ---------------------------------------------------------
st.header("Holdings ledger")
held = list_holdings("held")
if held:
    st.dataframe(pd.DataFrame(held), use_container_width=True)
else:
    st.info("No held positions recorded yet.")

with st.expander("Record a buy"), st.form("buy_form"):
    b_card_id = st.number_input("card_id", min_value=1, step=1, key="buy_card")
    b_cert = st.text_input("Cert number (optional)")
    b_grader = st.text_input("Slab grader (e.g. PSA)", value="PSA")
    b_grade = st.number_input("Grade", 0.0, 10.0, 10.0, 0.5)
    b_at = st.date_input("Acquired at", value=datetime.utcnow().date())
    b_cost = st.number_input("Cost USD", 0.0, step=1.0)
    b_channel = st.text_input("Channel (e.g. ebay)", value="ebay")
    ok = confirm_toggle("confirm_buy")
    submitted = st.form_submit_button("Record buy", disabled=not ok)
    if submitted:
        add_holding(
            card_id=int(b_card_id),
            cert_number=b_cert or None,
            slab_grader=b_grader or None,
            slab_grade=Decimal(str(b_grade)),
            acquired_at=datetime.combine(b_at, datetime.min.time()),
            acquired_cost_usd=Decimal(str(b_cost)),
            channel=b_channel,
        )
        st.success("buy recorded")
        st.rerun()

with st.expander("Mark sold"), st.form("sell_form"):
    s_id = st.number_input("holding_id", min_value=1, step=1)
    s_at = st.date_input("Sold at", value=datetime.utcnow().date())
    s_proceeds = st.number_input("Proceeds USD", 0.0, step=1.0)
    ok = confirm_toggle("confirm_sell")
    submitted = st.form_submit_button("Mark sold", disabled=not ok)
    if submitted:
        try:
            mark_sold(
                int(s_id),
                sold_at=datetime.combine(s_at, datetime.min.time()),
                sold_proceeds_usd=Decimal(str(s_proceeds)),
            )
            st.success("sale recorded")
            st.rerun()
        except KeyError as e:
            st.error(str(e))


# --- Trade list --------------------------------------------------------------
st.header("Generate trade list")
if st.button("Compute target vs current diff"):
    try:
        now = _pd.Timestamp.utcnow()
        with session_scope() as s:
            anchors = load_anchors(s)
            try:
                mispricing = load_mispricing(s, now)
            except Exception:
                mispricing = None
            try:
                stardom = load_stardom(s, now)
            except Exception:
                stardom = None
            positions = build_portfolio(
                UniverseSnapshot(anchors_df=anchors, factor_df=mispricing, prospect_df=stardom),
                AllocationConfig(total_aum_usd=1_000_000.0),
            )
            positions = apply_overrides(positions, s)
        current = current_weights()
        rows = []
        seen = set()
        for p in positions:
            cur = current.get(p.card_id, 0.0)
            diff = p.target_weight_pct - cur
            rows.append(
                {
                    "card_id": p.card_id,
                    "target_weight_pct": p.target_weight_pct,
                    "current_weight_pct": cur,
                    "diff": diff,
                    "action": "buy" if diff > 0 else "sell" if diff < 0 else "hold",
                }
            )
            seen.add(p.card_id)
        # Cards held but not in target → sell
        for cid, cur in current.items():
            if cid not in seen:
                rows.append(
                    {
                        "card_id": cid,
                        "target_weight_pct": 0.0,
                        "current_weight_pct": cur,
                        "diff": -cur,
                        "action": "sell",
                    }
                )
        trade_df = pd.DataFrame(rows)
        st.dataframe(trade_df, use_container_width=True)
        write_audit("trade_list_generated", {"n_rows": len(trade_df)})
        st.download_button(
            "Download CSV",
            data=trade_df.to_csv(index=False).encode(),
            file_name="trade_list.csv",
            mime="text/csv",
        )
    except Exception as e:  # pragma: no cover
        st.warning(f"trade list unavailable: {e}")
