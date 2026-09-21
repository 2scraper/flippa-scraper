# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[SemVer](https://semver.org/) as closely as a CLI toolkit can: a PATCH release
means fixes, not that every flag is frozen. A behaviour-changing default in a
patch release is announced at the top of its notes rather than discovered from
a bill.

## [0.1.0] — 2026-09-21

First release of the rewritten scraper. Everything below is a difference
from the previous, unreleased version of this code, with the measurement
that justified the change.

### Added

- **Flippa's own embedded listing JSON (`const STATE = {...}`) as the primary
  parse path.** It is in the HTML the site serves, with no JavaScript
  executed, and carries all 25 listings per page as numbers. The rendered
  cards became the fallback, and the two were verified to agree on all 22
  shared columns across all 25 rows of a real capture.
- **`page_flow.py`** — one classification of what a page IS (content, empty,
  past the end, served-but-unpainted, blocked) and one policy table
  (`STATE_POLICY`) shared by every engine, so three copies of the same
  if-chain cannot drift.
- **`proxy_pool.py`, `env_config.py`, `fingerprint_client.py`,
  `diff_runs.py`** — proxy pools with rotation and credential masking, `.env`
  loading with documented precedence, the Fingerprint API client, and a
  run-to-run diff keyed on the listing id.
- **Run metadata sidecar** (`<out>.meta.json`): status, stop reason, WHICH
  pages failed by number, the site's own `total_results`, and whether
  pagination was addressable.
- **`--concurrency N`**, where each worker owns its browser and its own proxy
  exit, dispatch stops at the end of the listing, and unattempted pages are
  reported rather than counted as failed.
- **A 327-check offline suite** and `.github/ci_checks.py`, invoked from both
  CI and the suite, plus a daily 3-page canary.
- `page` and `position` columns, with the pair asserted unique across a
  multi-page run.

### Fixed

- **Pagination never worked.** The scraper constructed `?page=N`, which
  Flippa's own JavaScript deletes (`t.page && delete t.page`); the correct
  parameter is `?page[number]=N`. Every multi-page run before this returned
  exactly 25 rows — one page — and exited 0. Pagination is now both corrected
  and VERIFIED at runtime: page 2 must carry listings page 1 did not, or the
  run reports `partial` instead of a complete-looking result.
- **Negative net profit parsed as null.** Flippa writes a loss as
  `-USD $315 p/mo`, and the currency was stripped with a `^`-anchored pattern,
  so the leading minus made the whole field fail. 2 of 25 rows on the
  2026-08-27 capture were affected.
- **Badges were read from card TEXT.** `sponsored`, `confidential` and a
  `status` matched against the word "Open" all fired on a seller's own summary
  containing those words. They are now read from the Angular `ng-if`
  attributes, which name the field they render.
- **`0.0` reported as a valuation multiple.** The site's JSON carries `0.0`
  where it shows no multiple at all; that is now `null`, on 12 of 75 rows in
  the 2026-09-19 run.
- **The Scraper API engine was reported as not working against this site.** It
  waited for a CSS class that only appears once the grid has PAINTED, and
  timed out on responses that already contained all 25 listings.
- **`asyncio.get_event_loop()` in the pyppeteer engine** — since Python 3.12
  there is no implicit loop in the main thread, so the engine died before its
  first fetch. Found by running it, not by reading it.
- **A challenge marker that matches every good page.** The inherited marker
  set contained `challenge-platform`, and Cloudflare's JS-detections tag
  (`/cdn-cgi/challenge-platform/scripts/jsd/main.js`) is on every page Flippa
  serves — it would have reported exit 3 on every successful run.
- **Credentials in exception text.** The legacy captcha endpoint and the
  Fingerprint API both take the key as a query parameter, and `requests` puts
  the full URL into every error it raises. Both paths now redact before
  raising — globally, not on the first occurrence — and the redaction covers
  every URL scheme, `socks5://` included.
- **`--fp-tags` defaulted to a three-tag list**, which the Fingerprint API
  rejects with HTTP 400. It is now one OS-family tag.
- **A copied `.env.example` read as configured.** Any value still carrying
  `{braces}` is now treated as unset, and an empty value is unset without a
  warning about nothing.
- **A remote browser's password printed in a traceback.** Measured live on
  2026-09-19 against a Scraping Browser endpoint that answered 401: Playwright
  put the full `ws://user:pass@…` endpoint into the exception message and into
  a four-line call log beneath it — five occurrences of the password in one
  traceback, and the engine let it through untouched. All three engines now
  wrap the remote connect and redact any traceback before printing it, and the
  offline suite pins both.
- **A dead proxy reported as a slow site.** An exit whose password had been
  rotated answered `407 Proxy Authentication Required` in 0.2s to a plain
  request, while the same exit under Chromium produced only navigation
  timeouts — three 60s attempts per page, with the proxy never mentioned in
  the log. Every engine now sends one small request through the exit before
  launching a browser: a rejected or unusable exit ends the run in under a
  second with exit 2 and a message naming the proxy, a slow one only warns,
  because the target may be what is slow.
- **A bought captcha token thrown away.** `--solve-captcha always` injected the
  token and then reloaded the page, which discards it: a form reads its token
  at submit time, not from a cookie. Measured on `/signup`, which carries a
  real Turnstile. A page that was NOT blocked is no longer reloaded, so the
  token stays where it was written — and, since nothing here submits a form,
  the default `when-blocked` still refuses to buy one at all.

### Verified against the live APIs (2026-09-19)

- Captcha solving: a real Cloudflare Turnstile on `/signup`, detected from the
  static markup with its live sitekey and solved through
  `TurnstileTaskProxyless` in about 5 seconds. Twice. No form was submitted.
- The Scraper API: two billable tasks at $0.0005 each, 50 listings, every
  column matching the browser engines' except `price_source`.
- Proxies: a 2-page run through `eu.proxy.2captcha.com:2334`, 50 listings,
  credentials never in argv.
- The canary, dispatched by hand on 2026-09-21: 75 listings across 3 pages
  from a bare GitHub runner — a datacentre address with no proxy and no key —
  and every threshold in its sanity check met. The runner-IP caveat in the
  workflow's own comment stands; it simply did not bite here.
- The Fingerprint API answered `403` for the key used here — a separate
  subscription — so that path remains unverified rather than assumed.
- The Scraping Browser API, over a CDP URL copied from the Browser API
  dashboard: 75 listings across 3 pages, `Captcha.setAutoSolve` enabled on
  connect, `--concurrency` refused with the profile-lock reason, and data
  identical to the local-browser run bar three rows' `position`. An endpoint
  assembled BY HAND from the documented URL shape answers `401 deny_no_user`
  — the URL is copied, not built, and that 401 said nothing about the
  account.

### Removed

- The removed local-solver flag and the hardcoded `127.0.0.1` endpoint behind
  it. That endpoint was a placeholder for a product that does not exist under
  that name, so the flag could not work for anyone who set it — and a flag
  that cannot succeed is worse than a missing feature, because it reads as an
  option. The real "the browser solves it for you" path is
  `Captcha.setAutoSolve` over the Scraping Browser API. The offline suite
  asserts the flag stays gone.
- The JSON-LD parse path. A flippa.com search page carries two `ld+json`
  blocks, a `WebPage` and an `FAQPage`, and neither contains a listing — the
  code was dead and looked load-bearing.
