"""
product_parser.py
------------------
Extraction for flippa.com listing pages. **This module IS the site** — every
other module in this repo is family core with a handful of named constants.

What the captures actually say (measured elsewhere in this family: answered from the
dumps, not from expectation). Sources: a rendered browser capture of
`/search?filter[sitetype]=saas` (2026-08-27, `captures/real_search_dump.html`,
1.3 MB, 25 cards) and five plain-HTTP captures taken 2026-09-19 (search page
1 and 2, a category shortcut, a no-results query, and a page past the end).

1. **There is no product JSON-LD.** A search page carries exactly two
   `application/ld+json` blocks: a `WebPage` and an `FAQPage`. Neither
   contains a single listing. A parser built on the family's usual JSON-LD
   primary path would return zero rows for ever, silently.

2. **Flippa embeds its own listing JSON in the page it serves**, as
   `const STATE = {"results":[...], "metadata":{"totalResults":N,...}}` — 25
   records per page, each numeric and complete (price, original_price,
   multiples, profit, country, badges). This is the site's own structured
   data, it is present in the RAW HTTP response with no JavaScript executed,
   and the grid is rendered from it. It is therefore the primary path, and
   the equivalent of JSON-LD everywhere else in this family.

3. **The rendered grid is the same 25 records, in the same order.** Verified
   on the browser capture: the DOM's `<div id="listing-NNN">` ids match
   `STATE.results[*].id` one for one, in order. So the DOM is a fallback
   rather than a second opinion — but it is a real fallback, because a page
   fetched through a browser that has already navigated inside the Angular
   app can hold a grid the served STATE does not describe.

4. **A card's boundary is the site's own id attribute**, `id="listing-NNN"` —
   not a CSS class (Flippa's are Tailwind build output) and not "widen up
   from a product link until one link is left": a real card holds THREE links
   to the same listing (thumbnail, text block, "View Listing"), plus a
   `/watch_item?...` link, so the widen-up heuristic never leaves the anchor.

5. **The badges are Angular `ng-if` attributes**, which name the field they
   render: `listing.sponsored`, `listing.confidential`, `listing.price_dropped`
   and so on. Reading those is exact. The previous version of this file
   matched the card's TEXT for "Sponsored"/"Open", which a seller's own
   one-line summary can contain — a false positive waiting to happen, and
   `status` was matched that way against the word "Open" that also appears in
   "Open to Offer".

6. **Net profit can be negative and is written `-USD $315 p/mo`.** 2 of the
   25 cards on the 2026-08-27 capture are negative. The previous parser
   stripped the currency with a `^`-anchored pattern, so the leading minus
   made the whole field fail to parse and the column came back null on
   exactly the rows where it mattered.

7. **Pagination is `?page[number]=N`, and a bare `?page=N` is deleted by the
   site's own code.** From Flippa's shipped `legacy-foot-*.js`:

       e.currentPage > 1 ? t["page[number]"] = e.currentPage
                         : (delete t.page_number, delete t["page[number]"]),
       t.page && delete t.page

   The server honours both spellings for the initial render (measured
   2026-09-19: `?page=2` and `?page[number]=2` return the same 25 ids, none
   of them page 1's), but the app rewrites the URL to `page[number]`, so that
   is the spelling this module builds. There is NO next-page link anywhere in
   the markup to follow: the pagination control is
   `<span ng-click="pageChangeHandle(page)">2</span>` with no href, so
   `page_url()` is not a fallback here — it is the only mechanism, and every
   run verifies it against the data (see the engines' addressability check).
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import (urljoin, urlparse, urlunparse, parse_qsl, urlencode)

from bs4 import BeautifulSoup

from output_writer import Product

logger = logging.getLogger("product_parser")

SELECTORS = {
    # The site's own id attribute on the card wrapper. The rule this family settled on: where a
    # site publishes no structured data in the DOM, its own data attribute is
    # a better anchor than any class and nearly as good as structured data.
    # Used by every engine as the "has the grid rendered?" marker too.
    "item_card": 'div[id^="listing-"]',
}

# A listing's own URL is flippa.com/<digits> and nothing else — no slug, no
# extension. That is the id, and it is also how a link is told apart from the
# dozens of other links on a card (/watch_item?..., /auctions/<id>/bids,
# /data-insights?...).
_LISTING_PATH_RE = re.compile(r"^/(\d{5,10})/?$")
_CARD_ID_RE = re.compile(r"^listing-(\d+)$")

# ---------------------------------------------------------------------------
# Price parsing
# ---------------------------------------------------------------------------
# Flippa prints money as "USD $573,006" — an ISO code AND a symbol — and the
# key-data block prints monthly profit as "USD $13,202 p/mo", or
# "-USD $315 p/mo" when the business loses money.
#
# An explicit ISO allowlist, never a bare [A-Z]{3}: the card text carries
# "MRR", "MAU", "ARR" and "SEO" next to numbers, and each of those would
# otherwise become a currency and a phantom price. Measured on the 2026-08-27
# capture: a bare [A-Z]{3} pattern matched MRR and MAU as currencies.
_CURRENCY_CODES = frozenset("""
    USD EUR GBP AUD CAD NZD SGD HKD JPY CHF SEK NOK DKK PLN CZK
    INR IDR MYR THB PHP VND ZAR AED SAR BRL MXN ARS CLP COP TRY ILS
