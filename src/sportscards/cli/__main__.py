"""sportscards CLI."""

from __future__ import annotations

import logging
from datetime import UTC
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from datetime import datetime


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
    """Card Ladder agent-operated capture helpers."""


@cardladder.command("next")
@click.option("--limit", default=10, type=int, show_default=True)
def cardladder_next_cmd(limit: int) -> None:
    """Print the next Card Ladder capture URLs as JSON."""
    import json

    from sportscards.ingest.cardladder_batch import next_capture_plan

    click.echo(json.dumps(next_capture_plan(limit=limit), indent=2, default=str))


@cardladder.command("import-links")
@click.argument("path", type=click.Path(exists=True))
@click.option("--query", required=True, help="Card Ladder Sales History search query.")
def cardladder_import_links_cmd(path: str, query: str) -> None:
    """Import browser-accessibility links captured from a Card Ladder page."""
    import json
    from pathlib import Path

    from sportscards.ingest.cardladder_batch import captured_links_to_import_summary

    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, list):
        raise click.ClickException("links JSON must be a list of {description, value} objects")
    summary = captured_links_to_import_summary(query, payload)
    click.echo(json.dumps(summary, indent=2, default=str))


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


@cli.group()
def portfolio() -> None:
    """Portfolio construction and risk."""


@portfolio.command("plan")
@click.option("--aum", type=float, default=1_000_000.0)
@click.option(
    "--grading-arbitrage-pct",
    type=float,
    default=0.0,
    show_default=True,
    help="Opt-in grading-arbitrage sleeve as % of AUM (0–10; capped at 10).",
)
@click.option(
    "--tactical",
    is_flag=True,
    default=False,
    help="Apply tactical tilt by catalyst score within each sleeve.",
)
def portfolio_plan_cmd(aum: float, grading_arbitrage_pct: float, tactical: bool) -> None:
    """Print current target weights (anchor-only fallback if no factor data)."""
    import warnings as _w

    import pandas as pd
    from rich.console import Console
    from rich.table import Table

    from sportscards.db.session import session_scope
    from sportscards.portfolio.adapters import (
        load_anchors,
        load_catalyst_scores,
        load_mispricing,
        load_stardom,
    )
    from sportscards.portfolio.construction import (
        AllocationConfig,
        UniverseSnapshot,
        apply_overrides,
        build_portfolio,
    )

    now = pd.Timestamp.utcnow()
    catalyst_scores: dict[int, float] | None = None
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        with session_scope() as s:
            anchors = load_anchors(s)
            mispricing = load_mispricing(s, now)
            stardom = load_stardom(s, now)
            if tactical:
                ids: set[int] = set()
                if not anchors.empty:
                    ids.update(int(x) for x in anchors["card_id"].tolist())
                if mispricing is not None and not mispricing.empty:
                    ids.update(int(x) for x in mispricing["card_id"].tolist())
                if stardom is not None and not stardom.empty:
                    ids.update(int(x) for x in stardom["card_id"].tolist())
                catalyst_scores = load_catalyst_scores(s, sorted(ids), now.to_pydatetime())
            positions = build_portfolio(
                UniverseSnapshot(anchors_df=anchors, factor_df=mispricing, prospect_df=stardom),
                AllocationConfig(
                    total_aum_usd=aum,
                    grading_arbitrage_weight=grading_arbitrage_pct / 100.0,
                    tactical_tilt=tactical,
                ),
                catalyst_scores=catalyst_scores,
            )
            positions = apply_overrides(positions, s)

    console = Console()
    for w_ in caught:
        console.print(f"[yellow]warning:[/yellow] {w_.message}")
    has_overrides = any(p.is_override for p in positions)
    table = Table(title=f"Target portfolio (AUM ${aum:,.0f})")
    table.add_column("card_id", justify="right")
    table.add_column("sleeve")
    table.add_column("signal_source")
    table.add_column("weight %", justify="right")
    table.add_column("$ value", justify="right")
    if has_overrides:
        table.add_column("OVR")
    for p in sorted(positions, key=lambda x: (-abs(x.target_weight_pct), x.card_id)):
        row_vals = [
            str(p.card_id),
            p.sleeve,
            p.signal_source,
            f"{p.target_weight_pct * 100:.2f}",
            f"${p.target_usd_value:,.0f}",
        ]
        if has_overrides:
            row_vals.append("OVR" if p.is_override else "")
        table.add_row(*row_vals)
    console.print(table)
    total = sum(p.target_weight_pct for p in positions)
    console.print(f"[bold]total allocated:[/bold] {total * 100:.2f}%")


