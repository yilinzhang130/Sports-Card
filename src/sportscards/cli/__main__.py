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
@click.option(
    "--house", required=True, type=click.Choice(["goldin", "heritage", "fanatics_collect"])
)
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


@cli.group()
def psa() -> None:
    """PSA spec-id helpers."""


@psa.command("lookup-cert")
@click.argument("cert_number")
def psa_lookup_cert_cmd(cert_number: str) -> None:
    """Look up a PSA cert and print its SpecID + description (1 API call)."""
    import json

    from sportscards.ingest.psa_api import PsaClient

    payload = PsaClient().get_cert(cert_number)
    click.echo(json.dumps(payload, indent=2, default=str))


@psa.command("template")
def psa_template_cmd() -> None:
    """Print a psa_priority.yaml template seeded from card_master.

    Pipe to file:
        sportscards psa template > src/sportscards/master/seed_data/psa_priority.yaml
    """
    from sqlalchemy import select

    from sportscards.db.models import Card, Player
    from sportscards.db.session import session_scope

    with session_scope() as s:
        rows = s.execute(
            select(
                Card.card_id,
                Card.year,
                Card.set_name,
                Card.parallel,
                Card.card_number,
                Player.name,
            )
            .join(Player)
            .order_by(Card.year.desc(), Card.set_name)
        ).all()

    click.echo("# Daily PSA pop snapshot priority queue.")
    click.echo("# Fill psa_spec_id by running: sportscards psa lookup-cert <any cert of this card>")
    click.echo("# Then pull SpecID from the response.")
    for r in rows:
        comment = f"{r.year} {r.set_name} {r.parallel} #{r.card_number} {r.name}"
        click.echo(f'- {{card_id: {r.card_id}, psa_spec_id: "TBD"}}  # {comment}')


@psa.command("map")
@click.argument("card_id", type=int)
@click.argument("spec_id")
def psa_map_cmd(card_id: int, spec_id: str) -> None:
    """Append a (card_id → psa_spec_id) entry to psa_priority.yaml."""
    from pathlib import Path

    import yaml

    path = Path(__file__).parent.parent / "master" / "seed_data" / "psa_priority.yaml"
    data = yaml.safe_load(path.read_text()) or []
    if not isinstance(data, list):
        data = []
    data = [e for e in data if e.get("card_id") != card_id]
    data.append({"card_id": card_id, "psa_spec_id": spec_id})
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    click.echo(f"mapped card_id={card_id} → spec_id={spec_id}")


@cli.command("deploy")
def deploy_cmd() -> None:
    """Apply Prefect deployments defined in prefect.yaml."""
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    prefect_file = repo_root / "prefect.yaml"
    if not prefect_file.exists():
        raise click.ClickException(
            f"prefect.yaml not found at {prefect_file}. "
            "Run `sportscards deploy` from a source checkout."
        )
    subprocess.run(
        ["prefect", "deploy", "--prefect-file", str(prefect_file), "--all"],
        cwd=repo_root,
        check=True,
    )


@cli.group()
def model() -> None:
    """Hedonic fair-value model commands."""


@model.command("fit-hedonic")
@click.option("--train-end", default=None, help="YYYY-MM-DD cutoff for training data")
@click.option("--synthetic", is_flag=True, help="Generate synthetic tx data first (writes to DB)")
@click.option("--n-trials", default=20, type=int)
def fit_hedonic_cmd(train_end: str | None, synthetic: bool, n_trials: int) -> None:
    from datetime import date as _date

    from sportscards.db.session import session_scope
    from sportscards.factors.features import build_features
    from sportscards.factors.hedonic import fit, persist_residuals, predict, save_model
    from sportscards.factors.synthetic_data import generate_synthetic_transactions

    with session_scope() as s:
        if synthetic:
            n = generate_synthetic_transactions(s)
            click.echo(f"generated {n} synthetic tx rows")
        df = build_features(s)
        click.echo(f"feature matrix: {len(df)} rows")
        if df.empty:
            click.echo("no rows — aborting")
            return
        te = _date.fromisoformat(train_end) if train_end else None
        model_obj, encoder, metrics = fit(df, train_end=te, n_trials=n_trials)
        save_model(model_obj, encoder, metrics)
        pred = predict(model_obj, encoder, df)
        written = persist_residuals(s, df, pred)
        click.echo(
            f"OOS MAE: {metrics['oos_mae']:.4f}  "
            f"R²: {metrics['oos_r2']:.3f}  residuals written: {written}"
        )


@model.command("rank-mispricing")
@click.option("--top", default=20, type=int)
def rank_mispricing_cmd(top: int) -> None:
    from rich.console import Console
    from rich.table import Table
    from sqlalchemy import select

    from sportscards.db.models import Card, Player, TxClean, TxMispricing
    from sportscards.db.session import session_scope

    with session_scope() as s:
        stmt = (
            select(
                TxMispricing.residual,
                TxMispricing.predicted_log_price,
                TxClean.price_usd,
                TxClean.sold_at,
                TxClean.slab_grader,
                TxClean.slab_grade,
                Card.year,
                Card.set_name,
                Card.parallel,
                Player.name,
            )
            .join(TxClean, TxClean.tx_id == TxMispricing.tx_id)
            .join(Card, Card.card_id == TxClean.card_id)
            .join(Player, Player.player_id == Card.player_id)
        )
        rows = s.execute(stmt).all()

    rows = sorted(rows, key=lambda r: abs(float(r.residual)), reverse=True)[:top]

    console = Console()
    table = Table(title=f"Top {top} hedonic mispricings (|residual|)")
    for col in ("player", "year", "set", "parallel", "grade", "price_usd", "pred_log", "residual"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r.name), str(r.year), str(r.set_name), str(r.parallel),
            f"{r.slab_grader} {r.slab_grade}",
            f"${float(r.price_usd):,.2f}",
            f"{float(r.predicted_log_price):.3f}",
            f"{float(r.residual):+.3f}",
        )
    console.print(table)


