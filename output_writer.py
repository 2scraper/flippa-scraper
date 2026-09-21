"""
output_writer.py
-----------------
Shared row model + JSON/CSV writers + the run-status/exit-code mapping used by
every engine in this repo.

Family core. The only per-site parts are the `source` constant
and the site-specific columns at the END of `Product` — the family prefix
(source, scraped_at, url, sku, title, price, currency, original_price,
discount_pct, category, price_source) keeps its names and its order so one
consumer can read every repo in the family.

Three family columns are deliberately ABSENT, each with the measurement that
justifies removing it rather than shipping a column that is null on every row
of every run:

  brand         Flippa sells businesses, not branded goods. 0 of 25 rows on
                the 2026-08-27 capture and 0 of 25 on each 2026-09-19 capture
                carry anything brand-shaped.
  rating        A listing card shows no rating. The embedded JSON has an
                `app_rating` on 1 of 25 rows (an App Store rating for an iOS
                listing, not a rating OF the listing), so the column would be
                null on ~96% of rows and mean something different on the rest.
  in_stock      Not a concept here; `status` and `sale_method` carry what the
                marketplace actually says about availability.
"""

import csv
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional, List, Set

SOURCE = "flippa.com"


@dataclass
class Product:
    # --- family prefix: same names, same order, across the whole family ----
    source: str = SOURCE
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url: str = ""
    sku: Optional[str] = None          # Flippa's numeric listing id
    title: Optional[str] = None        # the card's heading: "SaaS | Health and Beauty"
    price: Optional[float] = None
    # No guessed default: a row whose currency could not be established says
    # so (None) rather than silently claiming USD.
    currency: Optional[str] = None
    original_price: Optional[float] = None
    discount_pct: Optional[float] = None
    category: Optional[str] = None     # the --category label for this run
    # Where the row came from, because the same columns can be filled from two
    # views with different confidence and nothing used to say which:
    #   "state"     — the listing JSON Flippa embeds in the page it serves
    #                 (`const STATE = {...}`). Numeric, complete, and what the
    #                 grid itself is rendered from.
    #   "state+dom" — the same row, cross-checked against the rendered card
    #                 with the matching id. Both views agreed.
    #   "dom"       — the rendered card only: the fallback path, used when the
    #                 embedded JSON is absent or unparseable. Several columns
    #                 are not on the card at all and stay null.
    price_source: Optional[str] = None
    # Which page of the listing this row came from, and its 1-based position
    # within that page. `position` restarts at 1 on every page, so the pair is
    # what is unique — see smoke_test.py, which asserts exactly that.
    page: Optional[int] = None
    position: Optional[int] = None

    # --- site-specific, at the end -----------------------------------------
    description: Optional[str] = None      # the seller's own one-line summary
    asset_type: Optional[str] = None       # "SaaS", "Ecommerce", "Domain", ...
    industry: Optional[str] = None         # "Health and Beauty", "Business", ...
    monetization: Optional[str] = None     # "Services & Subscriptions", "Ads", ...
    monthly_profit: Optional[float] = None    # net profit per month, may be negative
    monthly_revenue: Optional[float] = None
    profit_multiple: Optional[float] = None   # price / (12 x monthly_profit)
    revenue_multiple: Optional[float] = None
    age_text: Optional[str] = None         # as Flippa writes it: "3 years"
    country: Optional[str] = None          # as Flippa writes it: "MI, United States"
    status: Optional[str] = None           # "open", "won", ...
    sale_method: Optional[str] = None      # "classified", "auction", "instant_sale"
    bid_count: Optional[int] = None
    broker_name: Optional[str] = None
    managed_by_flippa: Optional[bool] = None
    sponsored: Optional[bool] = None
    editors_choice: Optional[bool] = None
    confidential: Optional[bool] = None
    verified_listing: Optional[bool] = None
    image_url: Optional[str] = None


def dedupe_by_sku(products: List[Product], seen: Set[str]) -> List[Product]:
    """Drop products whose sku already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a repeating page then re-parses without duplicating its rows into the
    final output. A product with no sku is always kept: there is nothing to
    key a duplicate check on, and dropping it would be a silent data loss
    rather than a duplicate removal.
    """
    fresh = []
    for p in products:
        if p.sku is None or p.sku not in seen:
            if p.sku is not None:
                seen.add(p.sku)
            fresh.append(p)
    return fresh


def write_json(products: List[Product], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in products], f, ensure_ascii=False, indent=2)