""".split())
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}

# Grouping spaces: plain, NBSP, narrow NBSP, thin space. A rendered page uses
# a no-break variant so a number does not wrap, and missing them reads
# "1 234" as 234.
_GROUP_SPACES = "    "
_AMOUNT = (r"\d{1,3}(?:[" + _GROUP_SPACES + r"]\d{3})+(?:[.,]\d{1,2})?"
           r"|[\d.,]+(?:[.,]\d{1,2})?")
_PRICE_RE = re.compile(
    r"(-?)\s*"                                   # a leading minus belongs to the number
    r"(?:\b([A-Z]{3})\b\s*)?"                    # optional ISO code:  USD $13,202
    r"(?:([$€£¥])\s?)?"                          # optional symbol
    r"(" + _AMOUNT + r")")
_REDUCED_PCT_RE = re.compile(r"Reduced\s+([\d.]+)\s*%", re.IGNORECASE)
_PROFIT_MULTIPLE_RE = re.compile(r"([\d.]+)\s*x\s*Profit", re.IGNORECASE)
_REVENUE_MULTIPLE_RE = re.compile(r"([\d.]+)\s*x\s*Revenue", re.IGNORECASE)
_BID_COUNT_RE = re.compile(r"(\d[\d,]*)\s*bids?\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Blocked-page detection
# ---------------------------------------------------------------------------
# flippa.com is fronted by Cloudflare (Server: cloudflare, CF-RAY, __cf_bm),
# and this family's rule applies with force here: COUNT A MARKER ON A PAGE
# YOU KNOW IS GOOD BEFORE ADDING IT. Measured on a served page holding the
# full catalogue (2026-09-19, 964 KB):
#
#     cdn-cgi                1 occurrence   <- Cloudflare's JS-detections tag
#     challenge-platform     1 occurrence   <- ditto: /cdn-cgi/challenge-platform/scripts/jsd/main.js
#     cloudflare             2 occurrences  <- a cdnjs.cloudflare.com preconnect
#
# All three are on EVERY page Flippa serves, so all three are facts about the
# site rather than markers — the family's inherited "challenge-platform" entry
# would have reported exit 3 on every successful run. What is left are strings
# that only a challenge carries.
BOT_CHALLENGE_MARKERS = {
    "cloudflare": ("_cf_chl_opt", "cf-browser-verification", "cf_chl_",
                   "Attention Required! | Cloudflare",
                   "Checking your browser before accessing",
                   "Just a moment...", "/cdn-cgi/challenge-platform/h/"),
}

# Flippa serves every page out of its own asset hosts. An interstitial, and
# Chromium's own network-error page, do not (measured elsewhere in this family: "a served page
# is built out of the site's own assets; an interstitial is not"). Measured:
# 333-432 references on good pages, and this is the ONLY signal that
# classifies Chromium's error page correctly, since that page carries the
# site's own hostname in its <title>.
_ASSET_HOST_RE = re.compile(r"static2?\.flippa\.com|flippa\.com/assets")
MIN_ASSET_REFERENCES = 5

# The 2Captcha Scraping Browser's auto-solve extension injects its own
# captcha hunters into every page it loads, and one of them carries
# data-ts-input="cf-turnstile-response". Flippa's own captcha mount point is
# <captcha-widgets>, which is EMPTY on a listing page and holds a real
# <captcha-widget data-captcha-type="turnstile" data-sitekey="..."> on
# /signup — so the extension's tags must be removed before any marker scan
# (measured elsewhere in this family: add this guard only where the marker set can actually match
# one — here it can).
_EXTENSION_SCRIPT_RE = re.compile(
    r"<script\b[^>]*(?:chrome|moz)-extension://[^>]*>\s*</script>", re.IGNORECASE)


def strip_extension_scripts(html: str) -> str:
    """Remove browser-extension <script> tags, whole, before scanning markers."""
    return _EXTENSION_SCRIPT_RE.sub("", html)


def detect_bot_challenge(html: str) -> Optional[str]:
    """Vendor name if `html` looks like a challenge interstitial, else None."""
    cleaned = strip_extension_scripts(html)
    for vendor, markers in BOT_CHALLENGE_MARKERS.items():
        if any(marker in cleaned for marker in markers):
            return vendor
    return None


def asset_reference_count(html: str) -> int:
    """How many times the page references Flippa's own asset hosts."""
    return len(_ASSET_HOST_RE.findall(html))


