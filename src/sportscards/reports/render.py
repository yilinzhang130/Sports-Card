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
]
