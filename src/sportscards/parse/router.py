"""Routing logic between regex and LLM parsers."""
from __future__ import annotations

import logging
from decimal import Decimal

from sportscards.parse.llm_parser import parse_title_llm
from sportscards.parse.regex_parser import parse_title as parse_title_regex
from sportscards.parse.schema import ParsedTitle

log = logging.getLogger(__name__)

REGEX_CONFIDENCE_FLOOR = Decimal("0.85")


def parse_title(title: str, *, allow_llm: bool = True) -> ParsedTitle:
    """Try regex first; fall back to DeepSeek if confidence is below floor."""
    regex_result = parse_title_regex(title)
    if regex_result.confidence >= REGEX_CONFIDENCE_FLOOR or not allow_llm:
        return regex_result
    try:
        llm_result = parse_title_llm(title)
    except Exception as e:
        log.warning("LLM fallback failed for %r: %s", title[:80], e)
        return regex_result
    if llm_result.confidence > regex_result.confidence:
        return llm_result
    return regex_result