@cli.group()
def backtest() -> None:
    """Walk-forward backtesting."""


@backtest.command("run")
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--aum", type=float, default=1_000_000.0)
@click.option("--out", default=None, help="Output path prefix; .json and .md are written")
def backtest_run_cmd(start: str, end: str, aum: float, out: str | None) -> None:
    import json
    from datetime import date as _date
    from pathlib import Path

    import pandas as pd

    from sportscards.db.session import session_scope
    from sportscards.portfolio.adapters import (
        load_anchors,
        load_mispricing,
        load_price_panel,
        load_stardom,
    )
    from sportscards.portfolio.backtester import BacktestConfig, run_backtest
    from sportscards.portfolio.construction import (
        AllocationConfig,
        UniverseSnapshot,
    )

    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)

    with session_scope() as s:
        anchors = load_anchors(s)
        card_ids = anchors["card_id"].tolist() if not anchors.empty else []
        panel = load_price_panel(s, card_ids, pd.Timestamp(start_d), pd.Timestamp(end_d))

        def provider(as_of: pd.Timestamp) -> UniverseSnapshot:
            with session_scope() as s2:
                a = load_anchors(s2, as_of=as_of.to_pydatetime())
                m = load_mispricing(s2, as_of.to_pydatetime())
                p = load_stardom(s2, as_of.to_pydatetime())
            return UniverseSnapshot(anchors_df=a, factor_df=m, prospect_df=p)

        cfg = BacktestConfig(
            start=start_d,
            end=end_d,
            initial_aum_usd=aum,
            allocation=AllocationConfig(total_aum_usd=aum),
        )
        result = run_backtest(cfg, provider, panel)

    summary_json = result.summary
    md_lines = [
        f"# Backtest {start} → {end}",
        "",
        "## Summary",
        *[f"- **{k}**: {v}" for k, v in summary_json.items()],
        "",
        "## NAV (resampled monthly)",
        result.nav.resample("ME").last().to_frame("nav").to_markdown(),
    ]
    md = "\n".join(md_lines)

    out_path = Path(out) if out else Path(f"backtest_{start}_{end}")
    json_path = out_path.with_suffix(".json")
    md_path = out_path.with_suffix(".md")
    json_path.write_text(json.dumps(summary_json, indent=2, default=str))
    md_path.write_text(md)
    click.echo(f"wrote {json_path} and {md_path}")
    click.echo(json.dumps(summary_json, indent=2, default=str))


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
            str(r.name),
            str(r.year),
            str(r.set_name),
            str(r.parallel),
            f"{r.slab_grader} {r.slab_grade}",
            f"${float(r.price_usd):,.2f}",
            f"{float(r.predicted_log_price):.3f}",
            f"{float(r.residual):+.3f}",
        )
    console.print(table)


@cli.group()
def factor() -> None:
    """Factor panel (momentum + liquidity) commands."""


@factor.command("compute-panel")
@click.option(
    "--as-of",
    default=None,
    help="YYYY-MM-DD as-of date (default: today UTC).",
)
@click.option("--lookback", default=90, type=int, help="Trailing window in days.")
def factor_compute_panel_cmd(as_of: str | None, lookback: int) -> None:
    """Compute and persist factor_panel rows for the universe."""
    from datetime import UTC
    from datetime import date as _date
    from datetime import datetime as _datetime

    from sportscards.db.session import session_scope
    from sportscards.factors.factor_panel import persist_panel

    if as_of is None:
        as_of_dt = _datetime.now(tz=UTC)
    else:
        as_of_dt = _datetime.combine(_date.fromisoformat(as_of), _datetime.min.time())

    with session_scope() as s:
        n = persist_panel(s, as_of_dt, lookback_days=lookback)
    click.echo(f"factor_panel rows written: {n} (as_of={as_of_dt:%Y-%m-%d})")


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


