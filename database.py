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
  3. Settings → API → copy Project URL + anon key
  4. Add as GitHub Secrets: SUPABASE_URL, SUPABASE_KEY

INSTALL:
  pip install supabase
=============================================================
"""

import os
import logging
from datetime import datetime

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
    run_id         text not null
);

-- 3. Indexes (critical for forecasting queries)
create index if not exists idx_prices_product_time
    on product_prices(product_id, scraped_at);

create index if not exists idx_prices_run_id
    on product_prices(run_id);

-- 4. View for easy pandas / Supabase analysis
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
    for i in range(0, len(product_rows), chunk_size):
        chunk = product_rows[i : i + chunk_size]
        client.table("products").upsert(
            chunk,
            on_conflict="url",
            ignore_duplicates=True
        ).execute()

    # Fetch IDs in chunks of 100 — fixes "URL query too long" error
    url_to_id = {}
    urls = [r["url"] for r in product_rows]

    for i in range(0, len(urls), chunk_size):
        chunk_urls = urls[i : i + chunk_size]
        response = (
            client.table("products")
            .select("id, url")
            .in_("url", chunk_urls)
            .execute()
        )
        for row in response.data:
            url_to_id[row["url"]] = row["id"]

    log.info("  Mapped %d product URLs to IDs", len(url_to_id))
    return url_to_id


# -----------------------------------------------------------
# INSERT PRICES
# Every scrape run inserts fresh rows — this IS the time series.
# Batched in chunks of 500 to stay within Supabase limits.
# -----------------------------------------------------------
def insert_prices(client, items: list, url_to_id: dict):
    """
    Insert one price observation per item per scrape run.
    Carries all 3 layers of data from the scraper output.
    """
    price_rows = []

    for item in items:
        product_id = url_to_id.get(item.get("url", ""))
        if not product_id:
            continue

        price_rows.append({
            "product_id": product_id,

            # Layer 1
            "original_price": item.get("original_price"),
            "sale_price":     item.get("sale_price"),
            "final_price":    item.get("final_price"),

            # Layer 2
            "unit_value": item.get("unit_value"),
            "unit_type":  item.get("unit_type"),
            "unit_price": item.get("unit_price"),

            # quality signals
            "rating":       item.get("rating"),
            "review_count": item.get("review_count"),

            # Layer 3 time series
            "run_id":     item.get("run_id"),
            "scraped_at": item.get("scraped_at", datetime.now().isoformat()),
        })

    if not price_rows:
        log.warning("No price rows to insert.")
        return

    chunk_size = 500
    for i in range(0, len(price_rows), chunk_size):
        chunk = price_rows[i : i + chunk_size]
        client.table("product_prices").insert(chunk).execute()
        log.info("  Inserted %d price rows", len(chunk))


# -----------------------------------------------------------
# MAIN SAVE FUNCTION
# Called by main.py — handles the full save flow in one call.
# -----------------------------------------------------------
def save_all(items: list):
    """
    Save all scraped items (daraz + naheed) to Supabase.
    Upserts products first, then inserts price observations.
    """
    if not items:
        log.warning("No items to save.")
        return

    log.info("Connecting to Supabase...")
    client = get_client()

    log.info("Upserting products...")
    url_to_id = upsert_products(client, items)

    log.info("Inserting price observations...")
    insert_prices(client, items, url_to_id)

    log.info("Save complete → %d items", len(items))


# -----------------------------------------------------------
# QUERY HELPER — used by forecasting module (next phase)
#
# Returns a clean DataFrame ready for Prophet:
#   ds = scraped_at (datetime)
#   y  = unit_price (the forecasting target)
# -----------------------------------------------------------
def get_price_history(category: str, source: str = None, unit_type: str = "kg"):
    """
    Fetch time-series price history for a category from Supabase.
    Returns a pandas DataFrame sorted by scraped_at.

    Args:
        category:  e.g. "flour", "oil", "sugar"
        source:    "daraz" | "naheed" | None (both)
        unit_type: "kg" | "liter" | "unit" — filter for comparability

    Returns:
        DataFrame with: name, source, unit_price, unit_type, scraped_at
    """
    import pandas as pd

    client = get_client()

    query = (
        client.table("product_prices")
        .select(
            "final_price, unit_value, unit_type, unit_price, run_id, scraped_at, "
            "products(name, category, source, keyword)"
        )
        .eq("unit_type", unit_type)
        .not_.is_("unit_price", "null")
    )

    response = query.execute()

    if not response.data:
        return pd.DataFrame()

    rows = []
    for row in response.data:
        product = row.get("products") or {}
        if product.get("category") != category:
            continue
        if source and product.get("source") != source:
            continue
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
            "scraped_at":  row["scraped_at"],
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["scraped_at"] = pd.to_datetime(df["scraped_at"])
    return df.sort_values("scraped_at").reset_index(drop=True)