def count_cards(html: str) -> int:
    """How many rendered listing cards the markup holds."""
    return len(set(_CARD_ID_RE.match(m).group(1)
                   for m in re.findall(r'id="(listing-\d+)"', html)))


# ---------------------------------------------------------------------------
# The embedded listing JSON
# ---------------------------------------------------------------------------
_STATE_ASSIGNMENT_RE = re.compile(r"\bconst\s+STATE\s*=\s*")


def _json_object_at(text: str, start: int) -> Optional[str]:
    """Return the JSON object beginning at `start`, by brace matching.

    Reading to the end of the line would work on both captures — the site
    emits the object on one line — but that is a formatting accident, and a
    pretty-printed deploy would silently return nothing. Quotes and escapes
    are tracked so a brace inside a seller's summary cannot end the scan.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_state(html: str) -> Optional[dict]:
    """Flippa's embedded `const STATE = {...}` object, or None.

    None means the response is not a rendered listing page — the shell before
    the assignment is emitted, an interstitial, or an error page. It never
    means "no listings": an empty listing is `{"results": [], ...}` with a
    metadata block, which is a different and reportable answer.
    """
    match = _STATE_ASSIGNMENT_RE.search(html)
    if not match:
        return None
    raw = _json_object_at(html, match.end())
    if raw is None:
        logger.warning("Found the STATE assignment but could not brace-match "
                       "its object — treating the page as unparsed.")
        return None
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Flippa's embedded listing JSON did not parse (%s) — "
                       "falling back to the rendered cards.", e)
        return None
    return state if isinstance(state, dict) else None


def state_total_results(state: Optional[dict]) -> Optional[int]:
    """Flippa's own count of matches for this query, or None.

    Capped by the site at 10000 — it prints "10,000+" above that — so a caller
    must not treat 10000 as exact.
    """
    if not state:
        return None
    metadata = state.get("metadata")
    if not isinstance(metadata, dict):
        return None
    total = metadata.get("totalResults")
    return total if isinstance(total, int) else None


def state_results(state: Optional[dict]) -> List[dict]:
    """The listing records in `state`, or [].

    **A zero-match query still returns five records.** Measured 2026-09-19:
    `?query[keyword]=<nonsense>` answers with `totalResults: 0` and five
    promoted listings — the same five that lead the unfiltered search page.
    They are placements, not matches, so reporting them would be five
    fabricated rows in an empty result. The caller (page_flow.classify) treats
    totalResults == 0 as an empty listing and never gets here.
    """
    if not state:
        return []
    results = state.get("results")
    if not isinstance(results, list):
        return []
    return [r for r in results if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# Number and price helpers
# ---------------------------------------------------------------------------
def _normalize_amount(raw: str) -> Optional[float]:
    """Parse an amount written in either decimal convention.

    When both separators appear, whichever comes last is the decimal point.
    When only one appears, exactly three trailing digits is a thousands
    grouping — no currency here has a three-digit subunit.
    """
    for space in _GROUP_SPACES:
        raw = raw.replace(space, "")
    last_dot, last_comma = raw.rfind("."), raw.rfind(",")
    if last_dot != -1 and last_comma != -1:
        norm = (raw.replace(",", "") if last_dot > last_comma
                else raw.replace(".", "").replace(",", "."))
    else:
        sep = max(last_dot, last_comma)
        trailing = raw[sep + 1:] if sep != -1 else ""
        if len(trailing) == 3 and trailing.isdigit():
            norm = raw.replace(".", "").replace(",", "")
        else:
            norm = raw.replace(",", ".")
    try:
        return float(norm)
    except ValueError:
        return None


_PERCENT_RE = re.compile(r"-?\s*\d{1,3}(?:[.,]\d+)?\s*%")


def strip_percentages(text: str) -> str:
    """Remove percentage badges before any price is matched.

    The rule this family settled on: rejecting a percentage match afterwards is not enough,
    because the rejected match has already consumed the currency symbol. On a
    reduced Flippa listing the price node also carries "Reduced 38%", and a
    layout class renders that badge FIRST, so a reader that matched in
    document order could return 38 as the asking price.
    """
    return _PERCENT_RE.sub(" ", text)


def prices_in(text: str) -> Tuple[List[float], Optional[str]]:
    """Return ([amounts], currency_or_None) for the money written in `text`.

    A leading minus is part of the amount: Flippa writes a loss-making
    business's net profit as "-USD $315 p/mo", and dropping the sign turns a
    loss into a profit.

    Currency, in descending trustworthiness: a written ISO code
    names itself and is matched against a real allowlist; a bare "$" is a
    guess, and reads as USD because that is what Flippa prices in; anything
    else is None rather than a default.
    """
    amounts: List[float] = []
    currency: Optional[str] = None
    for m in _PRICE_RE.finditer(text):
        sign, code, symbol, raw = m.groups()
        if code and code not in _CURRENCY_CODES:
            continue
        amount = _normalize_amount(raw)
        if amount is None:
            continue
        if sign == "-":
            amount = -amount
        if currency is None:
            currency = code or _CURRENCY_SYMBOLS.get(symbol or "")
        amounts.append(amount)
    return amounts, currency


def _currency_from_label(label: Optional[str]) -> Optional[str]:
    """ISO code out of Flippa's own `currency_label`, e.g. "USD $" -> "USD".

    Never a default: a label this does not recognise leaves the column null,
    because a wrong currency is not a rounding error.
    """
    if not label:
        return None
    m = re.search(r"\b([A-Z]{3})\b", label)
    if m and m.group(1) in _CURRENCY_CODES:
        return m.group(1)
    return _CURRENCY_SYMBOLS.get(label.strip()[:1])


def _norm_space(text: Optional[str]) -> Optional[str]:
    """Collapse runs of whitespace, or None for nothing at all.

    Applied to every free-text column on BOTH paths, so the same listing reads
    byte-identically whether it came from the embedded JSON or from the card:
    the JSON keeps the seller's own double spaces and newlines, while the
    rendered card has them collapsed by the browser. Without this the two
    paths disagreed on 25 of 25 descriptions for no real reason.
    """
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", str(text)).strip()
    return collapsed or None


def _to_float(value) -> Optional[float]:
    """A number out of a JSON field that may be a number, a string, or null."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    amounts, _ = prices_in(str(value))
    return amounts[0] if amounts else None