@scouting.command("ingest-gleague")
@click.option(
    "--season",
    "seasons",
    multiple=True,
    type=str,
    help="G-League season(s) in 'YYYY-YY' form. Defaults to 2017-18 .. 2024-25.",
)
def scouting_ingest_gleague_cmd(seasons: tuple[str, ...]) -> None:
    from sportscards.scouting.nba.ingest_gleague import LiveGLeagueClient, ingest_season

    target = list(seasons) if seasons else [f"{y}-{str(y + 1)[-2:]}" for y in range(2017, 2025)]
    client = LiveGLeagueClient()
    for s in target:
        click.echo(f"ingesting G-League {s}…")
        ingest_season(s, client=client)
    click.echo(f"done ({len(target)} seasons)")


@scouting.command("ingest-euro")
@click.option(
    "--league",
    "leagues",
    multiple=True,
    type=click.Choice(["euroleague", "eurocup", "acb", "lnb", "lba", "nbl"]),
    help="European league(s) to pull. Defaults to all six.",
)
@click.option(
    "--season",
    "seasons",
    multiple=True,
    type=str,
    help="Season(s) in 'YYYY-YY' form. Defaults to 2017-18 .. 2024-25.",
)
def scouting_ingest_euro_cmd(leagues: tuple[str, ...], seasons: tuple[str, ...]) -> None:
    from sportscards.scouting.nba.ingest_euro import (
        LEAGUE_IDS,
        LiveEuroClient,
        ingest_league_season,
    )

    target_leagues = list(leagues) if leagues else list(LEAGUE_IDS.keys())
    target_seasons = (
        list(seasons) if seasons else [f"{y}-{str(y + 1)[-2:]}" for y in range(2017, 2025)]
    )
    client = LiveEuroClient()
    for lg in target_leagues:
        for s in target_seasons:
            click.echo(f"ingesting {lg} {s}…")
            ingest_league_season(lg, s, client=client)
    click.echo(f"done ({len(target_leagues)}x{len(target_seasons)} pulls)")


@scouting.command("refit-mle")
def scouting_refit_mle_cmd() -> None:
    """Refit Major League Equivalency multipliers from observed draft classes.

    TODO refit from data once 5+ draft classes with both college + euro
    samples are observed. Currently a stub that reports the cohort and warns
    if there isn't enough data — the seeded YAML continues to ship.
    """
    from sportscards.scouting.nba.ingest_prospects import build_unified_cohort

    prospects, _ = build_unified_cohort(range(2010, 2025))
    by_origin = prospects.groupby("prospect_origin").size()
    classes_per_origin = prospects.groupby("prospect_origin")["draft_year"].nunique()
    click.echo("observed cohort:")
    for origin, n in by_origin.items():
        k = int(classes_per_origin.get(origin, 0))
        click.echo(f"  {origin:12s} rows={int(n):5d} draft_classes={k}")
    if (classes_per_origin < 5).any():
        click.echo(
            "WARNING: <5 draft classes for some origins — keeping seeded "
            "MLE multipliers. Re-run after more data is ingested."
        )


@scouting.command("ingest-combine")
@click.option(
    "--year",
    "years",
    multiple=True,
    type=int,
    help="Draft year(s) to pull combine data for. Defaults to 2010-2024.",
)
def scouting_ingest_combine_cmd(years: tuple[int, ...]) -> None:
    from sportscards.scouting.nba.ingest_combine import LiveCombineClient
    from sportscards.scouting.nba.ingest_combine import ingest_year as ingest_combine_year

    target_years = list(years) if years else list(range(2010, 2025))
    client = LiveCombineClient()
    for y in target_years:
        click.echo(f"ingesting combine {y}…")
        try:
            ingest_combine_year(y, client=client)
        except Exception as e:  # pragma: no cover - network failure modes
            click.echo(f"  skipped {y}: {e}")
    click.echo(f"done ({len(target_years)} years attempted)")


