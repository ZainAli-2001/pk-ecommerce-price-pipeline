"""
=============================================================
  database.py — Supabase Storage Layer
=============================================================
SCHEMA DESIGN (schema stabilization):

  Two tables mirror the 3-layer data structure from scrapers:

  products table:
    → one row per unique product URL
    → stores identity + context (Layer 3 fields)
    → source column distinguishes daraz vs naheed
    → UNIQUE constraint on url prevents duplicates

  product_prices table:
    → one row per scrape observation per product
    → stores all three layers per run:
        Layer 1: original_price, sale_price, final_price
        Layer 2: unit_value, unit_type, unit_price
        Layer 3: run_id, scraped_at
    → this is the time series Prophet consumes
    → indexed on (product_id, scraped_at) for fast queries

WHY SUPABASE:
  GitHub Actions runs on a fresh VM every times. Supabase is
  a free cloud PostgreSQL database that persists forever,
  so every run appends to the same growing time series.

SETUP (5 minutes, one-time):
  1. supabase.com → New project (free)
  2. SQL Editor → paste and run SCHEMA_SQL below
  3. Settings → API → copy Project URL + service_role key
  4. Add as GitHub Secrets: SUPABASE_URL, SUPABASE_KEY

INSTALL:
  pip install supabase
=============================================================
"""

import os
import logging
from datetime import datetime

from utils.validation import validate_unit_price

log = logging.getLogger(__name__)


# -----------------------------------------------------------
# SCHEMA SQL
# Run this once in Supabase SQL Editor to create tables.
# Copy everything between the triple quotes.
# -----------------------------------------------------------
SCHEMA_SQL = """
-- ============================================================
-- Run this once in Supabase SQL Editor
-- ============================================================

-- 1. Products table (deduplicated items)
create table if not exists products (
    id         bigserial primary key,
    name       text not null,
    url        text unique,
    category   text,
    keyword    text,
    source     text,                        -- 'daraz' | 'naheed'
    created_at timestamp default now()
);

-- 2. Price history table (main forecasting time series)
create table if not exists product_prices (
    id             bigserial primary key,

    product_id     bigint references products(id) on delete cascade,

    -- Layer 1: raw prices
    original_price numeric,
    sale_price     numeric,
    final_price    numeric,

    -- Layer 2: normalised unit price
    unit_value     numeric,                 -- extracted weight/volume number
    unit_type      text,                    -- 'kg' | 'liter' | 'unit'
    unit_price     numeric,                 -- final_price / unit_value

    -- quality signals
    rating         numeric,
    review_count   integer,

    -- Layer 3: time series anchors
    scraped_at     timestamp not null,
    run_id         text not null,

    -- version info
    pipeline_version text
);

-- 3. Scrape execution statistics
create table if not exists scrape_runs (
    id                bigserial primary key,

    run_id            text not null,
    scraper           text not null,

    total_received    integer default 0,
    parsed            integer default 0,

    missing_title     integer default 0,
    missing_price     integer default 0,
    missing_unit      integer default 0,

    outliers          integer default 0,
    filtered_products integer default 0,
    parsing_failures  integer default 0,

    -- actual DB-confirmed insert count, patched in after insert_prices()
    -- runs (see update_inserted_count()) — may differ from `parsed` due
    -- to dedup, missing product_id mapping, or failed insert chunks
    inserted          integer default 0,

    scraped_at        timestamp default now()
);


-- 4. Rejected product records
create table if not exists scrape_rejections (
    id             bigserial primary key,

    run_id         text,
    scraper        text,

    category       text,
    reason         text,

    product_name   text,
    product_url    text,

    rejected_at    timestamp default now()
);

-- 5. Indexes (critical for forecasting queries)
create index if not exists idx_prices_product_time
    on product_prices(product_id, scraped_at);

create index if not exists idx_prices_run_id
    on product_prices(run_id);

-- 6. View for easy pandas / Supabase analysis
create or replace view latest_product_prices as
select distinct on (p.id)
    p.id,
    p.name,
    p.category,
    p.source,
    pr.final_price,
    pr.unit_price,
    pr.unit_type,
    pr.rating,
    pr.scraped_at,
    pr.run_id
from products p
join product_prices pr on pr.product_id = p.id
order by p.id, pr.scraped_at desc;
"""