def _to_bool(value) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def discount_pct(price: Optional[float], original: Optional[float],
                 stated: Optional[float] = None,
                 sku: Optional[str] = None) -> Optional[float]:
    """The discount, computed from the two prices — never read off the badge.

    Flippa prints its own "Reduced 38%", and on every dropped listing measured
    (8 on 2026-09-19, 14 on 2026-08-27) it agrees with the computed figure to
    within its own rounding. It is still not what goes in the column: the
    family learned on farfetch-scraper that a printed percentage can be the
    first of two compounding discounts, and a figure computed from the two
    numbers in the same row is checkable by the reader. `stated` is used as a
    cross-check only, and a disagreement is logged with the sku.

    Returns None — not 0, not a negative — when the two figures are not what
    they were taken for, so an "original price" at or below the price cannot
    be reported as a discount.
    """
    if price is None or original is None or original <= 0 or original <= price:
        return None
    computed = round((1 - price / original) * 100, 1)
    if stated is not None and abs(stated - computed) > 1.0:
        logger.warning("Listing %s: Flippa prints a %.0f%% drop but %s -> %s is "
                       "%.1f%% — keeping the computed figure.",
                       sku or "?", stated, original, price, computed)
    return computed


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
_PAGE_PARAM = "page[number]"
# Every spelling of "which page" that Flippa's own deserializer reads, all of
# which have to be removed before a new one is set, or two of them end up in
# the same URL disagreeing with each other.
_PAGE_PARAM_ALIASES = ("page[number]", "page_number", "page")


