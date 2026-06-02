from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from click.testing import CliRunner
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sportscards.cli.__main__ import cli
from sportscards.db.models import CardIdentityCandidate, TxRaw
from sportscards.identity.card_identity import parse_card_identity
from sportscards.identity.materialize import materialize_card_identity_candidates


def test_parse_card_identity_keeps_set_parallel_and_grade_separate():
    select_title = "2014-15 Panini Select Stephen Curry Prizm Blue and Silver #113 Warriors PSA 10"
    prizm_title = "2013 Panini Prizm Stephen Curry #176 PSA 10 Golden State Warriors"

    select_identity = parse_card_identity(select_title, search_query="Stephen Curry Prizm PSA 10")
    prizm_identity = parse_card_identity(prizm_title, search_query="Stephen Curry Prizm PSA 10")

    assert select_identity.player_name == "Stephen Curry"
    assert select_identity.manufacturer == "Panini"
    assert select_identity.year == 2014
    assert select_identity.set_name == "Select"
    assert select_identity.parallel == "Blue and Silver Prizm"
    assert select_identity.card_number == "113"
    assert select_identity.slab_grader == "PSA"
    assert select_identity.slab_grade == Decimal("10")
    assert select_identity.canonical_key != prizm_identity.canonical_key
    assert prizm_identity.set_name == "Prizm"
    assert prizm_identity.parallel == "Base"


def test_parse_card_identity_marks_auto_rookie_and_print_run():
    identity = parse_card_identity(
        "2019 PANINI PRIZM FAST BREAK AUTO #NJK NIKOLA JOKIC PSA 10 AUTO",
        search_query="Nikola Jokic Prizm PSA 10",
    )

    assert identity.player_name == "Nikola Jokic"
    assert identity.set_name == "Prizm"
    assert identity.parallel == "Fast Break"
    assert identity.card_number == "NJK"
    assert identity.has_auto is True
    assert identity.is_rookie is False
    assert "auto" in identity.canonical_key


def test_materialize_card_identity_candidates_separates_distinct_cards(migrated_db):
    engine = create_engine(migrated_db)
    with Session(engine) as session:
        session.add_all(
            [
                _raw(
                    "2014-15 Panini Select Stephen Curry Prizm Blue and Silver "
                    "#113 Warriors PSA 10",
                    "Stephen Curry Prizm PSA 10",
                    "a",
                ),
                _raw(
                    "2013 Panini Prizm Stephen Curry #176 PSA 10 Golden State Warriors",
                    "Stephen Curry Prizm PSA 10",
                    "b",
                ),
            ]
        )
        session.commit()

    summary = materialize_card_identity_candidates(engine=engine)

    assert summary == {"processed": 2, "inserted": 2, "updated": 0, "skipped": 0}
    with Session(engine) as session:
        candidates = session.execute(select(CardIdentityCandidate)).scalars().all()
    assert len(candidates) == 2
    assert {c.set_name for c in candidates} == {"Select", "Prizm"}
    assert len({c.canonical_key for c in candidates}) == 2


def test_identity_materialize_cli_prints_summary(migrated_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_db)

    result = CliRunner().invoke(cli, ["identity", "materialize"])

    assert result.exit_code == 0
    assert '"processed": 0' in result.output


def _raw(title: str, search_query: str, external_id: str) -> TxRaw:
    return TxRaw(
        source="cardladder_manual",
        raw_title=title,
        raw_price=Decimal("100.00"),
        raw_currency="USD",
        sold_at=datetime(2026, 6, 1, tzinfo=UTC),
        external_id=external_id,
        raw_json={"search_query": search_query},
    )