# -----------------------------------------------------------
# CLIENT
# Reads credentials from environment variables.
# GitHub Actions injects these from Secrets automatically.
# -----------------------------------------------------------
def get_client():
    """Create and return a Supabase client using env vars."""
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        raise EnvironmentError(
            "SUPABASE_URL and SUPABASE_KEY must be set.\n"
            "  Local:  export SUPABASE_URL=... SUPABASE_KEY=...\n"
            "  GitHub: Settings → Secrets → Actions → New secret"
        )
    return create_client(url, key)


# -----------------------------------------------------------
# UPSERT PRODUCTS
# Insert new products; skip any URL that already exists.
# Returns a dict of url → product_id for the price insert step.
# -----------------------------------------------------------
def upsert_products(client, items: list) -> dict:
    seen_urls = set()
    product_rows = []

    for item in items:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            product_rows.append({
                "name":     item["name"],
                "url":      url,
                "category": item["category"],
                "keyword":  item["keyword"],
                "source":   item.get("source", "unknown"),
            })

    if not product_rows:
        return {}

    # Upsert in chunks of 100 to avoid request size limits
    chunk_size = 100
    upsert_failures = 0
    for i in range(0, len(product_rows), chunk_size):
        chunk = product_rows[i : i + chunk_size]
        try:
            client.table("products").upsert(
                chunk,
                on_conflict="url",
                ignore_duplicates=True
            ).execute()
        except Exception as e:
            upsert_failures += 1
            log.error(
                "  Upsert failed for chunk %d-%d (%d products): %s",
                i, i + len(chunk), len(chunk), e
            )
            # Don't re-raise — a failed chunk here shouldn't stop the
            # remaining chunks. Any URL in this chunk will simply come back
            # with no id from the re-fetch below, and get counted there.

    # Fetch IDs in chunks of 100 — fixes "URL query too long" error
    url_to_id = {}
    urls = [r["url"] for r in product_rows]
    select_failures = 0

    for i in range(0, len(urls), chunk_size):
        chunk_urls = urls[i : i + chunk_size]
        try:
            response = (
                client.table("products")
                .select("id, url")
                .in_("url", chunk_urls)
                .execute()
            )
        except Exception as e:
            select_failures += 1
            log.error(
                "  ID re-fetch failed for chunk %d-%d (%d urls): %s",
                i, i + len(chunk_urls), len(chunk_urls), e
            )
            continue

        for row in response.data:
            url_to_id[row["url"]] = row["id"]

    unmapped = [u for u in urls if u not in url_to_id]
    if unmapped:
        log.warning(
            "  %d of %d product URLs did not map to an id (upsert_failures=%d, select_failures=%d) — "
            "these items will be skipped by insert_prices(). Sample: %s",
            len(unmapped), len(urls), upsert_failures, select_failures,
            unmapped[:5]
        )

    log.info("  Mapped %d product URLs to IDs", len(url_to_id))
    return url_to_id