def page_url(url: str, page_num: int) -> str:
    """`url` with Flippa's own page parameter set to `page_num`.

    Site knowledge, hence here rather than in an engine. Page 1 carries no
    page parameter at all — that is what the site's own serializer does, and a
    `page[number]=1` would make page 1's URL differ from the URL a user pastes
    for no reason.

    Existing query parameters (filters, sort order, keyword) are preserved,
    and every spelling of the page parameter is replaced rather than appended
    twice.
    """
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in _PAGE_PARAM_ALIASES]
    if page_num > 1:
        query.append((_PAGE_PARAM, str(page_num)))
    return urlunparse(parts._replace(query=urlencode(query)))


# Route prefixes that are not a category, and the LOCALE segments Flippa's own
# hreflang set publishes: `/search`, `/fr/search` and `/es/search` are the same
# listing in three languages. Without these, a French URL labelled every row
# with the category "fr".
_SITE_LOCALES = ("fr", "es")
_NOT_A_CATEGORY = {"search", "buy", "browse", "sitetype", ""} | set(_SITE_LOCALES)


def locale_from_url(url: str) -> Optional[str]:
    """The locale segment of a Flippa URL, or None for the default English.

    Worth knowing before a run rather than after: a localised path does not
    merely translate labels, it CONVERTS THE PRICES. Measured 2026-09-19,
    listing 12857417 on the same day — `/search`: USD 436,829. `/fr/search`:
    EUR 380,546. Both are correct for their page; mixing the two in one
    dataset is not.
    """
    parts = [p for p in urlparse(url).path.split("/") if p]
    if parts and parts[0].lower() in _SITE_LOCALES:
        return parts[0].lower()
    return None


def category_from_url(url: str) -> Optional[str]:
    """A label for the `category` column, from the URL the user asked for.

    Flippa addresses a listing set three ways, all of them seen live:
        /search?filter[property_type]=saas   the general search
        /websites, /domains, /apps           category shortcuts
        /buy/sitetype/saas                   the older shortcut form
    and each of those can carry a locale prefix (`/fr/search`).

    The QUERY is read first, because it is what the user actually filtered on;
    the path is the fallback. Returns None rather than a made-up label when
    the URL says nothing — an empty column is honest, "search" is not.
    """
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    for key in ("filter[property_type]", "filter[sitetype]", "filter[vertical]",
                "query[keyword]"):
        if query.get(key):
            return query[key]
    for segment in reversed([p for p in parsed.path.split("/") if p]):
        if segment.lower() not in _NOT_A_CATEGORY:
            return segment.replace("-", " ").replace("_", " ")
    return None


# ---------------------------------------------------------------------------
# The primary path: Flippa's own embedded listing JSON
# ---------------------------------------------------------------------------
def _listing_id(record: dict) -> Optional[str]:
    raw = record.get("id")
    if raw is None:
        return None
    raw = str(raw).strip()
    return raw or None


