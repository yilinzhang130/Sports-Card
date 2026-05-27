"""Render the monthly investor letter from a Jinja template."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from sportscards.reports.queries import LetterMetrics, collect_letter_metrics

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = REPO_ROOT / "reports"
DEFAULT_OUT_DIR = REPO_ROOT / "letters"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def render_monthly_letter(month: str, out_dir: Path | None = None) -> Path:
    """Render letters/<month>.md. Idempotent — re-running overwrites."""
    metrics: LetterMetrics = collect_letter_metrics(month)
    template = _env().get_template("monthly_letter.md.j2")
    body = template.render(
        month=metrics.month,
        index_returns=metrics.index_returns,
        top_mispricings=metrics.top_mispricings,
        rebalance_trades=metrics.rebalance_trades,
        fee_drag_ytd=metrics.fee_drag_ytd,
        sleeve_allocation=metrics.sleeve_allocation,
    )
    target_dir = out_dir if out_dir is not None else DEFAULT_OUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / f"{month}.md"
    out.write_text(body)
    return out


# Re-export for tests that monkeypatch the renderer module.
__all__ = [
    "LetterMetrics",
    "collect_letter_metrics",
    "render_monthly_letter",
    "render_pricing_panel",
    "render_exit_signals_tab",
]


# --- Trader Console render functions (Phase 6) ---------------------------------
# streamlit is imported inside each function so queries.py remains importable
# in headless (non-Streamlit) test environments.


def render_pricing_panel(card_ids: list[int], as_of) -> None:
    """Render a trade-targets dataframe for the given card IDs."""
    import streamlit as st

    from sportscards.reports.queries import get_trade_targets

    df = get_trade_targets(card_ids=card_ids, as_of=as_of)
    if df.empty:
        st.info("No trade targets for the selected cards.")
        return
    st.subheader("Trade Targets")
    st.dataframe(
        df[["card_id", "bid_max", "fair_value", "sell_target", "stop_loss", "confidence"]],
        use_container_width=True,
        hide_index=True,
    )


def render_exit_signals_tab() -> None:
    """Render open exit signals with a per-row Resolve button."""
    import streamlit as st

    from sportscards.reports.queries import get_open_exit_signals, resolve_exit_signal

    df = get_open_exit_signals()
    st.subheader("Open Exit Signals")
    if df.empty:
        st.success("No unresolved exit signals.")
        return
    for _, row in df.iterrows():
        cols = st.columns([1, 2, 2, 1])
        cols[0].write(f"#{row['id']}")
        cols[1].write(f"holding {row['holding_id']} · {row['rule_triggered']}")
        cols[2].write(row["notes"] or "")
        if cols[3].button("Resolve", key=f"resolve-{row['id']}"):
            resolve_exit_signal(int(row["id"]))
            st.rerun()