# -----------------------------------------------------------
# INSERT PRICES
# Every scrape run inserts fresh rows — this IS the time series.
# Batched in chunks of 500 to stay within Supabase limits.
# Returns {run_id: inserted_count}, tallied per run_id since `items` may
# combine multiple scrapers' output in one call — used to patch the real
# `inserted` count back into each scraper's scrape_runs row afterward.
# -----------------------------------------------------------
def insert_prices(client, items: list, url_to_id: dict) -> dict:
    price_rows = []
    skipped_no_product_id = 0

    for item in items:
        product_id = url_to_id.get(item.get("url", ""))
        if not product_id:
            skipped_no_product_id += 1
            continue

        # Sanitize — convert empty strings to None
        # Supabase integer columns reject "" but accept NULL
        def clean(val):
            if val == "" or val == "":
                return None
            return val

        price_rows.append({
            "product_id":     product_id,

            "original_price": clean(item.get("original_price")),
            "sale_price":     clean(item.get("sale_price")),
            "final_price":    clean(item.get("final_price")),

            "unit_value":     clean(item.get("unit_value")),
            "unit_type":      clean(item.get("unit_type")),
            "unit_price":     clean(item.get("unit_price")),

            "rating":         clean(item.get("rating")),
            "review_count":   clean(item.get("review_count")),

            "run_id":         item.get("run_id"),
            "pipeline_version": clean(item.get("pipeline_version")),
            "scraped_at":     item.get("scraped_at", datetime.now().isoformat()),
            
        })

    if skipped_no_product_id:
        log.warning(
            "  %d items skipped — no product_id mapping found (see upsert_products() log above for why)",
            skipped_no_product_id
        )

    if not price_rows:
        log.warning("No price rows to insert.")
        return {}

    chunk_size = 500
    inserted_by_run_id = {}
    total_inserted = 0
    insert_failures = 0
    for i in range(0, len(price_rows), chunk_size):
        chunk = price_rows[i : i + chunk_size]
        try:
            client.table("product_prices").insert(chunk).execute()
        except Exception as e:
            insert_failures += 1
            log.error(
                "  Insert failed for chunk %d-%d (%d rows): %s",
                i, i + len(chunk), len(chunk), e
            )
            # Don't re-raise — let remaining chunks still attempt to insert
            # rather than losing every row after the first bad chunk.
            continue

        for row in chunk:
            rid = row["run_id"]
            inserted_by_run_id[rid] = inserted_by_run_id.get(rid, 0) + 1
        total_inserted += len(chunk)
        log.info("  Inserted %d price rows", len(chunk))

    if insert_failures:
        log.warning(
            "  %d of %d price rows failed to insert across %d failed chunk(s)",
            len(price_rows) - total_inserted, len(price_rows), insert_failures
        )

    return inserted_by_run_id


def save_scrape_run(client, stats: dict, scraper_name: str, run_id: str):
    row = {
        "run_id": run_id,
        "scraper": scraper_name,
        "total_received": stats["total_received"],
        "parsed": stats["parsed"],
        "missing_title": stats["missing_title"],
        "missing_price": stats["missing_price"],
        "missing_unit": stats["missing_unit"],
        "outliers": stats["outliers"],
        "filtered_products": stats["filtered_products"],
        "parsing_failures": stats["parsing_failures"],
        "scraped_at": datetime.now().isoformat(),
    }

    client.table("scrape_runs").insert(row).execute()

    log.info(
        "Saved scrape statistics → %s | received=%d parsed=%d rejected=%d",
        scraper_name,
        stats["total_received"],
        stats["parsed"],
        (
            stats["filtered_products"]
            + stats["missing_title"]
            + stats["missing_price"]
            + stats["missing_unit"]
            + stats["outliers"]
        )
    )

def save_rejections(client, rejected_items: list):
    if not rejected_items:
        log.info("No rejected items to save.")
        return

    client.table("scrape_rejections").insert(rejected_items).execute()

    log.info(
        "Saved %d rejected products to scrape_rejections",
        len(rejected_items)
    )


def update_inserted_count(client, run_id: str, scraper: str, inserted: int):
    """
    Patch the real DB-confirmed `inserted` count into an existing
    scrape_runs row, once insert_prices() has finished. Called after the
    row's initial write so a crash during upsert/insert still leaves the
    rest of the diagnostic trail intact.
    """
    client.table("scrape_runs").update(
        {"inserted": inserted}
    ).eq("run_id", run_id).eq("scraper", scraper).execute()

    log.info("  Patched inserted=%d for %s run %s", inserted, scraper, run_id)


# -----------------------------------------------------------
# MAIN SAVE FUNCTION
# Called by main.py — handles the full save flow in one call.
# -----------------------------------------------------------
def save_all(items: list, results: list):
    """
    Save all scraped items (daraz + naheed) to Supabase.
    Upserts products first, then inserts price observations.
    """
    log.info("Connecting to Supabase...")
    client = get_client()

    # Save per-scraper metadata — always, even if no items were parsed,
    # so a fully-blocked run still leaves a diagnostic trail.
    for result in results:
        save_scrape_run(
            client,
            result["stats"],
            result["scraper"],
            result["run_id"],
        )

        save_rejections(
            client,
            result["rejections"],
        )

    if not items:
        log.warning("No items to save.")
        return

    # Save product catalogue
    log.info("Upserting products...")
    url_to_id = upsert_products(client, items)

    # Save price history
    log.info("Inserting price observations...")
    inserted_by_run_id = insert_prices(client, items, url_to_id)

    # Patch the real inserted count into each scraper's scrape_runs row,
    # now that we know what actually landed in product_prices.
    for result in results:
        update_inserted_count(
            client,
            result["run_id"],
            result["scraper"],
            inserted_by_run_id.get(result["run_id"], 0),
        )

    log.info("Save complete → %d items", len(items))