@scouting.command("fit")
@click.option("--start", default=2010, type=int)
@click.option("--end", default=2024, type=int)
def scouting_fit_cmd(start: int, end: int) -> None:
    from sportscards.scouting.nba.features import build_feature_matrix
    from sportscards.scouting.nba.ingest_combine import load_combine_cohort
    from sportscards.scouting.nba.ingest_prospects import build_unified_cohort
    from sportscards.scouting.nba.prism import (
        concordance,
        predict_scores,
        save_model,
        train_pairwise_model,
    )

    years = range(start, end + 1)
    prospects, outcomes = build_unified_cohort(years)
    combine = load_combine_cohort(years)
    X, y, groups, _ = build_feature_matrix(prospects, outcomes, combine=combine)
    model = train_pairwise_model(X, y, groups)
    save_model(model)
    c = concordance(predict_scores(model, X), y.to_numpy(), groups.to_numpy())
    click.echo(f"trained: in-sample concordance={c:.3f}  (combine rows: {len(combine)})")


@scouting.command("score")
@click.option("--draft-year", type=int, required=False)
def scouting_score_cmd(draft_year: int | None) -> None:
    from sportscards.db.session import session_scope
    from sportscards.scouting.nba.features import build_feature_matrix
    from sportscards.scouting.nba.ingest_combine import load_combine_cohort
    from sportscards.scouting.nba.ingest_prospects import build_unified_cohort
    from sportscards.scouting.nba.prism import load_model, predict_scores
    from sportscards.scouting.nba.score import compute_stardom_premium, persist_scores

    years = range(draft_year, draft_year + 1) if draft_year else range(2010, 2025)
    prospects, outcomes = build_unified_cohort(years)
    combine = load_combine_cohort(years)
    X, _, groups, slugs = build_feature_matrix(prospects, outcomes, combine=combine)
    model = load_model()
    scores = predict_scores(model, X)
    df = compute_stardom_premium(slugs, groups, prospects["draft_pick"], scores)
    with session_scope() as s:
        n = persist_scores(s, df)
    click.echo(f"persisted {n} stardom scores ({len(df)} prospects scored)")


@scouting.command("ingest-current-ncaa")
@click.option("--season", required=True, help="Season label, e.g. '2025-26'.")
def scouting_ingest_current_ncaa_cmd(season: str) -> None:
    """Pull D-I per-100 stats for the in-progress NCAA season."""
    from sportscards.scouting.nba.ingest_bref import (
        LiveBRefClient,
        ingest_current_ncaa_season,
    )

    path = ingest_current_ncaa_season(season, client=LiveBRefClient())
    click.echo(f"wrote {path}")


@scouting.command("refresh-mock-drafts")
@click.option("--draft-year", required=True, type=int)
def scouting_refresh_mock_drafts_cmd(draft_year: int) -> None:
    """Snapshot the current mock-draft consensus across all sources."""
    from sportscards.scouting.nba.mock_draft import (
        LiveMockDraftClient,
        refresh_mock_drafts,
    )

    written = refresh_mock_drafts(draft_year, client=LiveMockDraftClient())
    click.echo(f"refreshed {len(written)} mock-draft snapshot(s)")


@scouting.command("score-class")
@click.option("--draft-year", required=True, type=int)
@click.option("--season", required=True, help="Season label, e.g. '2025-26'.")
@click.option(
    "--as-of",
    type=click.DateTime(["%Y-%m-%d"]),
    default=None,
    help="Snapshot date; defaults to today.",
)
def scouting_score_class_cmd(draft_year: int, season: str, as_of: datetime | None) -> None:
    """Forward-looking score for a draft class; writes ``prospect_forecast``."""
    from sportscards.db.session import session_scope
    from sportscards.scouting.nba.score_undrafted import score_current_class

    as_of_date = as_of.date() if as_of is not None else None
    with session_scope() as s:
        df = score_current_class(
            draft_year=draft_year,
            season=season,
            as_of=as_of_date,
            session=s,
        )
    click.echo(
        f"scored {len(df)} prospects for draft_year={draft_year} "
        f"({df['premium'].notna().sum()} with mock consensus)"
    )


