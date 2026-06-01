"""Card Ladder search queue for agent-operated NBA sales capture."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CardLadderQuery:
    tier: str
    query: str
    target_rows: int
    cadence: str


def query_tiers() -> list[CardLadderQuery]:
    return [
        CardLadderQuery("A", "Michael Jordan Fleer PSA 10", 100, "daily"),
        CardLadderQuery("A", "LeBron James Topps Chrome PSA 10", 100, "daily"),
        CardLadderQuery("A", "Kobe Bryant Topps Chrome PSA 10", 100, "daily"),
        CardLadderQuery("A", "Stephen Curry Topps Chrome PSA 10", 100, "daily"),
        CardLadderQuery("A", "Stephen Curry Prizm PSA 10", 100, "daily"),
        CardLadderQuery("A", "Kevin Durant Topps Chrome PSA 10", 100, "daily"),
        CardLadderQuery("A", "Giannis Antetokounmpo Prizm PSA 10", 100, "daily"),
        CardLadderQuery("A", "Nikola Jokic Prizm PSA 10", 100, "daily"),
        CardLadderQuery("A", "Luka Doncic Prizm PSA 10", 100, "daily"),
        CardLadderQuery("A", "Victor Wembanyama Prizm PSA 10", 100, "daily"),
        CardLadderQuery("B", "Anthony Edwards Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Shai Gilgeous-Alexander Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Jayson Tatum Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Ja Morant Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Zion Williamson Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Paolo Banchero Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Chet Holmgren Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Tyrese Haliburton Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Jalen Brunson Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Devin Booker Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("C", "Cooper Flagg Bowman Chrome PSA 10", 50, "weekly"),
        CardLadderQuery("C", "Cooper Flagg Topps Chrome PSA 10", 50, "weekly"),
        CardLadderQuery("C", "Dylan Harper Bowman Chrome PSA 10", 50, "weekly"),
        CardLadderQuery("C", "Ace Bailey Bowman Chrome PSA 10", 50, "weekly"),
        CardLadderQuery("C", "VJ Edgecombe Topps Now PSA 10", 50, "weekly"),
        CardLadderQuery("C", "Kon Knueppel Bowman Chrome PSA 10", 50, "weekly"),
        CardLadderQuery("C", "Tre Johnson Bowman Chrome PSA 10", 50, "weekly"),
        CardLadderQuery("C", "Jeremiah Fears Bowman Chrome PSA 10", 50, "weekly"),
    ]


def next_searches(coverage: dict[str, int], limit: int = 10) -> list[CardLadderQuery]:
    tier_rank = {"A": 0, "B": 1, "C": 2, "D": 3}

    def priority(row: CardLadderQuery) -> tuple[int, float, int, str]:
        current_rows = coverage.get(row.query, 0)
        coverage_ratio = current_rows / row.target_rows
        return (tier_rank.get(row.tier, 99), coverage_ratio, current_rows, row.query)

    undercovered = [row for row in query_tiers() if coverage.get(row.query, 0) < row.target_rows]
    return sorted(undercovered, key=priority)[:limit]
