# Troubleshooting

Ordered by how often each one actually happens. Every number here was measured
on the date given, from an ordinary residential connection in Europe unless
stated otherwise.

## The run wrote 25 rows and I asked for 3 pages

Read `<out>.meta.json` first. If `stop_reason` is `pagination_not_addressable`,
the run deliberately stopped: it fetched page 2, found nothing page 1 did not
already have, and refused to report a complete-looking result holding one page.

That is the check, not the bug. The bug it guards against is real and was in
this repo: flippa.com's search is an Angular app whose own code **deletes** a
bare `?page=` parameter, so `?page=2` used to land back on page 1, the run
deduplicated the repeat away, and every "3-page" run returned exactly 25 rows
and exit 0. The convention this repo uses instead comes from the site's own
JavaScript:

    ?page[number]=2        (URL-encoded: ?page%5Bnumber%5D=2)

If you see `pagination_not_addressable` today, that convention has changed —
please open a "Site changed" issue with the URL.

## Exit 4, and the output file was not written

A run that finds nothing writes nothing, so a failure cannot overwrite last
night's good data with `[]`. Check `total_results` in the sidecar:

* `total_results: 0` — Flippa itself says the query matches nothing. That is an
  answer. Pass `--allow-empty` if an empty file is what you want.
* no sidecar at all — the run wrote no output, so it wrote no metadata either
  (a `"failed"` sidecar beside the previous run's good data would contradict
  it). The log line says which page failed and why.

**A zero-match query still returns five listings**, and this scraper does not
report them. Measured 2026-09-19: `?query[keyword]=<nonsense>` answers with
`totalResults: 0` and the five promoted listings that lead the unfiltered
search page. They are placements, not matches.

## Exit 2 — "the proxy exit … cannot be used"

The run stopped before launching a browser, because one small request through
that exit failed in a way another attempt cannot fix. The usual cause is the
one the message names: the password was rotated on the provider's side and
`.env` still holds the old one. `407` in the detail means exactly that.

The check exists because the browser does NOT report this usefully. Measured
2026-09-21 on a rotated 2Captcha proxy: `requests` said
`407 Proxy Authentication Required` in 0.2s, while Chromium through the same
exit produced three 60-second navigation timeouts per page and never
mentioned the proxy at all. A timeout and a dead exit want opposite
responses, so the exit is checked first.

A slow exit is not treated this way — it warns and the run continues, since
the target may be what is slow.

## Exit 3 — blocked

flippa.com sits behind Cloudflare. On 2026-09-19 it served the full search
page to plain `curl` with an ordinary user agent, six times, with no proxy and
no key — so exit 3 is not the normal state of affairs and is worth reading
properly:

* The log names the signal: an HTTP refusal status, a challenge marker, or
  "the response references Flippa's own assets N times". The last one is how
  Chromium's own network-error page is classified correctly — it carries
  `flippa.com` in its `<title>`, so every text marker reads it as a real page.
* A datacentre address is the likeliest cause. Try `--proxy`, or the Scraping
  Browser API via `--cdp-endpoint`.
* `--dump-html dump.html` writes exactly what the parser was given.

## The multiple does not match the numbers beside it

`profit_multiple` is Flippa's own figure, not a computed one, and it does not
always equal `price ÷ (12 × monthly_profit)`. Measured 2026-09-19 across 63
listings that show a multiple: 53 agree within 0.15, 10 do not (one example:
price $9,082, net profit $386/mo, Flippa's multiple 2.6, the computed figure
1.96). The site appears to compute it over a different profit window, so this
repo reports what the site publishes and does not "fix" it. It is a trap that
looks like a parsing bug, which is why it is written down rather than asserted.

## monthly_profit is negative

That is the listing, not the parser. Flippa writes a loss-making business's net
profit as `-USD $315 p/mo`, and 2 of the 25 listings on the 2026-08-27 capture
were negative. An earlier version of this parser returned `null` for exactly
those rows.

## profit_multiple and revenue_multiple are null on some rows

The site shows no multiple for those listings (`show_multiple: false`), and its
JSON carries `0.0` there. Reporting `0.0` would be a fabricated valuation, so
the column is null. Measured 12 of 75 rows on 2026-09-19.

## Selenium: SessionNotCreatedException against a Scraping Browser endpoint

Expected, and refused up front with the reason. Chromedriver's
`debuggerAddress` takes a bare `host:port` and has nowhere to put a password,
while Playwright's `connect_over_cdp` and pyppeteer's `browserWSEndpoint` take
a full `ws://user:pass@host:port`. Use either of those engines instead.

Selenium also cannot authenticate a `--proxy`: the credentials are stripped and
the run warns rather than pretending.

## pyppeteer: "Browser closed unexpectedly"

pyppeteer downloads a Chromium from 2018 and launches that. On a current macOS
it does not start. Point it at a browser that does:

    PYPPETEER_EXECUTABLE_PATH="$HOME/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" \
      python3 puppeteer_scraper.py --url "..."

An environment variable rather than a flag, so the three engines keep exactly
the same flag set.

## A captcha is reported on a page that clearly has none

Two real causes, both handled here, both worth knowing if you are modifying the
detector:

* **The Scraping Browser's own extension injects captcha markup into every
  page it loads**, including `data-ts-input="cf-turnstile-response"`. Every
  detector in this repo strips `chrome-extension://` script tags first.
* **Flippa ships an empty `<captcha-widgets>` element on every page.** It is a
  mount point, not a challenge: on `/signup` it holds a real Turnstile widget,
  on a listing page it is empty. The detector asks whether it has CONTENT.

## `--fingerprint` seems to do nothing

Run `python3 fingerprint_client.py --explain`, with the key exported as
`TWOCAPTCHA_KEY`. It prints, field by field, which response key each setting
came from and which settings could not be applied at all.

If it answers **403**, the key is not subscribed to fingerprints — that is a
separate product from captcha solving, and a key with a healthy solving
balance answers 403 here (measured 2026-09-19). The field mapping in this repo
has therefore never been checked against a real response, which the module
docstring says plainly rather than implying otherwise.

## Solving a captcha on `/signup` does not get you anything

It is not meant to. `/signup` carries a real Turnstile, and `--solve-captcha
always` will detect and solve it — but a form's token is spent at submit time,
and this project never submits a form. The token is injected into the page's
own response field and the page is deliberately NOT reloaded (a reload
discards it). The default, `--solve-captcha when-blocked`, does not buy one at
all unless the listings are genuinely unreachable without it.