@scouting.command("top-prospects")
@click.option("--draft-year", required=True, type=int)
@click.option("--limit", default=30, type=int)
def scouting_top_prospects_cmd(draft_year: int, limit: int) -> None:
    """Print the top-N prospects from the latest forecast snapshot."""
    from sqlalchemy import select

    from sportscards.db.models import ProspectForecast
    from sportscards.db.session import session_scope
    from sportscards.scouting.nba.prism import MODEL_VERSION

    with session_scope() as s:
        latest_as_of = s.execute(
            select(func_max_as_of_date())
            .where(ProspectForecast.draft_year == draft_year)
            .where(ProspectForecast.model_version == MODEL_VERSION)
        ).scalar_one_or_none()
        if latest_as_of is None:
            click.echo(
                f"no prospect_forecast rows for draft_year={draft_year}; "
                f"run `sportscards scouting score-class --draft-year {draft_year}` first."
            )
            return
        rows = (
            s.execute(
                select(ProspectForecast)
                .where(ProspectForecast.draft_year == draft_year)
                .where(ProspectForecast.model_version == MODEL_VERSION)
                .where(ProspectForecast.as_of_date == latest_as_of)
                .where(ProspectForecast.premium.is_not(None))
                .order_by(ProspectForecast.premium.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title=f"Top {len(rows)} prospects — draft_year={draft_year} as_of={latest_as_of}")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Class")
    table.add_column("Premium", justify="right")
    table.add_column("Pairwise", justify="right")
    table.add_column("Consensus", justify="right")
    table.add_column("Sources", justify="right")
    for i, r in enumerate(rows, start=1):
        table.add_row(
            str(i),
            r.name,
            "FR"
            if r.is_underclassman and r.years_until_draft == 2
            else "SO"
            if r.is_underclassman
            else "Upper",
            f"{float(r.premium):+.3f}" if r.premium is not None else "—",
            f"{float(r.pairwise_score):+.3f}" if r.pairwise_score is not None else "—",
            f"{float(r.consensus_rank):.1f}" if r.consensus_rank is not None else "—",
            str(r.sources_count or "—"),
        )
    console.print(table)


def func_max_as_of_date() -> Any:
    """Indirection — keeps the heavy ``func`` import out of the import-time
    surface of this module's CLI loader. Returns ``MAX(as_of_date)`` over
    ``prospect_forecast`` (caller chains its own WHERE).
    """
    from sqlalchemy import func

    from sportscards.db.models import ProspectForecast

    return func.max(ProspectForecast.as_of_date)


@cli.group()
def index() -> None:
    """Repeat-sales index construction."""


@index.command("build")
@click.option("--bucket", default="weekly", type=click.Choice(["weekly", "monthly"]))
@click.option(
    "--grade-tier",
    "grade_tiers",
    multiple=True,
    type=click.Choice(["PSA10", "PSA9", "PSA8", "lower", "all"]),
    help="Restrict to one or more grade tiers (default: all four).",
)
@click.option(
    "--era",
    "eras",
    multiple=True,
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
        sport=sport,
        bucket=bucket,
        grade_tiers=tiers,
        eras=era_list,
        replace=replace,
    )
    for key, n in stats.items():
        click.echo(f"{key}: {n} rows")


@index.command("seed-synthetic")
@click.option("--certs", default=2000, type=int)
@click.option("--weeks", default=300, type=int)
@click.option("--seed", default=42, type=int)
@click.option(
    "--card-id", default=1, type=int, help="card_master.card_id to attach all synthetic tx to."
)
def index_seed_synthetic_cmd(certs: int, weeks: int, seed: int, card_id: int) -> None:
    """Seed tx_raw + tx_clean with synthetic cert-tagged repeat sales for local dev."""
    from sportscards.factors.index_build import seed_synthetic_tx

    n = seed_synthetic_tx(n_certs=certs, weeks=weeks, seed=seed, card_id=card_id)
    click.echo(f"seeded {n} synthetic tx_clean rows")


@cli.group()
def events() -> None:
    """NBA catalyst event ingestors."""


@events.command("refresh-injuries")
def events_refresh_injuries_cmd() -> None:
    """Pull the latest NBA injury report and write status-change events."""
    from datetime import date as _date

    from sportscards.db.session import session_scope
    from sportscards.events.injuries import LiveInjuryClient, ingest_injuries

    with session_scope() as s:
        n = ingest_injuries(s, client=LiveInjuryClient(), as_of=_date.today())
    click.echo(f"wrote {n} injury events")


@events.command("refresh-schedule")
@click.option("--season", default=None, help="Season string (e.g. '2025-26'); default = current")
def events_refresh_schedule_cmd(season: str | None) -> None:
    """Pull NBA schedule and write playoff/finals win events."""
    from sportscards.db.session import session_scope
    from sportscards.events.schedule import LiveScheduleClient, ingest_schedule
    from sportscards.flows.daily_events import _current_season

    s_str = season or _current_season()
    with session_scope() as s:
        n = ingest_schedule(s, client=LiveScheduleClient(), season=s_str)
    click.echo(f"wrote {n} schedule events")


@events.command("refresh-awards")
@click.option("--season", default=None, help="Season string; default = current")
def events_refresh_awards_cmd(season: str | None) -> None:
    """Pull NBA awards and write award events."""
    from sportscards.db.session import session_scope
    from sportscards.events.awards import LiveAwardsClient, ingest_awards
    from sportscards.flows.daily_events import _current_season

    s_str = season or _current_season()
    with session_scope() as s:
        n = ingest_awards(s, client=LiveAwardsClient(), season=s_str)
    click.echo(f"wrote {n} award events")


@events.command("refresh-transactions")
@click.option("--since-days", default=7, type=int)
def events_refresh_transactions_cmd(since_days: int) -> None:
    """Pull recent NBA transactions and write call-up/two-way events."""
    from datetime import date as _date
    from datetime import timedelta as _td

    from sportscards.db.session import session_scope
    from sportscards.events.transactions import LiveTransactionsClient, ingest_transactions

    since = _date.today() - _td(days=since_days)
    with session_scope() as s:
        n = ingest_transactions(s, client=LiveTransactionsClient(), since=since)
    click.echo(f"wrote {n} transaction events")


@cli.command("dashboard")
def dashboard_cmd() -> None:
    """Launch the Streamlit dashboard (Trader Console)."""
    import os
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    app = repo_root / "reports" / "app" / "Home.py"
    # The multi-page app imports `reports.app._components.*`, which means the
    # repo root must be on PYTHONPATH so `reports` resolves as a top-level
    # package. Streamlit doesn't add it automatically.
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(repo_root), env.get("PYTHONPATH", "")]))
    subprocess.run(["streamlit", "run", str(app)], env=env, check=False)


