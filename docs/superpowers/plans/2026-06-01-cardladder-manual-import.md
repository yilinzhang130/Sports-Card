# Card Ladder Manual Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a localhost-only Card Ladder paste/import and quick-sale-entry workflow that converts manually copied Sales History rows into local `tx_raw` and best-effort `tx_clean` rows.

**Architecture:** Put all parsing, normalization, duplicate key generation, and DB writes in `src/sportscards/ingest/cardladder_manual.py`; keep Streamlit UI thin in the Ingest page. Reuse the existing title parser and `parse_pending._resolve_card_id` so manual imports feed the same downstream tables as eBay and auction imports.

**Tech Stack:** Python 3.14, SQLAlchemy ORM/Core, Streamlit, pandas, pytest, existing `sportscards` parser and DB session helpers.

---

## File Structure

- Create `src/sportscards/ingest/cardladder_manual.py`
  - `parse_cardladder_text(text: str) -> list[CardLadderSale]`
  - `build_quick_sale(platform: str, raw_title: str, price_usd: Decimal, sold_date: date, listing_type: str | None, verified: bool) -> CardLadderSale`
  - `import_cardladder_sales(sales: Sequence[CardLadderSale], allow_clean: bool = True) -> ImportResult`
  - stable duplicate key based on platform/title/date/price/listing type/raw text
- Modify `reports/app/_components/actions.py`
  - Add job-runnable `cardladder_manual_import(rows: list[dict[str, Any]]) -> dict[str, Any]`
- Modify `reports/app/pages/3_📥_Ingest.py`
  - Add Card Ladder Paste Import expander with textarea, parse preview, import button
  - Add Quick Sale Entry expander with single-sale form
- Create `tests/test_cardladder_manual_import.py`
  - Parser coverage for 10-15 realistic copied rows
  - Duplicate handling smoke test against migrated SQLite DB
- Modify `tests/test_ingest_page.py`
  - Keep render smoke test passing with the new UI
- Modify `docs/trader_console.md`
  - Document manual paste workflow and constraints

## Parsing Contract

Use this dataclass in `cardladder_manual.py`:

```python
@dataclass(frozen=True)
class CardLadderSale:
    platform: str
    raw_title: str
    price_usd: Decimal
    sold_at: datetime
    listing_type: str | None
    verified: bool
    raw_text: str
    warnings: Sequence[str] = ()
```

Use this result dataclass:

```python
@dataclass(frozen=True)
class ImportResult:
    inserted_raw: int
    inserted_clean: int
    skipped_duplicates: int
    failed_clean: int
    errors: Sequence[str] = ()
```

Supported platform tokens should include exact uppercase matching for:

```python
PLATFORMS = (
    "FANATICS WEEKLY",
    "FANATICS",
    "CARD HOBBY",
    "MY SLABS",
    "HERITAGE",
    "GOLDIN",
    "EBAY",
    "ALT",
)
```

Supported listing labels should include:

```python
LISTING_TYPES = (
    "Best Offer",
    "Auction",
    "Buy Now",
    "Fixed Price",
)
```

The row parser should normalize whitespace, locate prices like `Price $105.00`, locate the last `Mon D, YYYY` date, detect `verified` case-insensitively, then treat the text between platform and `Price` as title after removing platform/listing prefixes and trailing listing labels.

## Task 1: Parser Tests First

**Files:**
- Create: `tests/test_cardladder_manual_import.py`
- Create later: `src/sportscards/ingest/cardladder_manual.py`

- [ ] **Step 1: Write parser sample tests**

Add tests with these exact sample strings:

```python
SAMPLES = [
    "FANATICS BUY NOW 2025 Topps Now Tyrese Maxey VJ Edgecombe ROOKIE #32 PSA 10 GEM MINT Price $105.00 Best Offer Jun 1, 2026",
    "GOLDIN 2018-19 Panini National Treasures Rookie Patch Autograph (RPA) #132 Jalen Brunson PSA 10 Price $13,420.00 verified Auction Apr 10, 2026",
    "EBAY 2018-19 Panini Prizm Luka Doncic #280 Silver PSA 10 Rookie RC Price $5,954.02 Auction Nov 30, 2025",
    "ALT 2003-04 Topps Chrome Refractor LeBron James #111 PSA 9 Price $7,200.00 Fixed Price May 2, 2026",
    "HERITAGE 1996-97 Topps Chrome Kobe Bryant #138 PSA 10 Price $9,500.00 verified Auction Jan 15, 2026",
    "MY SLABS 2023-24 Panini Prizm Victor Wembanyama #136 Silver PSA 10 RC Price $1,875.00 Buy Now Mar 3, 2026",
    "CARD HOBBY 2020-21 Panini Prizm Anthony Edwards #258 PSA 10 新秀 Price $520.00 Auction Feb 12, 2026",
    "FANATICS WEEKLY 2019-20 Panini Prizm Zion Williamson #248 Silver BGS 9.5 Price $1,250.00 verified Best Offer Dec 8, 2025",
]
```