@cli.group()
def scouting() -> None:
    """NBA prospect scouting model (PRISM-style pairwise CatBoost)."""


@scouting.command("ingest-nba")
@click.option(
    "--year",
    "years",
    multiple=True,
    type=int,
    help="Draft year(s) to pull. Defaults to 2010-2024.",
)
def scouting_ingest_nba_cmd(years: tuple[int, ...]) -> None:
    from sportscards.scouting.nba.ingest_bref import LiveBRefClient, ingest_year

    target_years = list(years) if years else list(range(2010, 2025))
    client = LiveBRefClient()
    for y in target_years:
        click.echo(f"ingesting {y}…")
        ingest_year(y, client=client)
    click.echo(f"done ({len(target_years)} years)")


@scouting.command("fit")
@click.option("--start", default=2010, type=int)
@click.option("--end", default=2024, type=int)
def scouting_fit_cmd(start: int, end: int) -> None:
    from sportscards.scouting.nba.features import build_feature_matrix
    from sportscards.scouting.nba.ingest_bref import load_cohort
    from sportscards.scouting.nba.prism import (
        concordance,
        predict_scores,
        save_model,
        train_pairwise_model,
    )

    prospects, outcomes = load_cohort(range(start, end + 1))
    X, y, groups, _ = build_feature_matrix(prospects, outcomes)
    model = train_pairwise_model(X, y, groups)
    save_model(model)
    c = concordance(predict_scores(model, X), y.to_numpy(), groups.to_numpy())
    click.echo(f"trained: in-sample concordance={c:.3f}")


@scouting.command("score")
@click.option("--draft-year", type=int, required=False)
def scouting_score_cmd(draft_year: int | None) -> None:
    from sportscards.db.session import session_scope
    from sportscards.scouting.nba.features import build_feature_matrix
    from sportscards.scouting.nba.ingest_bref import load_cohort
    from sportscards.scouting.nba.prism import load_model, predict_scores
    from sportscards.scouting.nba.score import compute_stardom_premium, persist_scores

    years = range(draft_year, draft_year + 1) if draft_year else range(2010, 2025)
    prospects, outcomes = load_cohort(years)
    X, _, groups, slugs = build_feature_matrix(prospects, outcomes)
    model = load_model()
    scores = predict_scores(model, X)
    df = compute_stardom_premium(slugs, groups, prospects["draft_pick"], scores)
    with session_scope() as s:
        n = persist_scores(s, df)
    click.echo(f"persisted {n} stardom scores ({len(df)} prospects scored)")


@cli.group()
def index() -> None:
    """Repeat-sales index construction."""


@index.command("build")
@click.option("--bucket", default="weekly", type=click.Choice(["weekly", "monthly"]))
@click.option(
    "--grade-tier", "grade_tiers", multiple=True,
    type=click.Choice(["PSA10", "PSA9", "PSA8", "lower", "all"]),
    help="Restrict to one or more grade tiers (default: all four).",
)
@click.option(
    "--era", "eras", multiple=True,
    type=click.Choice(["modern", "vintage", "all"]),
    help="Restrict to one or more eras (default: modern + vintage).",
)
@click.option("--sport", default="NBA")
@click.option("--replace", is_flag=True, help="Delete prior rows for the same partition first.")
def index_build_cmd(
    bucket: str,
    grade_tiers: tuple[str, ...],
    eras: tuple[str, ...],
    sport: str,
    replace: bool,
) -> None:
    from sportscards.factors.index_build import build_and_persist

    tiers = list(grade_tiers) or ["PSA10", "PSA9", "PSA8", "lower"]
    era_list = list(eras) or ["modern", "vintage"]
    stats = build_and_persist(
        sport=sport, bucket=bucket, grade_tiers=tiers, eras=era_list, replace=replace,
    )
    for key, n in stats.items():
        click.echo(f"{key}: {n} rows")


@index.command("seed-synthetic")
@click.option("--certs", default=2000, type=int)
@click.option("--weeks", default=300, type=int)
@click.option("--seed", default=42, type=int)
@click.option("--card-id", default=1, type=int,
              help="card_master.card_id to attach all synthetic tx to.")
def index_seed_synthetic_cmd(certs: int, weeks: int, seed: int, card_id: int) -> None:
    """Seed tx_raw + tx_clean with synthetic cert-tagged repeat sales for local dev."""
    from sportscards.factors.index_build import seed_synthetic_tx

    n = seed_synthetic_tx(n_certs=certs, weeks=weeks, seed=seed, card_id=card_id)
    click.echo(f"seeded {n} synthetic tx_clean rows")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