@cli.command("letter")
@click.option("--month", required=True, help="Month in YYYY-MM format")
def letter_cmd(month: str) -> None:
    """Render the monthly investor letter to letters/YYYY-MM.md."""
    from sportscards.reports.render import render_monthly_letter

    out = render_monthly_letter(month)
    click.echo(f"wrote {out}")


@cli.group()
def ev() -> None:
    """Grading-EV optionality model."""


@ev.command("compute")
@click.option("--as-of", default=None, help="ISO date; default today (UTC)")
@click.option("--grade-tier", default="value_bulk")
@click.option("--apply-trend-adjustment", is_flag=True, default=False)
def ev_compute_cmd(as_of: str | None, grade_tier: str, apply_trend_adjustment: bool) -> None:
    from datetime import datetime

    from sportscards.flows.daily_grading_ev import daily_grading_ev_flow

    parsed_as_of = None
    if as_of is not None:
        dt = datetime.fromisoformat(as_of)
        parsed_as_of = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    n = daily_grading_ev_flow(
        grade_tier=grade_tier,
        apply_trend_adjustment=apply_trend_adjustment,
        as_of=parsed_as_of,
    )
    click.echo(f"wrote {n} grading_ev rows")


@ev.command("top")
@click.option("--limit", default=20, type=int)
@click.option("--grade-tier", default="value_bulk")
@click.option("--min-ev-per-dollar", default=0.15, type=float)
def ev_top_cmd(limit: int, grade_tier: str, min_ev_per_dollar: float) -> None:
    from datetime import datetime
    from decimal import Decimal

    from sportscards.db.session import session_scope
    from sportscards.factors.grading_ev import rank_grading_candidates

    with session_scope() as s:
        df = rank_grading_candidates(
            s,
            as_of=datetime.now(tz=UTC),
            grade_tier=grade_tier,
            min_ev_per_dollar=Decimal(str(min_ev_per_dollar)),
        )
    if df.empty:
        click.echo("no positive-EV candidates")
        return
    for _, row in df.head(limit).iterrows():
        click.echo(
            f"card_id={int(row.card_id):>6} ev=${row.ev:>8.2f} "
            f"ev/$={row.ev_per_dollar:>5.2f} gem={row.gem_rate:.2f} "
            f"raw=${row.raw_price:.2f} (n={int(row.sample_size)})"
        )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
