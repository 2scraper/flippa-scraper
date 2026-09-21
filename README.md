# flippa-scraper

[![release](https://img.shields.io/github/v/release/2scraper/flippa-scraper?label=release)](https://github.com/2scraper/flippa-scraper/releases)
[![tests](https://github.com/2scraper/flippa-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/flippa-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/flippa-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/flippa-scraper/actions/workflows/canary.yml)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](pyproject.toml)
[![licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![engines](https://img.shields.io/badge/engines-playwright%20%7C%20pyppeteer%20%7C%20selenium%20%7C%20scraper--api-lightgrey)](#four-ways-to-run-it)
[![runs without an account](https://img.shields.io/badge/runs%20without%20an%20account-yes-brightgreen)](#what-the-paid-products-buy-you)

A working scraper for **flippa.com** listing pages — the marketplace for
buying and selling online businesses, websites, apps and domains. Any
`/search` URL with any combination of `filter[...]` parameters, and the
category shortcuts (`/websites`, `/apps`, `/domains`, `/buy/sitetype/saas`).
Four engines, one parser, JSON and CSV out.

Part of the [2scraper](https://github.com/2scraper) family of open-source,
single-site scrapers.

**Status: live-verified on 2026-09-19 and 2026-09-21.** Playwright, pyppeteer
and Selenium each ran against `?filter[property_type]=saas` and returned
byte-identical data — 75 listings across 3 pages, about 10 seconds, exit 0.
The daily canary passed on its first manual dispatch **from a bare GitHub
runner**: no proxy, no key, a datacentre address, 75 listings and
`status: complete`. 327 offline checks, no network required.

## What it extracts

One row per listing, 33 columns. The interesting ones:

| Column | What it is |
|---|---|
| `sku` | Flippa's own numeric listing id — the key `diff_runs.py` compares on |
| `url` | `https://flippa.com/<id>` |
| `title` | The card's heading, e.g. `SaaS | Health and Beauty` |
| `description` | The seller's own one-line summary |
| `price` / `currency` | Asking price. Every listing measured so far is priced in USD, and the column is `null` rather than a guess if that ever changes |
| `original_price` / `discount_pct` | Present when the price was reduced. The discount is **computed from the two prices**, not read off the site's "Reduced 38%" badge |
| `asset_type` / `industry` / `monetization` | SaaS, Ecommerce, Domain… / Health and Beauty, Business… / Ads, Services & Subscriptions… |
| `monthly_profit` / `monthly_revenue` | Net profit and revenue per month. **Profit can be negative** and is reported that way |
| `profit_multiple` / `revenue_multiple` | Flippa's own valuation multiples, `null` where the site shows none |
| `age_text` / `country` | As the site writes them: "3 years", "MI, United States" |
| `status` / `sale_method` / `bid_count` | open/won, classified/auction, bids where it is an auction |
| `broker_name` | Where a broker is named |
| `managed_by_flippa` / `sponsored` / `editors_choice` / `confidential` / `verified_listing` | Badges, read from the site's own field names rather than from card text |
| `page` / `position` | Where the row sat in the run. The PAIR is unique — `position` restarts at 1 on every page |
| `price_source` | `state` / `state+dom` / `dom` — which view produced the row (see below) |

Plus `source`, `scraped_at`, `category` (your `--category` label or one taken
from the URL) and `image_url`. The full row is in
[`sample_output.json`](sample_output.json), cut from the real run above.

Three columns the rest of this family has are deliberately **absent** here,
each with the measurement behind it: `brand` (Flippa sells businesses, not
branded goods — 0 of 25 rows on every capture), `rating` (a listing card shows
none; the embedded JSON has an `app_rating` on 1 of 25 rows and it rates the
app, not the listing) and `in_stock` (not a concept here — `status` and
`sale_method` carry what the marketplace actually says).

## How it reads the page, and why that matters

Flippa's search results are an Angular app, and the obvious conclusion — "this
needs a rendered browser" — is **wrong**, which is the single most useful fact
in this repo.

The page Flippa SERVES already contains every listing, as its own JSON:

```html
<script>
  const STATE = {"results":[{"id":"12857417","price":436829,
                 "original_price":927250,"profit_average":"13,202", ...}],
                 "metadata":{"totalResults":724}};
</script>
```

So:

1. **Primary path — that embedded JSON.** Numeric, complete, present in the
   first HTTP response with no JavaScript executed. There is no product
   JSON-LD on this site at all: a search page carries exactly two
   `application/ld+json` blocks, a `WebPage` and an `FAQPage`, and neither
   contains a listing.
2. **Fallback path — the rendered cards** (`<div id="listing-NNN">`), for a
   page a browser has navigated inside the app. Verified against the primary
   path on a real 25-card capture: **the two agree on all 22 shared columns,
   for all 25 rows.**
3. `price_source` on every row says which produced it, so two runs that read
   the page differently cannot look like a price change.

A card's boundary is the site's own `id` attribute, not a CSS class (Flippa's
are Tailwind build output) and not "widen up from a product link" — a real
card holds three links to the same listing, so that heuristic never leaves the
anchor.

## Pagination, and the bug this repo used to have

**Flippa publishes no next-page link.** The control is
`<span ng-click="pageChangeHandle(2)">2</span>` with no `href`, so there is
nothing to follow. Pages are addressed by URL instead — and the convention is
not the obvious one. From the site's own shipped JavaScript:

```js
e.currentPage > 1 ? t["page[number]"] = e.currentPage : …,
t.page && delete t.page          // a bare ?page= is DELETED
```

So `?page=2` is discarded by the app, and every multi-page run this repo did
before that was read out of the bundle returned exactly 25 rows and reported
success. The correct form is `?page[number]=2`, and **every run verifies it**:
page 2 is fetched on its own and checked for listings page 1 did not have. If
it has none, the run stops and reports `partial` with
`stop_reason: pagination_not_addressable` rather than a complete-looking
result holding one page.

`--concurrency N` fetches pages 3..N through N workers, each with its own
browser and its own proxy exit, and only after that check has passed.

## Four ways to run it

| Script | Engine | Live status, 2026-09-19 |
|---|---|---|
| `playwright_scraper.py` | **Playwright** (recommended) | 75 listings / 3 pages / ~10s / exit 0 |
| `puppeteer_scraper.py` | pyppeteer | 50 listings / 2 pages, identical data. Needs `PYPPETEER_EXECUTABLE_PATH` on modern macOS — its bundled Chromium is from 2018 |
| `selenium_scraper.py` | Selenium + Chrome | 50 listings / 2 pages, identical data. Two page-1 loads timed out at 60s first and the retry carried it |
| `scraper_api_client.py` | 2Captcha Scraper API (no local browser) | 50 listings / 2 pages, identical data. Two billable tasks at $0.0005 each |

All three browser engines produced **byte-identical rows** (`scraped_at`
aside) for the same 50 listings. They share the parser, the page-state
policy and the exit-code mapping precisely so that stays true.

## Install and run

```bash
pip install -r requirements.txt                    # core: bs4 + requests
pip install -r requirements-playwright.txt         # then ONE engine
playwright install chromium

python3 playwright_scraper.py \
  --url "https://flippa.com/search?filter%5Bproperty_type%5D=saas" \
  --pages 3 --out saas_listings --format both
```

Install exactly one engine per environment: playwright and pyppeteer pin
incompatible `pyee` versions, and pyppeteer and selenium collide on
`urllib3`. Use a virtualenv per engine if you want more than one.

Credentials live in `.env`, never on a command line — a secret in `argv` is
readable by anything that can run `ps` and lands in shell history:

```bash
cp .env.example .env
python3 env_config.py     # says what was picked up, without printing it
```

## What the paid products buy you

**State it plainly: you do not need any of them for this site.** Every number
in this README was measured with no API key, no proxy and no Scraping Browser
— including six plain `curl` fetches that each came back with 25 complete
listings. flippa.com is behind Cloudflare, but it served an ordinary request
from an ordinary connection every time it was asked on 2026-09-19.

What the four 2Captcha products (one key, billed separately) actually buy —
each line says whether it was run against the live API on 2026-09-19:

* **Proxies** (`--proxy`, `--proxy-file`) — volume from many addresses, and a
  specific country. `--proxy-rotate per-page` relaunches the browser on each
  new exit, because carrying a session across addresses is a stronger signal
  than either address alone. **Verified:** a 2-page run through
  `eu.proxy.2captcha.com:2334` returned 50 listings, exit 0, with the
  credentials passed through Playwright's own fields and only `host:port` in
  the browser's argv.
* **Captcha solving** (`--twocaptcha-key`) — for the challenge this site does
  configure: `/signup` carries a real Cloudflare Turnstile widget. **Verified
  twice:** the static detector read the live sitekey, `TurnstileTaskProxyless`
  returned a token in about 5 seconds, and the token was written into the
  page's own response field. Nothing submits the form. Listing pages render no
  challenge at all, which is why the default `--solve-captcha when-blocked`
  refuses to pay when the listings are already readable — and why
  `--solve-captcha always` on a form page buys a token a scraper has no use
  for.
* **The Scraper API** (`scraper_api_client.py`) — no browser at all.
  **Verified:** two billable tasks at $0.0005 each returned 50 listings whose
  every column matches the browser engines', bar `price_source` — `state`
  there against `state+dom` here, because a browserless fetch has no rendered
  card to cross-check against. That is the column doing its job.
* **The Scraping Browser API** (`--cdp-endpoint`) — no browser infrastructure
  of your own, a consistent device identity, a chosen exit country, and
  `Captcha.setAutoSolve`, which clears a challenge inside the browser before
  this code gets a turn. **Not verified:** the key available here has no
  Scraping Browser zone and the endpoint answered `401 deny_no_user`.
* **Fingerprints** (`--fingerprint`) — a device identity for a browser you
  launched yourself. **Not verified:** the same key answered **HTTP 403** at
  `/fingerprint/random`; fingerprints are a separate subscription. Rather than
  imply it works, `fingerprint_client.py --explain` prints field by field what
  it could and could not apply, and the 403 now says exactly that.

## Exit codes

A pipeline branches on these, and every engine produces the same one for the
same situation.

| Code | Means |
|---|---|
| 0 | listings written |
| 1 | crash |
| 2 | bad usage, including an exit that fails its preflight — one small request through the proxy before any browser starts, so a rotated password costs a second rather than three minutes of timeouts |
| 3 | blocked before parsing — a challenge, a refusal status, or a page not built out of Flippa's own assets |
| 4 | ran fine, zero listings (including "the site says this query matches nothing") |
| 5 | the remote API failed (`scraper_api_client.py`) |
| 6 | partial: some pages were gathered, then the run stopped early |

Every run that writes output also writes `<out>.meta.json` with `status`,
`stop_reason`, **which** pages failed by number, the site's own
`total_results`, and whether pagination was addressable. `diff_runs.py`
refuses to compare two runs that are not both `complete`, because a partial
run's un-fetched pages otherwise read as listings that were sold.

## Traps that look like bugs

Read [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for the full list with the
measurements. The four that cost the most time:

* **A "3-page" run returning 25 rows** is pagination, and this repo now
  detects it rather than reporting success — see above.
* **`profit_multiple` does not always equal `price ÷ (12 × monthly_profit)`.**
  53 of 63 listings agreed within 0.15 on 2026-09-19; 10 did not. It is
  Flippa's own figure, computed over a profit window this repo cannot see, and
  it is reported as published rather than "corrected".
* **A zero-match query still returns five listings.** They are promoted
  placements next to `totalResults: 0`, and reporting them would be five
  fabricated rows.
* **`<captcha-widgets>` is on every page.** It is a mount point, empty on a
  listing page and filled on `/signup`. Treating the bare element as a marker
  would report every run as challenged.
* **Solving a FORM's captcha buys nothing here.** A token in a form field is
  spent at submit time, and this scraper never submits anything. A reload
  after injecting would throw it away, so a page that was not blocked is no
  longer reloaded — see `handle_captcha_if_present`.

## Locales

Flippa's own `hreflang` set publishes three: `/search`, `/fr/search` and
`/es/search`. A localised path is not a translation layer on the same
numbers — **it converts the prices**. Measured on 2026-09-19, listing
`12857417` on the same day:

| URL | price | currency | age | industry |
|---|---|---|---|---|
| `/search` | 436,829 | USD | `3 years` | Health and Beauty |
| `/fr/search` | 380,546 | EUR | `3 années` | Santé et beauté |

Both rows are correct for the page they came from. The `currency` column
follows the page rather than defaulting, each engine says which localisation
it is scraping before it starts, and the locale segment is not mistaken for a
category label. Do not mix two locales in one dataset.

The key-data labels are localised too ("Industrie", "Ancienneté", "Profit"),
which is why the profit is read by the SHAPE of its value rather than by an
English label. The rendered-card fallback path is the exception — it still
keys `industry` and `monetization` on the English words, and the suite pins
that limitation rather than half-guarding it.

## Testing

```bash
python3 smoke_test.py      # 327 checks, no network, ~2s
pytest -q                  # the same run, through the pytest entry point
```

The suite asserts values on real captures rather than coverage, checks that
both parse paths agree, that every engine exposes the same flags, that each
engine imports its driver at module level, that every call into a shared
module binds against its real signature, that no name is used undefined, that
the Dockerfile copies what the entrypoint imports, and that nothing
credential-shaped or personal is committed. `.github/workflows/canary.yml`
runs one real 3-page scrape a day, because pagination is never exercised by a
one-page run.

## Licence

MIT. Scrapes public listing pages only: nothing behind a login, nothing that
submits a form, nothing that defeats a protection rather than passing it the
way an ordinary browser does.