Test expectations:

```python
def test_parse_cardladder_text_extracts_core_fields():
    rows = parse_cardladder_text("\n".join(SAMPLES))
    assert len(rows) == len(SAMPLES)
    assert rows[0].platform == "FANATICS"
    assert rows[0].price_usd == Decimal("105.00")
    assert rows[0].sold_at.date().isoformat() == "2026-06-01"
    assert rows[1].verified is True
    assert rows[1].listing_type == "Auction"
    assert rows[1].price_usd == Decimal("13420.00")
    assert rows[6].platform == "CARD HOBBY"
    assert "新秀" in rows[6].raw_title
```

- [ ] **Step 2: Add multiline row test**

```python
def test_parse_cardladder_text_joins_wrapped_rows():
    text = '''
    GOLDIN
    2018-19 Panini National Treasures Rookie Patch Autograph (RPA)
    #132 Jalen Brunson PSA 10
    Price $13,420.00 verified Auction Apr 10, 2026
    EBAY 2018-19 Panini Prizm Luka Doncic #280 PSA 10 Price $4,000.00 Auction Apr 11, 2026
    '''
    rows = parse_cardladder_text(text)
    assert len(rows) == 2
    assert rows[0].platform == "GOLDIN"
    assert rows[0].raw_title.startswith("2018-19 Panini National Treasures")
    assert rows[1].platform == "EBAY"
```

- [ ] **Step 3: Run failing parser tests**

Run:

```bash
uv run pytest tests/test_cardladder_manual_import.py -q
```

Expected: import error because `sportscards.ingest.cardladder_manual` does not exist yet.

## Task 2: Parser Implementation

**Files:**
- Create: `src/sportscards/ingest/cardladder_manual.py`
- Test: `tests/test_cardladder_manual_import.py`

- [ ] **Step 1: Implement parser dataclasses and regexes**

Implement:

```python
PRICE_RE = re.compile(r"\bPrice\s+\$([0-9][0-9,]*(?:\.[0-9]{2})?)\b", re.I)
DATE_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b")
```

`parse_cardladder_text` should:

1. Normalize lines by stripping and dropping blanks.
2. Build row chunks by starting a new chunk when a line starts with a supported platform and the current chunk already contains a price/date.
3. Parse each chunk with `_parse_row`.
4. Skip chunks that cannot produce price/date/platform, but preserve a warning in rows where optional listing/title cleanup is imperfect.

- [ ] **Step 2: Implement `_parse_row`**

Concrete behavior:

```python
row_text = " ".join(chunk.split())
platform = _extract_platform(row_text)
price = Decimal(price_match.group(1).replace(",", ""))
sold_at = datetime.strptime(date_match.group(0), "%b %d, %Y").replace(tzinfo=UTC)
verified = bool(re.search(r"\bverified\b", row_text, re.I))
listing_type = _extract_listing_type(row_text)
raw_title = _extract_title(row_text, platform, price_match.start())
```

For title cleanup, remove leading platform and repeated listing prefixes (`BUY NOW`, `AUCTION`, `FIXED PRICE`) only at the start. Do not remove title words elsewhere.

- [ ] **Step 3: Run parser tests**

Run:

```bash
uv run pytest tests/test_cardladder_manual_import.py -q
```

Expected: parser tests pass except DB duplicate test, if that test has already been added.

## Task 3: DB Import and Duplicate Handling

**Files:**
- Modify: `src/sportscards/ingest/cardladder_manual.py`
- Modify: `tests/test_cardladder_manual_import.py`

- [ ] **Step 1: Add duplicate/import tests**

Add a migrated DB test:

```python
def test_import_cardladder_sales_dedupes_and_writes_clean_best_effort(migrated_db):
    sale = parse_cardladder_text(
        "EBAY 2018-19 Panini Prizm Luka Doncic #280 Silver PSA 10 Rookie RC Price $5,954.02 Auction Nov 30, 2025"
    )[0]
    first = import_cardladder_sales([sale])
    second = import_cardladder_sales([sale])
    assert first.inserted_raw == 1
    assert second.inserted_raw == 0
    assert second.skipped_duplicates == 1
```

- [ ] **Step 2: Implement stable external ID**

Use:

```python
def stable_external_id(sale: CardLadderSale) -> str:
    payload = "|".join([
        sale.platform.upper(),
        sale.raw_title.casefold(),
        sale.sold_at.date().isoformat(),
        str(sale.price_usd.quantize(Decimal("0.01"))),
        (sale.listing_type or "").casefold(),
        sale.raw_text.casefold(),
    ])
    return "clm-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
```

- [ ] **Step 3: Implement raw insert**

Insert `TxRaw` with:

```python
source="cardladder_manual"
raw_title=sale.raw_title
raw_price=sale.price_usd
raw_currency="USD"
sold_at=sale.sold_at
external_id=stable_external_id(sale)
raw_json={
    "platform": sale.platform,
    "listing_type": sale.listing_type,
    "verified": sale.verified,
    "raw_text": sale.raw_text,
    "warnings": list(sale.warnings),
}
```

Use PostgreSQL insert with `on_conflict_do_nothing(constraint="uq_tx_raw_source_extid")` only when the dialect is PostgreSQL. For SQLite tests, pre-check `TxRaw` by `source/external_id` before insert to avoid dialect mismatch.

- [ ] **Step 4: Implement best-effort clean insert**

After raw insert succeeds, run:

```python
parsed = parse_title(sale.raw_title, allow_llm=False)
card_id = _resolve_card_id(session, parsed)
```

If parser confidence is at least `Decimal("0.5")`, insert `TxClean` with:

```python
raw_id=raw.raw_id
card_id=card_id
slab_grader=parsed.slab_grader
slab_grade=parsed.slab_grade
cert_number=parsed.cert_number
price_usd=sale.price_usd
sold_at=sale.sold_at
parser_confidence=parsed.confidence
parser_method=f"cardladder_{parsed.method}"[:16]
```

If clean insert is skipped because of low confidence, count `failed_clean += 1` and still keep `tx_raw`.

- [ ] **Step 5: Run DB tests**

Run:

```bash
uv run pytest tests/test_cardladder_manual_import.py -q
```

Expected: all tests pass.

## Task 4: Streamlit Action Wrapper

**Files:**
- Modify: `reports/app/_components/actions.py`
- Test: `tests/test_cardladder_manual_import.py`

- [ ] **Step 1: Add action wrapper**

Add:

```python
def cardladder_manual_import(*, rows: list[dict[str, Any]]) -> dict[str, Any]:
    from sportscards.ingest.cardladder_manual import CardLadderSale, import_cardladder_sales

    sales = [CardLadderSale.from_dict(row) for row in rows]
    result = import_cardladder_sales(sales)
    return {
        "inserted_raw": result.inserted_raw,
        "inserted_clean": result.inserted_clean,
        "skipped_duplicates": result.skipped_duplicates,
        "failed_clean": result.failed_clean,
        "errors": list(result.errors),
    }
```

Add `CardLadderSale.to_dict()` and `CardLadderSale.from_dict()` helpers in the importer so Streamlit session state/job payloads stay JSON-serializable.

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest tests/test_cardladder_manual_import.py -q
```

Expected: pass.

## Task 5: Ingest Page UI

**Files:**
- Modify: `reports/app/pages/3_📥_Ingest.py`
- Modify: `tests/test_ingest_page.py`

- [ ] **Step 1: Add paste import UI**

Add an expander after Auction CSV import:

```python
with st.expander("Card Ladder paste import"):
    pasted = st.text_area("Paste Card Ladder Sales History rows", height=220)
    if st.button("Parse Preview", disabled=not pasted.strip()):
        sales = parse_cardladder_text(pasted)
        st.session_state["cardladder_preview"] = [s.to_dict() for s in sales]
    preview = st.session_state.get("cardladder_preview", [])
    if preview:
        st.dataframe(pd.DataFrame(preview), use_container_width=True)
        ok = confirm_toggle("confirm_cardladder_import")
        if st.button("Import confirmed rows", disabled=not ok):
            run_id = submit_job(
                "cardladder_manual_import",
                actions.cardladder_manual_import,
                params={"rows": len(preview)},
                kwargs={"rows": preview},
            )
            st.session_state["job_cardladder"] = run_id
            st.rerun()
    _render_status("job_cardladder")
