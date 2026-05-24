"""Seed loaders for player_master and card_master from YAML checklists."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sportscards.db.models import Card, Player
from sportscards.db.session import session_scope

log = logging.getLogger(__name__)

SEED_DIR = Path(__file__).parent / "seed_data"


def seed_players(path: Path | None = None) -> int:
    path = path or (SEED_DIR / "players_seed.yaml")
    data: list[dict[str, Any]] = yaml.safe_load(path.read_text())
    added = 0
    with session_scope() as s:
        for row in data:
            stmt = pg_insert(Player).values(**row).on_conflict_do_nothing(
                index_elements=["br_slug"]
            )
            res = s.execute(stmt)
            added += res.rowcount or 0
    log.info("seeded %d players", added)
    return added


def seed_cards(path: Path | None = None) -> int:
    path = path or (SEED_DIR / "cards_seed.yaml")
    data: list[dict[str, Any]] = yaml.safe_load(path.read_text())
    added = 0
    with session_scope() as s:
        # name → player_id
        rows = s.execute(select(Player.player_id, Player.name)).all()
        name_to_id: dict[str, int] = {r[1]: r[0] for r in rows}

        for row in data:
            player_name = row.pop("player")
            player_id = name_to_id.get(player_name)
            if player_id is None:
                log.warning("seed_cards: unknown player %r, skipping", player_name)
                continue
            row["player_id"] = player_id
            # Map yaml `set` -> ORM column attr `set_name`
            if "set" in row:
                row["set"] = row["set"]  # column literal name remains "set" in DB
            stmt = pg_insert(Card.__table__).values(**row).on_conflict_do_nothing(
                constraint="uq_card_master_identity"
            )
            res = s.execute(stmt)
            added += res.rowcount or 0
    log.info("seeded %d cards", added)
    return added