def _row_from_record(record: dict, base_url: str, category: Optional[str],
                     page: Optional[int], position: Optional[int]) -> Product:
    """One Product from one record of Flippa's embedded listing JSON."""
    sku = _listing_id(record)
    url = record.get("listing_url") or (urljoin(base_url, f"/{sku}") if sku else "")

    price = _to_float(record.get("price"))
    original_price = _to_float(record.get("original_price"))
    # price_dropped is the flag the card renders from; without it a stale
    # original_price left on a record would read as a live discount.
    if not record.get("price_dropped"):
        original_price = None

    basic_info = record.get("basic_info")
    title = None
    if isinstance(basic_info, dict):
        title = _norm_space(basic_info.get("name"))
    # The card's paragraph renders the record's `title`, not its `summary` —
    # verified on all 25 cards of the 2026-08-27 capture. The two differ: one
    # listing's summary carries a euro sign its title has had stripped, so
    # reading `summary` here would make the two paths disagree on rows where
    # nothing is actually wrong.
    summary = record.get("title") or record.get("summary")

    bid_count = record.get("bid_count")
    bid_count = int(bid_count) if isinstance(bid_count, int) else None

    # Net profit comes from the record's own key_data block, which is what the
    # card renders — NOT from `profit_average`. Measured on the 2026-08-27
    # capture: `profit_average` is null on exactly the two listings that lose
    # money, while key_data carries "-USD $315 p/mo" for them. Reading
    # profit_average alone drops the column on the rows where its sign is the
    # whole story.
    key_data = _record_key_data(record)
    monthly_profit = _money_valued(key_data)
    if monthly_profit is None:
        monthly_profit = _to_float(record.get("profit_average"))

    # `multiple` and `revenue_multiple` are 0.0 — not null — on a listing that
    # has no multiple to show, and the card renders none for it (show_multiple
    # is false). Reporting 0.0 would be a fabricated valuation.
    show_multiple = record.get("show_multiple")
    profit_multiple = revenue_multiple = None
    if show_multiple is not False:
        profit_multiple = _to_float(record.get("multiple")) or None
        revenue_multiple = _to_float(record.get("revenue_multiple")) or None

    return Product(
        url=url,
        sku=sku,
        title=title,
        price=price,
        currency=_currency_from_label(record.get("currency_label")),
        original_price=original_price,
        discount_pct=discount_pct(price, original_price,
                                  _to_float(record.get("price_dropped_percent")),
                                  sku),
        category=category,
        price_source="state",
        page=page,
        position=position,
        description=_norm_space(summary),
        asset_type=record.get("property_type") or None,
        # Flippa calls the industry "category" in its own JSON and prints it
        # under the label "Industry" on the card. Renamed here rather than
        # shadowing this repo's own `category` column, which holds the run's
        # --category label.
        industry=record.get("category") or None,
        monetization=record.get("monetization") or None,
        monthly_profit=monthly_profit,
        monthly_revenue=_to_float(record.get("revenue_average")),
        profit_multiple=profit_multiple,
        revenue_multiple=revenue_multiple,
        age_text=record.get("formatted_age_in_years") or None,
        country=record.get("country_name") or None,
        status=record.get("status") or None,
        sale_method=record.get("sale_method") or None,
        bid_count=bid_count,
        broker_name=(record.get("broker_name") or None),
        managed_by_flippa=_to_bool(record.get("managed_by_flippa")),
        sponsored=_to_bool(record.get("sponsored")),
        editors_choice=_to_bool(record.get("editors_choice")),
        confidential=_to_bool(record.get("confidential")),
        # The card's "Verified Listing" pill renders from
        # display_verification_badge; `manually_vetted` is the separate
        # tooltip beside it, and mapping that one instead made the two paths
        # disagree on a listing that carries the pill without the tooltip.
        verified_listing=_to_bool(record.get("display_verification_badge")),
        image_url=(record.get("thumbnail_url")
                   or record.get("blurred_or_thumbnail_url") or None),
    )


def rows_from_state(state: dict, base_url: str, category: Optional[str] = None,
                    page: Optional[int] = None) -> List[Product]:
    rows = []
    for position, record in enumerate(state_results(state), start=1):
        if _listing_id(record) is None:
            # No id means no sku, no url and nothing to dedupe on. Say so
            # rather than emitting a row that cannot be joined to anything.
            logger.warning("Skipping an embedded record with no id "
                           "(position %d).", position)
            continue
        rows.append(_row_from_record(record, base_url, category, page, position))
    return rows


# ---------------------------------------------------------------------------
# The fallback path: the rendered cards
# ---------------------------------------------------------------------------
_MONEY_MARKER_RE = re.compile(r"[$€£¥]|\b(?:" + "|".join(sorted(_CURRENCY_CODES)) + r")\b")


def _money_valued(key_data: dict) -> Optional[float]:
    """The first key-data value that is an amount of money, parsed.

    Read by the SHAPE of the value, not by an English label, because Flippa
    localises the labels: the same block reads "Net Profit" on /search and
    "Profit" on /fr/search, with "Industrie", "Monétisation" and "Ancienneté"
    beside it. Only one entry on either page is money, so this is exact in
    both — and in a locale nobody here has looked at.
    """
    for value in key_data.values():
        if value and _MONEY_MARKER_RE.search(str(value)):
            amounts, _ = prices_in(str(value))
            if amounts:
                return amounts[0]
    return None


def _record_key_data(record: dict) -> dict:
    """{label.lower: value} from a record's own key_data list.

    The same label/value pairs the card renders: Type, Industry,
    Monetization, Site Age (or App Age / Domain Age) and Net Profit.
    """
    data = {}
    for pair in record.get("key_data") or []:
        if isinstance(pair, dict) and pair.get("label"):
            data[str(pair["label"]).strip().lower()] = pair.get("value")
    return data