```

Import `pandas as pd` and `parse_cardladder_text` at the top.

- [ ] **Step 2: Add quick sale entry UI**

Add another expander:

```python
with st.expander("Quick sale entry"):
    with st.form("form_cardladder_quick_sale"):
        platform = st.selectbox("Platform", ["EBAY", "FANATICS", "GOLDIN", "ALT", "CARD HOBBY", "HERITAGE", "MY SLABS"])
        title = st.text_input("Title")
        price = st.number_input("Price USD", min_value=0.01, step=1.0)
        sold_date = st.date_input("Sold date")
        listing_type = st.selectbox("Listing type", ["Auction", "Best Offer", "Buy Now", "Fixed Price"])
        verified = st.checkbox("Verified")
        ok = confirm_toggle("confirm_cardladder_quick")
        submitted = st.form_submit_button("Import sale", disabled=not ok or not title.strip())
        if submitted:
            sale = build_quick_sale(
                platform=platform,
                raw_title=title,
                price_usd=Decimal(str(price)),
                sold_date=sold_date,
                listing_type=listing_type,
                verified=verified,
            )
            run_id = submit_job(
                "cardladder_quick_sale",
                actions.cardladder_manual_import,
                params={"rows": 1},
                kwargs={"rows": [sale.to_dict()]},
            )
            st.session_state["job_cardladder_quick"] = run_id
            st.rerun()
    _render_status("job_cardladder_quick")
```

Import `Decimal` and `build_quick_sale` at the top. Use `build_quick_sale` from the importer so quick entry and paste entry share the same schema and duplicate key logic.

- [ ] **Step 3: Update page render smoke test if needed**

Keep:

```python
def test_ingest_page_renders(migrated_db):
    at = AppTest.from_file("reports/app/pages/3_📥_Ingest.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
```

If Streamlit testing needs session-state initialization for the new preview key, initialize it in the page with `st.session_state.get("cardladder_preview", [])` rather than modifying the test.

- [ ] **Step 4: Run UI smoke test**

Run:

```bash
uv run pytest tests/test_ingest_page.py -q
```

Expected: pass.

## Task 6: Docs

**Files:**
- Modify: `docs/trader_console.md`

- [ ] **Step 1: Update Ingest page docs**

Replace the `📥 Ingest` bullet list with entries for:

- Auction-house CSV upload
- Card Ladder paste import
- Quick sale entry
- eBay sold-listings ingest
- PSA pop snapshot

Add these constraints:

```markdown
Card Ladder import is manual-only. It does not scrape Card Ladder, use browser cookies,
or call private APIs. The operator copies visible Sales History rows, previews parsed
fields locally, then imports confirmed rows into `tx_raw` with
`source='cardladder_manual'`; best-effort parsed rows also enter `tx_clean`.
```

- [ ] **Step 2: Run docs-adjacent tests**

Run:

```bash
uv run pytest tests/test_ingest_page.py tests/test_cardladder_manual_import.py -q
```

Expected: pass.

## Task 7: Verification

**Files:**
- All changed files

- [ ] **Step 1: Run focused verification**

Run:

```bash
uv run pytest tests/test_cardladder_manual_import.py tests/test_ingest_page.py -q
```

Expected: pass.

- [ ] **Step 2: Run existing parser and report-adjacent tests**

Run:

```bash
uv run pytest tests/test_regex_parser.py tests/test_parse_triage.py tests/test_ingest_page.py -q
```

Expected: pass.

- [ ] **Step 3: Run lint**

Run:

```bash
uv run ruff check .
```

Expected: pass.

- [ ] **Step 4: Optional full test suite**

Run if time and local DB state allow:

```bash
uv run pytest tests/ -q
```

Expected: pass, or report any pre-existing failures with exact failing tests.

## Task 8: Review and Handoff

**Files:**
- Git metadata only

- [ ] **Step 1: Inspect diff**

Run:

```bash
git status --short
git diff -- src/sportscards/ingest/cardladder_manual.py reports/app/_components/actions.py reports/app/pages/3_📥_Ingest.py tests/test_cardladder_manual_import.py tests/test_ingest_page.py docs/trader_console.md
```

Expected: only planned files changed.

- [ ] **Step 2: Commit after approval**

Commit only after implementation is reviewed:

```bash
git add src/sportscards/ingest/cardladder_manual.py reports/app/_components/actions.py reports/app/pages/3_📥_Ingest.py tests/test_cardladder_manual_import.py tests/test_ingest_page.py docs/trader_console.md
git commit -m "feat: add Card Ladder manual import"
```

## Self-Review

Spec coverage:
- Paste parser: Task 1 and Task 2
- tx_raw/tx_clean import: Task 3
- duplicate handling: Task 3
- Streamlit paste preview/import: Task 5
- Quick Sale Entry: Task 5
- tests: Tasks 1, 3, 5, 7
- docs: Task 6
- no scraping/private API/cookies: Docs and implementation architecture keep feature manual-only

Placeholder scan:
- No placeholder markers, vague error-handling instruction, or ellipsis placeholder remains.

Type consistency:
- `CardLadderSale` and `ImportResult` are the only new importer types.
- All import paths point to existing package locations or files created by this plan.
