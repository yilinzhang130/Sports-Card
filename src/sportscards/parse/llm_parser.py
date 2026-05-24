"""DeepSeek V4 Flash fallback parser.

OpenAI-compatible SDK; cheap per-call so we route all low-confidence regex
results through this rather than dropping them.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from openai import OpenAI

from sportscards.config import get_settings
from sportscards.parse.schema import ParsedTitle

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract structured fields from raw sports trading card listing titles.

Output a single JSON object with these keys (omit a key if unknown):
  year (int, e.g. 2018 for "2018-19"),
  manufacturer ("Panini" | "Topps" | "Upper Deck" | "Fleer"),
  set (e.g. "Prizm", "National Treasures", "Topps Chrome", "Donruss Optic"),
  card_number (string, no "#"),
  parallel (e.g. "Base", "Silver", "Gold /10", "Refractor", "Black 1/1"),
  player_name,
  is_rookie (bool),
  has_auto (bool),
  has_patch (bool),
  is_one_of_one (bool),
  slab_grader ("PSA" | "BGS" | "SGC" | "CGC"),
  slab_grade (float, e.g. 10 or 9.5),
  cert_number (string)

Only output the JSON object. No prose.
"""


def _client() -> OpenAI:
    s = get_settings()
    if not s.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")
    return OpenAI(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url)


def parse_title_llm(title: str) -> ParsedTitle:
    s = get_settings()
    resp = _client().chat.completions.create(
        model=s.deepseek_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": title},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        log.warning("LLM returned non-JSON: %s", content[:200])
        return ParsedTitle(method="llm", confidence=Decimal("0"))

    if "set" in data:
        data["set_name"] = data.pop("set")
    if "slab_grade" in data and data["slab_grade"] is not None:
        data["slab_grade"] = Decimal(str(data["slab_grade"]))

    parsed = ParsedTitle(method="llm", **data)
    # Heuristic LLM confidence: full if we got the core 4 fields.
    core = [parsed.year, parsed.manufacturer, parsed.set_name, parsed.card_number]
    parsed.confidence = Decimal("0.90") if all(core) else Decimal("0.60")
    return parsed