def _ng_if(card, expression: str):
    """The element an Angular `ng-if` rendered, or None.

    Angular leaves `<!-- ngIf: expr -->` where the condition was false and the
    element itself where it was true, so presence IS the boolean. Reading the
    attribute is exact where matching the card's text is not: a seller's
    summary containing the word "Sponsored" would otherwise set that flag.
    """
    return card.find(attrs={"ng-if": expression})


def _card_key_data(card) -> dict:
    """The card's label/value block, whatever labels this card happens to show.

    Confirmed labels: Type, Industry, Monetization, Site Age (or App Age /
    Domain Age — the site picks the wording from the asset type) and Net
    Profit. Read generically so a card showing a different subset still parses.
    """
    data = {}
    for block in card.find_all(attrs={"ng-repeat": "data in listing.key_data"}):
        pair = block.find_all("div", recursive=False)
        if len(pair) >= 2:
            label = pair[0].get_text(" ", strip=True)
            value = pair[1].get_text(" ", strip=True)
            if label:
                data[label.lower()] = value
    return data


def _card_country(card) -> Optional[str]:
    """The seller's country. There is no text label for it anywhere.

    The element is `<div ng-if="listing.country_name">`, which names the field
    outright; the map-pin icon it contains is the fallback for markup that
    drops the attribute.
    """
    node = _ng_if(card, "listing.country_name")
    if node is not None:
        span = node.find("span")
        text = (span or node).get_text(" ", strip=True)
        if text:
            return text
    for svg in card.find_all("svg"):
        use = svg.find("use")
        href = (use.get("href") or use.get("xlink:href") or "") if use else ""
        if "map-pin" in href:
            span = svg.find_next("span")
            if span and span.get_text(strip=True):
                return span.get_text(strip=True)
    return None


def _card_prices(card, sku: Optional[str]):
    """(price, currency, original_price, discount_pct) read off the card.

    Scoped to the price column's own `ng-if` elements, never to the whole
    card: the key-data block carries "USD $13,202 p/mo" (net profit) EARLIER
    in document order than the asking price, and a whole-card search picks
    that up instead — measured on the very first real card inspected.

    Three shapes, all from the live markup:
        <h5 ng-if="!listing.price_dropped && !listing.open_to_offer">USD $17,289</h5>
        <div ng-if="listing.price_dropped">  <del>USD $927,250</del> ... USD $573,006 ... Reduced 38%
        <... ng-if="listing.open_to_offer">  no figure at all
    """
    dropped = _ng_if(card, "listing.price_dropped")
    if dropped is not None:
        del_tag = dropped.find("del")
        original, currency = None, None
        if del_tag is not None:
            amounts, currency = prices_in(
                strip_percentages(del_tag.get_text(" ", strip=True)))
            original = amounts[0] if amounts else None
            del_tag.extract()   # so the current price is read from what is left
        text = dropped.get_text(" ", strip=True)
        badge = _REDUCED_PCT_RE.search(text)
        amounts, cur2 = prices_in(strip_percentages(text))
        price = amounts[0] if amounts else None
        stated = float(badge.group(1)) if badge else None
        return (price, currency or cur2, original,
                discount_pct(price, original, stated, sku))

    plain = _ng_if(card, "!listing.price_dropped && !listing.open_to_offer")
    if plain is not None:
        amounts, currency = prices_in(
            strip_percentages(plain.get_text(" ", strip=True)))
        return (amounts[0] if amounts else None), currency, None, None

    # "Open to offer": the site prints no figure, so neither does this row.
    return None, None, None, None


