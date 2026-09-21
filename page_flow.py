"""
page_flow.py
-------------
What kind of page came back, and what a run should do about it.

flippa.com answers a listing request in five distinguishable ways, and four of
them want a different response — which is the test this family's template sets for
adding this module rather than writing the triage three times, once per
engine, and letting the three drift:

    content      the listing JSON is there with records in it        parse
    empty        the site says so itself: metadata.totalResults == 0  stop, exit 4
    exhausted    past the last page: results == [] with a total > 0   stop, complete
    unpainted    served, but the data has not arrived in this
                 response yet — the SPA shell                         wait, then retry
    blocked      a challenge, a refusal status, or a page that is
                 not built out of Flippa's own assets                 rotate/solve, exit 3

The decision is DATA (`STATE_POLICY`), not three copies of an if-chain, so an
engine cannot quietly disagree with its twins about whether a page is worth
retrying or worth paying for.

**Signal order is by how much a signal proves, not by how cheap it is**: Flippa's own answer about the result set outranks every
heuristic, so a real page that happens to reference few assets cannot be
reported as blocked. The asset-count heuristic only ever runs on a response
that has already failed to produce any listing data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from product_parser import (asset_reference_count, count_cards,
                            detect_bot_challenge, parse_state, state_results,
                            state_total_results, MIN_ASSET_REFERENCES)

CONTENT = "content"
EMPTY = "empty"
EXHAUSTED = "exhausted"
UNPAINTED = "unpainted"
BLOCKED = "blocked"

# HTTP statuses that are a refusal rather than a page. Flippa answers a normal
# request with 200 (measured repeatedly on 2026-09-19, including from a
# datacentre address), so anything in here is the primary blocked signal where
# a status is available at all — a browser engine often has none.
REFUSAL_STATUSES = (401, 403, 405, 406, 429, 503)


# What a full page of this site holds. Flippa serves 25 listings per page on
# every capture taken (search, category shortcuts, filtered search, page 2,
# page 99), and the site's own metadata says how many matched in total — so
# "did this page bring back everything it should have?" is ARITHMETIC here,
# not a threshold or a guess (measured elsewhere in this family: where a site publishes the
# numbers, a gap is arithmetic).
PAGE_SIZE = 25


def expected_records(total_results, page_num: int, page_size: int = PAGE_SIZE):
    """How many records page `page_num` should hold, or None if unknowable.

    None when the site published no total, or when the total is its 10000 cap
    ("10,000+" in the UI) — a cap is not a count, and treating it as one would
    report a phantom shortfall on every large listing.
    """
    if not isinstance(total_results, int) or total_results <= 0:
        return None
    if total_results >= 10000:
        return None
    remaining = total_results - page_size * (page_num - 1)
    if remaining <= 0:
        return 0
    return min(page_size, remaining)


def shortfall(total_results, page_num: int, got: int, page_size: int = PAGE_SIZE):
    """A message naming a page that came back short, or None.

    A page that returns 18 of the 25 listings the site's own count says are
    there is the signature of a snapshot taken mid-render — and it is exactly
    the failure that otherwise looks like a successful run with less data.
    """
    expected = expected_records(total_results, page_num, page_size)
    if expected is None or got >= expected:
        return None
    return (f"page {page_num} returned {got} listing(s) where the site's own "
            f"count of {total_results} match(es) implies {expected} — the page "
            f"may have been read before it finished loading")


@dataclass(frozen=True)
class PagePolicy:
    """What to do about a page in one state."""
    retry: bool           # fetching it again could plausibly change the answer
    wait_first: bool      # give the page time to paint before retrying
    rotate_exit: bool     # a different proxy exit is what might help
    may_solve: bool       # a captcha here is worth paying to solve
    blocked: bool         # counts as exit 3 if the run ends with nothing
    complete: bool        # the listing genuinely ended here


# Deliberately consulted by every engine — a constant nothing reads is the
# same defect as dead code, so the engines take their retry
# budget, their rotation decision and their solve decision from HERE and
# nowhere else.
STATE_POLICY = {
    CONTENT:   PagePolicy(retry=False, wait_first=False, rotate_exit=False,
                          may_solve=False, blocked=False, complete=False),
    EMPTY:     PagePolicy(retry=False, wait_first=False, rotate_exit=False,
                          may_solve=False, blocked=False, complete=True),
    EXHAUSTED: PagePolicy(retry=False, wait_first=False, rotate_exit=False,
                          may_solve=False, blocked=False, complete=True),
    # Served but not painted is NOT a retry-first case: the page is the SPA
    # shell and the data arrives into it, so waiting is what helps and a
    # reload throws away the wait. Tokopedia's live run spent
    # a whole fetch rediscovering this.
    UNPAINTED: PagePolicy(retry=True, wait_first=True, rotate_exit=False,
                          may_solve=False, blocked=False, complete=False),
    BLOCKED:   PagePolicy(retry=True, wait_first=False, rotate_exit=True,
                          may_solve=True, blocked=True, complete=False),
}


@dataclass
class PageState:
    """The classification of one response, with the evidence behind it."""
    state: str
    reason: str
    vendor: Optional[str] = None
    total_results: Optional[int] = None
    record_count: int = 0
    card_count: int = 0
    status_code: Optional[int] = None

    @property
    def policy(self) -> PagePolicy:
        return STATE_POLICY[self.state]

    def __str__(self) -> str:
        return f"{self.state} ({self.reason})"


def classify(html: Optional[str], status_code: Optional[int] = None,
             url: Optional[str] = None) -> PageState:
    """Classify one response. `status_code` is optional — a browser rarely has one.

    Ordered by how much each signal proves:

    1. Flippa's own embedded listing JSON, which answers the question outright
       — including "this query matches nothing", which no interstitial could
       fake.
    2. Rendered cards, for a page a browser has navigated inside the app.
    3. A refusal status.
    4. A challenge marker, and only now: a marker scan on a page that HAS
       produced listing data would only ever produce a false positive, since
       the 2Captcha Scraping Browser's own extension injects captcha markup
       into every page it loads.
    5. Whether the response is built out of Flippa's own assets at all. This
       is the one that classifies Chromium's network-error page correctly —
       it carries the site's hostname in its <title>, so every text marker
       reads it as a real page.
    """
    html = html or ""
    state = parse_state(html)
    total = state_total_results(state)
    records = len(state_results(state))
    cards = count_cards(html)

    if state is not None and total == 0:
        return PageState(EMPTY, "the site reports 0 matches for this query",
                         total_results=0, record_count=records,
                         card_count=cards, status_code=status_code)
    if records:
        return PageState(CONTENT, f"{records} listing record(s) in the page's own JSON",
                         total_results=total, record_count=records,
                         card_count=cards, status_code=status_code)
    if state is not None and total:
        return PageState(EXHAUSTED,
                         f"no records on this page, past the end of {total} result(s)",
                         total_results=total, card_count=cards,
                         status_code=status_code)
    if cards:
        return PageState(CONTENT, f"{cards} rendered card(s), no embedded JSON",
                         total_results=total, card_count=cards,
                         status_code=status_code)

    if status_code in REFUSAL_STATUSES:
        return PageState(BLOCKED, f"HTTP {status_code}", vendor="http",
                         status_code=status_code)

    vendor = detect_bot_challenge(html)
    if vendor:
        return PageState(BLOCKED, f"a {vendor} challenge page", vendor=vendor,
                         status_code=status_code)

    assets = asset_reference_count(html)
    if assets < MIN_ASSET_REFERENCES:
        return PageState(
            BLOCKED,
            f"the response references Flippa's own assets {assets} time(s) "
            f"(a served page references them hundreds of times), so it was not "
            f"built by the site — an interstitial or the browser's own error page",
            vendor="unknown", status_code=status_code)

    return PageState(UNPAINTED,
                     "built out of the site's own assets but carrying no "
                     "listing data yet — the app shell, still to paint",
                     status_code=status_code)
