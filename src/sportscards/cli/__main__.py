"""sportscards CLI."""
from __future__ import annotations

import logging

import click


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@cli.group()
def seed() -> None:
    """Seed master data tables."""


@seed.command("players")
def seed_players_cmd() -> None:
    from sportscards.master.seed import seed_players

    n = seed_players()
    click.echo(f"seeded {n} players")


@seed.command("cards")
def seed_cards_cmd() -> None:
    from sportscards.master.seed import seed_cards

    n = seed_cards()
    click.echo(f"seeded {n} cards")


@cli.group()
def ingest() -> None:
    """Run ingestors."""


@ingest.command("ebay")
@click.option("--keywords", default=None, help="Optional keyword filter")
@click.option("--pages", default=10, type=int)
def ingest_ebay_cmd(keywords: str | None, pages: int) -> None:
    from sportscards.ingest.ebay_browse import ingest_sold

    n = ingest_sold(keywords=keywords, max_pages=pages)
    click.echo(f"ingested {n} new rows")


@cli.group()
def cardladder() -> None:
    """Card Ladder operations."""


@cardladder.command("import")
@click.argument("path", type=click.Path(exists=True))
def cardladder_import_cmd(path: str) -> None:
    from sportscards.ingest.cardladder import import_sales_csv

    raw, clean = import_sales_csv(path)
    click.echo(f"imported {raw} raw / {clean} clean from {path}")


@cli.group()
def auction() -> None:
    """Auction-house CSV import (Goldin/Heritage/Fanatics Collect)."""


@auction.command("import")
@click.argument("path", type=click.Path(exists=True))
@click.option("--house", required=True, type=click.Choice(["goldin", "heritage", "fanatics_collect"]))
def auction_import_cmd(path: str, house: str) -> None:
    from sportscards.ingest.auction_import import import_auction_csv

    n = import_auction_csv(path, house)
    click.echo(f"imported {n} rows from {house} export {path}")


@cli.command("parse")
@click.option("--batch", default=1000, type=int)
@click.option("--no-llm", is_flag=True)
def parse_cmd(batch: int, no_llm: bool) -> None:
    from sportscards.flows.parse_pending import parse_pending_flow

    stats = parse_pending_flow(batch_size=batch, allow_llm=not no_llm)
    click.echo(stats)


@cli.command("pop-snapshot")
def pop_snapshot_cmd() -> None:
    from sportscards.flows.daily_psa_pop import daily_psa_pop_flow

    n = daily_psa_pop_flow()
    click.echo(f"pop rows written: {n}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