# -----------------------------------------------------------
# QUERY HELPER — used by forecasting module (next phase)
#
# Returns a clean DataFrame ready for Prophet:
#   ds = scraped_at (datetime)
#   y  = unit_price (the forecasting target)
# -----------------------------------------------------------
def get_price_history(category: str, source: str = None, unit_type: str = None, pipeline_version: str = None, page_size: int = 1000):
    """
    Fetch time-series price history for a category from Supabase.
    Returns a pandas DataFrame sorted by scraped_at.

    Args:
        category:         e.g. "flour", "oil", "sugar"
        source:           "daraz" | "naheed" | None (both)
        unit_type:        "kg" | "liter" | None (no filter — each category
                           already normalizes to one standard unit; forcing
                           "kg" as a default silently emptied liter-based
                           categories like oil/ghee/dairy)
        pipeline_version: filter to rows from one pipeline version, or
                           None (all versions)
        page_size:        rows per request when paginating (default 1000,
                           matching Supabase/PostgREST's typical cap — raise
                           it only if your project's max-rows setting has
                           been raised to match, otherwise a page_size above
                           the server's real cap silently gets truncated
                           back down to that cap per request anyway)

    Returns:
        DataFrame with: name, source, unit_price, unit_type, scraped_at
    """
    import pandas as pd

    from utils.config import MIN_SAMPLE_FOR_MEDIAN

    client = get_client()

    query = (
        client.table("product_prices")
        .select(
            "final_price, unit_value, unit_type, unit_price, run_id, pipeline_version, scraped_at, "
            "products!inner(name, category, source, keyword)"
        )
        .eq("products.category", category)
        .not_.is_("unit_price", "null")
    )
    if unit_type is not None:
        query = query.eq("unit_type", unit_type)
    if source is not None:
        query = query.eq("products.source", source)
    if pipeline_version is not None:
        query = query.eq("pipeline_version", pipeline_version)

    # Supabase/PostgREST caps a single request (commonly 1000 rows) unless
    # paginated — page through with .range() until a page comes back short.
    data = []
    start = 0
    while True:
        page = query.range(start, start + page_size - 1).execute()
        if not page.data:
            break
        data.extend(page.data)
        if len(page.data) < page_size:
            break
        start += page_size

    if not data:
        return pd.DataFrame()

    rows = []
    for row in data:
        product = row.get("products") or {}
        rows.append({
            "name":        product.get("name"),
            "source":      product.get("source"),
            "category":    product.get("category"),
            "keyword":     product.get("keyword"),
            "final_price": row["final_price"],
            "unit_value":  row["unit_value"],
            "unit_type":   row["unit_type"],
            "unit_price":  row["unit_price"],
            "run_id":      row["run_id"],
            "pipeline_version": row.get("pipeline_version"),
            "scraped_at":  row["scraped_at"],
        })

    df = pd.DataFrame(rows)

    # Re-validate with a real median from this fetched sample, same
    # median-first logic the live pipeline uses, rather than always
    # falling back to the static dict (which would re-reject legitimate
    # rows a real median had already vouched for at scrape time).
    prices = df["unit_price"].dropna()
    reference_median = prices.median() if len(prices) >= MIN_SAMPLE_FOR_MEDIAN else None
    df = df[df["unit_price"].apply(
        lambda p: validate_unit_price(p, category, reference_median=reference_median) is not None
    )]

    if df.empty:
        return df

    df["scraped_at"] = pd.to_datetime(df["scraped_at"])
    return df.sort_values("scraped_at").reset_index(drop=True)