def write_csv(products: List[Product], path: str) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows.
    if not products:
        with open(path, "w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=list(asdict(Product()).keys())).writeheader()
        return
    fieldnames = list(asdict(products[0]).keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in products:
            writer.writerow(asdict(p))


# Exit code used when a run completes but produced nothing. Distinct from 1
# (crash) so a caller can tell "ran, found nothing" from "blew up".
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started — distinct from EXIT_NO_PRODUCTS so a caller can tell "the
# search genuinely matched nothing" from "something stood between us and the
# content". See page_flow.classify.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME listings and then stopped early — a
# page-load timeout, or a challenge, on page 3 of 10. The output file is still
# written (throwing away two good pages would be worse), but it is not a
# complete picture, and a consumer that cannot tell the difference will read
# the pages that were never fetched as listings that were delisted.
EXIT_PARTIAL = 6


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path.

    Deliberately a separate `<out>.meta.json` rather than columns on every
    row: this describes the RUN, not the listing, and repeating it across 25
    identical rows would both bloat the output and change the schema every
    consumer already parses.

    diff_runs.py reads it to refuse a comparison between runs that are not
    both complete — the failure mode it exists to prevent is a partial run's
    un-fetched pages being reported as delisted listings.
    """
    path = f"{out_prefix}.meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             total_results: Optional[int] = None,
             addressable: Optional[bool] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — every requested page was fetched, or the listing genuinely
                 ran out (nothing more existed to get)
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `pages_failed` lists the pages that did not yield data, BY NUMBER: a count
    stops being a description once pages can be fetched independently and page
    3 can fail while 4 and 5 succeed.

    `total_results` is Flippa's own match count for the query, which makes
    "did we get everything?" checkable rather than a guess — note the site
    caps it at 10000 and prints "10,000+" above that.

    `addressable` records whether pages 2..N could be addressed by URL at all
    (see product_parser.page_url): False means the run had to chain, and that
    --concurrency was refused for this listing.
    """
    return {
        "source": SOURCE,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        "products": products,
        "total_results": total_results,
        "addressable": addressable,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def save(products: List[Product], out_prefix: str, fmt: str,
         allow_empty: bool = False) -> int:
    """Write JSON/CSV and return a process exit code.

    On zero products, nothing is written at all unless `allow_empty`. A
    page-load timeout that writes `[]` and exits 0 is read by a consuming
    pipeline as a successful run with no listings — and if the file already
    held a good result, that result is now gone: the failure destroyed the
    last known good data.

    `allow_empty=True` is for the legitimate case: a filter that genuinely
    matches nothing (Flippa says so explicitly — its own totalResults is 0),
    where an empty file is the answer.
    """
    if not products and not allow_empty:
        print(f"[!] 0 listings — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(products, f"{out_prefix}.json")
        print(f"[+] Saved {len(products)} listings -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        write_csv(products, f"{out_prefix}.csv")
        print(f"[+] Saved {len(products)} listings -> {out_prefix}.csv")
    return 0 if products else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see. Anything
# else ended the page loop early, so the result is only a partial view.
#
# "no_new_listings" and "listing_exhausted" are both properties of the DATA —
# a page that added nothing new, and Flippa's own empty results array past the
# end of the listing. There is deliberately no selector-based entry: this site
# publishes no next-page link at all (see product_parser.page_url()).
COMPLETE_STOP_REASONS = ("completed", "listing_exhausted", "no_new_listings",
                         "no_results")


def finish_run(products: List[Product], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               total_results: Optional[int] = None,
               addressable: Optional[bool] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all engines so the status/exit-code mapping cannot drift between
    them.

    The metadata sidecar is written ONLY when the output file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other.
    """
    complete = stop_reason in COMPLETE_STOP_REASONS
    rc = save(products, out_prefix, fmt, allow_empty=allow_empty)
    wrote_output = bool(products) or allow_empty

    if wrote_output:
        status = "complete" if (products and complete) else (
            "partial" if products else "failed")
        if not products and allow_empty and complete:
            # An explicitly empty result that the site itself reported as
            # empty is a complete answer, not a failure.
            status = "complete"
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, total_results=total_results,
            addressable=addressable,
            start_url=start_url, final_url=final_url, products=len(products)))

    if not products:
        # Nothing gathered at all: a challenge outranks "empty listing",
        # because it says something stood between the run and the content.
        return EXIT_BLOCKED if blocked else rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
