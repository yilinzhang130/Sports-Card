from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import TxRaw
from sportscards.ingest.cardladder_queue import next_searches
from sportscards.reports import queries


def _raw(query: str, price: str, external_id: str) -> TxRaw:
    return TxRaw(
        source="cardladder_manual",
        raw_title=query,
        raw_price=Decimal(price),
        raw_currency="USD",
        sold_at=datetime(2026, 6, 1, tzinfo=UTC),
        external_id=external_id,
        raw_json={"search_query": query},
    )


def test_cardladder_player_radar_scores_price_and_coverage(migrated_db):
    engine = create_engine(migrated_db)
    with Session(engine) as session:
        session.add_all(
            [
                _raw("Amen Thompson Prizm PSA 10", "100", "amen-1"),
                _raw("Amen Thompson Prizm PSA 10", "200", "amen-2"),
                _raw("Amen Thompson Prizm PSA 10", "1000", "amen-3"),
                _raw("Ausar Thompson Prizm PSA 10", "20", "ausar-1"),
            ]
        )
        session.commit()

    radar = queries.cardladder_player_radar(engine=engine)

    amen = radar.loc[radar.search_query == "Amen Thompson Prizm PSA 10"].iloc[0]
    assert int(amen["rows"]) == 3
    assert float(amen["median_price"]) == 200.0
    assert float(amen["high_sale"]) == 1000.0
    assert float(amen["premium_sale_pct"]) > 0
    assert float(amen["radar_score"]) > 0
    assert amen["next_action"] == "ingest_more"


def test_cardladder_queue_prioritizes_undercovered_prospect_queries():
    coverage = {
        "Michael Jordan Fleer PSA 10": 100,
        "LeBron James Topps Chrome PSA 10": 100,
        "Kobe Bryant Topps Chrome PSA 10": 100,
        "Stephen Curry Topps Chrome PSA 10": 100,
        "Stephen Curry Prizm PSA 10": 100,
        "Kevin Durant Topps Chrome PSA 10": 100,
        "Giannis Antetokounmpo Prizm PSA 10": 100,
        "Nikola Jokic Prizm PSA 10": 100,
        "Luka Doncic Prizm PSA 10": 100,
        "Victor Wembanyama Prizm PSA 10": 100,
    }

    queries_to_run = [row.query for row in next_searches(coverage, limit=12)]

    assert "Amen Thompson Prizm PSA 10" in queries_to_run
    assert "Jalen Williams Prizm PSA 10" in queries_to_run
