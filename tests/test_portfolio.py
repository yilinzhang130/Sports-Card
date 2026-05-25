"""Tests for portfolio construction, transaction costs, and risk budget."""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

from sportscards.portfolio.construction import (
    AllocationConfig,
    UniverseSnapshot,
    build_portfolio,
)
from sportscards.portfolio.risk_budget import (
    beta_to_index,
    concentration,
    herfindahl_index,
    position_size_violations,
)
from sportscards.portfolio.transaction_costs import (
    FeeSchedule,
    cost_to_grade,
    landed_cost,
    net_proceeds,
    round_trip_cost_pct,
)

# ---- transaction costs ----


def test_ebay_net_proceeds() -> None:
    # 100 - 13.25 - 0.30 = 86.45
    assert net_proceeds(100, "ebay") == pytest.approx(86.45)


def test_goldin_landed_cost_includes_buyer_premium() -> None:
    assert landed_cost(100, "goldin") == pytest.approx(120.0)


def test_ebay_landed_cost_is_face() -> None:
    assert landed_cost(100, "ebay") == pytest.approx(100.0)


def test_psa_value_bulk_tier() -> None:
    assert cost_to_grade("value_bulk") == pytest.approx(24.99)


def test_unknown_tier_raises() -> None:
    with pytest.raises(ValueError):
        cost_to_grade("platinum")


def test_round_trip_cost_positive_at_flat_price() -> None:
    cost = round_trip_cost_pct(100, 100, "ebay", "ebay")
    assert cost > 0  # fees always make round-trip costly


def test_custom_schedule_overrides_default() -> None:
    sched = FeeSchedule(ebay_pct=0.10, ebay_flat=0.0)
    assert net_proceeds(100, "ebay", sched) == pytest.approx(90.0)


# ---- portfolio construction ----


def _make_universe(n_anchors: int = 5, n_factor: int = 10, n_prospect: int = 5):
    anchors = pd.DataFrame({"card_id": list(range(100, 100 + n_anchors)), "last_price": 1000.0})
    factor = pd.DataFrame(
        {
            "card_id": list(range(200, 200 + n_factor)),
            "mispricing_residual": [(-1) ** i * (i + 1) * 0.05 for i in range(n_factor)],
            "parallel_tier": ["base"] * n_factor,
            "sport": ["NBA"] * n_factor,
        }
    )
    prospect = pd.DataFrame(
        {
            "card_id": list(range(300, 300 + n_prospect)),
            "stardom_score": [10 - i for i in range(n_prospect)],
            "last_price": 200.0,
        }
    )
    return UniverseSnapshot(anchors_df=anchors, factor_df=factor, prospect_df=prospect)


def test_barbell_weights_close_to_one() -> None:
    # Wide universe + roomy caps so all 3 sleeves can be fully deployed
    universe = _make_universe(n_anchors=10, n_factor=100, n_prospect=20)
    cfg = AllocationConfig(
        anchor_position_cap_pct=0.10,
        other_position_cap_pct=0.05,
        prospect_per_name_cap_pct=0.05,
    )
    positions = build_portfolio(universe, cfg)
    total = sum(p.target_weight_pct for p in positions)
    assert total == pytest.approx(1.0, abs=1e-6)
    assert total <= 1.0 + 1e-9


def test_barbell_sleeves_sum_to_configured_weights() -> None:
    universe = _make_universe(n_anchors=10, n_factor=10, n_prospect=10)
    cfg = AllocationConfig(
        anchor_position_cap_pct=0.10,
        other_position_cap_pct=0.05,
        prospect_per_name_cap_pct=0.05,
    )
    positions = build_portfolio(universe, cfg)
    by_sleeve: dict[str, float] = {}
    for p in positions:
        by_sleeve[p.sleeve] = by_sleeve.get(p.sleeve, 0.0) + p.target_weight_pct
    assert by_sleeve.get("anchor", 0.0) == pytest.approx(0.70, abs=1e-6)


def test_anchor_only_fallback_emits_warning_and_redistributes() -> None:
    universe = UniverseSnapshot(
        anchors_df=pd.DataFrame({"card_id": [1, 2, 3, 4, 5], "last_price": 500.0}),
        factor_df=None,
        prospect_df=None,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        positions = build_portfolio(universe, AllocationConfig(anchor_position_cap_pct=0.30))
    assert any("missing sleeves" in str(w.message) for w in caught)
    anchor_total = sum(p.target_weight_pct for p in positions if p.sleeve == "anchor")
    assert anchor_total == pytest.approx(1.0, abs=1e-6)
    assert all(p.sleeve == "anchor" for p in positions)


def test_anchor_position_cap_enforced() -> None:
    universe = UniverseSnapshot(
        anchors_df=pd.DataFrame({"card_id": [1, 2], "last_price": 500.0}),
        factor_df=None,
        prospect_df=None,
    )
    cfg = AllocationConfig(anchor_position_cap_pct=0.05)  # only 10% total deployable
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        positions = build_portfolio(universe, cfg)
    assert all(p.target_weight_pct <= 0.05 + 1e-9 for p in positions)


def test_factor_long_only_when_short_decile_none() -> None:
    universe = _make_universe()
    positions = build_portfolio(universe, AllocationConfig(factor_decile_short=None))
    assert not any(p.sleeve == "factor_short" for p in positions)


# ---- risk budget ----


def test_position_size_violations_empty_under_caps() -> None:
    universe = _make_universe()
    cfg = AllocationConfig()
    positions = build_portfolio(universe, cfg)
    assert position_size_violations(positions, cfg) == []


def test_herfindahl_concentrated_when_single_card() -> None:
    universe = UniverseSnapshot(
        anchors_df=pd.DataFrame({"card_id": [1], "last_price": 500.0}),
        factor_df=None,
        prospect_df=None,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        positions = build_portfolio(universe, AllocationConfig(anchor_position_cap_pct=1.0))
    assert herfindahl_index(positions) == pytest.approx(1.0)


def test_concentration_by_player() -> None:
    universe = _make_universe()
    positions = build_portfolio(universe, AllocationConfig())
    meta = {
        p.card_id: {"player": "Jordan" if i % 2 == 0 else "Kobe", "year": 1996}
        for i, p in enumerate(positions)
    }
    hhi = concentration(positions, "player", meta)
    assert sum(hhi.values()) <= 1.0 + 1e-9
    assert set(hhi.keys()).issubset({"Jordan", "Kobe", "unknown"})


def test_beta_to_index_perfectly_correlated() -> None:
    r = pd.Series([0.01, 0.02, -0.01, 0.03])
    i = r * 2.0
    assert beta_to_index(r, i) == pytest.approx(0.5)


def test_target_positions_carry_signal_source():
    u = _make_universe()
    positions = build_portfolio(u, cfg=AllocationConfig(total_aum_usd=1_000_000))
    sources = {p.signal_source for p in positions}
    assert sources <= {"anchor", "factor", "prospect"}
    assert "anchor" in sources