def _row_from_card(card, sku: str, base_url: str, category: Optional[str],
                   page: Optional[int], position: Optional[int]) -> Product:
    heading = card.find("h6")
    title = _norm_space(heading.get_text(" ", strip=True)) if heading else None

    description = None
    desc = card.find("p", class_=lambda c: c and "tw-text-gray-900" in c)
    if desc is not None:
        description = _norm_space(desc.get_text(" ", strip=True))

    key_data = _card_key_data(card)
    age_text = next((v for k, v in key_data.items() if k.endswith("age")), None)
    monthly_profit = _money_valued(key_data)

    card_text = card.get_text(" ", strip=True)
    profit_multiple = revenue_multiple = None
    m = _PROFIT_MULTIPLE_RE.search(card_text)
    if m:
        profit_multiple = float(m.group(1))
    m = _REVENUE_MULTIPLE_RE.search(card_text)
    if m:
        revenue_multiple = float(m.group(1))

    # The card's own ng-if expressions carry the bid count exactly: the
    # "N bids" link renders when there are bids, and the plain asking-price
    # label renders under `!listing.bid_count`, which IS the site saying zero.
    bid_count = None
    bids = _ng_if(card, "listing.bid_count")
    if bids is not None:
        m = _BID_COUNT_RE.search(bids.get_text(" ", strip=True))
        if m:
            bid_count = int(m.group(1).replace(",", ""))
    elif _ng_if(card, "!listing.bid_count && !listing.open_to_offer") is not None:
        bid_count = 0

    url = urljoin(base_url, f"/{sku}")
    for a in card.find_all("a", href=True):
        path = re.sub(r"^https?://[^/]+", "", a["href"])
        if _LISTING_PATH_RE.match(path):
            url = urljoin(base_url, a["href"])
            break

    image_url = None
    img = card.find("img")
    if img is not None:
        image_url = img.get("src") or img.get("ng-src")

    price, currency, original_price, drop = _card_prices(card, sku)

    return Product(
        url=url,
        sku=sku,
        title=title,
        price=price,
        currency=currency,
        original_price=original_price,
        discount_pct=drop,
        category=category,
        price_source="dom",
        page=page,
        position=position,
        description=description,
        asset_type=key_data.get("type"),
        industry=key_data.get("industry"),
        monetization=key_data.get("monetization"),
        monthly_profit=monthly_profit,
        profit_multiple=profit_multiple,
        revenue_multiple=revenue_multiple,
        age_text=age_text,
        country=_card_country(card),
        bid_count=bid_count,
        managed_by_flippa=_ng_if(card, "listing.managed_by_flippa") is not None,
        sponsored=_ng_if(card, "listing.sponsored") is not None,
        editors_choice=_ng_if(card, "listing.editors_choice") is not None,
        confidential=_ng_if(card, "listing.confidential") is not None,
        verified_listing=_ng_if(card, "listing.display_verification_badge") is not None,
        image_url=image_url,
    )


def rows_from_dom(html: str, base_url: str, category: Optional[str] = None,
                  page: Optional[int] = None) -> List[Product]:
    soup = BeautifulSoup(html, "html.parser")
    rows, seen = [], set()
    position = 0
    for div in soup.find_all("div", id=True):
        m = _CARD_ID_RE.match(div["id"])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        position += 1
        rows.append(_row_from_card(div, m.group(1), base_url, category,
                                   page, position))
    return rows


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------
def parse_products(html: str, base_url: str, category: Optional[str] = None,
                   page: Optional[int] = None) -> List[Product]:
    """Rows for one listing page: the embedded JSON first, the cards second.

    When both views are present the rows come from the JSON and are marked
    `state+dom` once the rendered cards are confirmed to describe the same
    listings in the same order. A DISAGREEMENT is not repaired silently: the
    JSON still wins (it is what the grid is rendered from) and the mismatch is
    logged, because overwriting a correct row is worse than leaving one
    uncorrected.
    """
    state = parse_state(html)
    total = state_total_results(state)
    if state is not None and total == 0:
        # Flippa answers a zero-match query with five promoted listings. They
        # are placements, not matches — see state_results().
        logger.info("Flippa reports 0 matches for this query; the %d promoted "
                    "listing(s) it returns alongside are not results and are "
                    "not reported.", len(state_results(state)))
        return []

    if state is not None and state_results(state):
        rows = rows_from_state(state, base_url, category, page)
        dom_ids = [m for m in re.findall(r'id="listing-(\d+)"', html)]
        if dom_ids:
            state_ids = [r.sku for r in rows]
            if dom_ids == state_ids:
                for row in rows:
                    row.price_source = "state+dom"
            else:
                logger.warning(
                    "The rendered grid (%d cards) does not match the embedded "
                    "listing JSON (%d records) — reporting the JSON, which is "
                    "what the grid is rendered from, and leaving price_source "
                    "as 'state'.", len(dom_ids), len(state_ids))
        return rows

    rows = rows_from_dom(html, base_url, category, page)
    if rows:
        logger.info("No embedded listing JSON in this response — parsed %d "
                    "rendered card(s) instead. Columns the card does not show "
                    "(revenue, sale method, status) stay null on these rows.",
                    len(rows))
    return rows
