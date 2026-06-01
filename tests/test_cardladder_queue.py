from sportscards.ingest.cardladder_queue import next_searches, query_tiers


def test_query_tiers_include_required_anchor_players():
    tiers = query_tiers()
    tier_a = [q.query for q in tiers if q.tier == "A"]

    assert "LeBron James Topps Chrome PSA 10" in tier_a
    assert "Stephen Curry Prizm PSA 10" in tier_a
    assert "Victor Wembanyama Prizm PSA 10" in tier_a


def test_next_searches_prioritizes_undercovered_tier_a():
    rows = next_searches(
        coverage={
            "Michael Jordan Fleer PSA 10": 100,
            "LeBron James Topps Chrome PSA 10": 120,
            "Kobe Bryant Topps Chrome PSA 10": 100,
            "Stephen Curry Topps Chrome PSA 10": 100,
            "Stephen Curry Prizm PSA 10": 20,
            "Kevin Durant Topps Chrome PSA 10": 100,
            "Giannis Antetokounmpo Prizm PSA 10": 100,
            "Nikola Jokic Prizm PSA 10": 100,
            "Luka Doncic Prizm PSA 10": 100,
            "Victor Wembanyama Prizm PSA 10": 0,
        },
        limit=3,
    )

    assert rows[0].query == "Victor Wembanyama Prizm PSA 10"
    assert rows[1].query == "Stephen Curry Prizm PSA 10"


def test_next_searches_stays_in_tier_order_before_lower_tiers():
    rows = next_searches(
        coverage={
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
        },
        limit=1,
    )

    assert rows[0].tier == "B"
