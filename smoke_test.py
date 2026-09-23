#!/usr/bin/env python3
"""
smoke_test.py
--------------
The offline suite: one file of plain functions with inline fixtures, no
pytest, no conftest, no fixtures directory.
`tests/test_smoke.py` wraps this as a single pytest test so `pytest` works as
an entry point without a second copy of the checks.

    python3 smoke_test.py

It must pass with NO engine library installed at all — every
`import playwright_scraper` / `puppeteer_scraper` / `selenium_scraper` is
guarded and the skip is recorded and printed. CI's `engine-smoke` job installs
each engine in its own virtualenv and fails if any group reports skipped,
because "skipped, engine absent" reads identically to a real import error.

**The fixtures are real captures, trimmed and verified.** PAGE_FIXTURE_HTML is
two cards and the matching two records of Flippa's own embedded listing JSON,
cut from `captures/real_search_dump.html` (a rendered /search page,
2026-08-27) with the `<svg>` subtrees removed — they are most of a card's
bytes and carry no data. Before it was committed, every column of both rows
was compared against the same two rows parsed from the untrimmed 1.3 MB
capture and found identical (only `position` differs, since 2 of 25 cards
were kept). The listings are real, public marketplace listings; the capture
carries no session material, no tokens and no personal data — checked, and
`.github/ci_checks.py` keeps checking.

SIGNUP_FIXTURE_HTML is the shape of flippa.com/signup's Turnstile widget with
the sitekey replaced by an obvious placeholder of the same SHAPE: the tests
need the structure, not the site's live key.
"""

import ast
import csv
import inspect
import io
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import asdict

import cli_types
import page_flow
import product_parser
import proxy_pool
import env_config
import output_writer
import captcha_solver
from output_writer import (EXIT_BLOCKED, EXIT_NO_PRODUCTS, EXIT_PARTIAL, Product,
                           dedupe_by_sku, finish_run, save, write_csv)
from product_parser import (category_from_url, discount_pct, page_url,
                            parse_products, parse_state, prices_in,
                            state_total_results, strip_percentages)

REPO = os.path.dirname(os.path.abspath(__file__))

# Engine modules are optional: the suite has to pass with none of the driver
# libraries installed. What is NOT optional is that each engine imports its
# driver at MODULE level — see check_engine_imports_driver_at_module_level.
ENGINES = {}
SKIPPED_GROUPS = []
for _name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
    try:
        ENGINES[_name] = __import__(_name)
    except ImportError as exc:
        SKIPPED_GROUPS.append(f"{_name} ({exc.name or exc})")

PASSED = []
FAILED = []
SKIPPED = []


def check(label, condition):
    (PASSED if condition else FAILED).append(label)
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    return bool(condition)


def skip(label, why):
    """Record a check that could NOT be made.

    Deliberately not `check(label, True)`. A check asserted as True because
    its input was missing is indistinguishable in the output from one that
    actually ran, and the count it inflates is the number the README quotes.
    """
    SKIPPED.append(f"{label} ({why})")
    print(f"  SKIP  {label} — {why}")
    return False


def eq(label, actual, expected):
    ok = actual == expected
    if not ok:
        label = f"{label} (got {actual!r}, expected {expected!r})"
    return check(label, ok)


PAGE_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Online Businesses for Sale | Flippa</title><link rel="canonical" href="https://flippa.com/search"><script src="https://static.flippa.com/assets/legacy-head-23638d7a.js"></script></head><body><div ng-controller="SearchV2_SearchController">
<div class="tw-w-full tw-flex tw-flex-col @3xl:tw-flex-row tw-gap-4 tw-rounded-lg tw-border tw-border-solid tw-border-gray-300 tw-p-4 tw-shadow hover:tw-shadow-lg" id="listing-12857417">
<a class="!tw-no-underline GTM-search-result-card tw-h-full tw-w-full @3xl:tw-max-w-72 tw-shrink-0 tw-flex-col" data-turbo="false" data-turbo-frame="_top" href="https://flippa.com/12857417" ng-href="https://flippa.com/12857417">
<div class="tw-relative tw-group tw-rounded tw-shadow-sm tw-border tw-border-solid tw-border-gray-200">
<img class="tw-aspect-21-9 lg:tw-aspect-video tw-w-full tw-h-full tw-object-cover tw-rounded" data-action="error-&gt;image-fallback#fallback" data-controller="image-fallback" data-image-fallback-default-image-value="https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg" ng-src="https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png" src="https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png"/>
<!-- ngIf: listing.confidential --><div class="ng-scope" ng-if="listing.confidential">
<div class="tw-absolute tw-top-0 tw-left-0 tw-w-full tw-h-full tw-flex tw-items-center tw-justify-center">
<!-- ngIf: listing.invest -->
<!-- ngIf: !listing.invest --><div class="tw-bg-primary-500 tw-p-2.5 tw-text-white tw-text-center tw-rounded-md tw-font-medium tw-font-mon ng-scope" ng-if="!listing.invest">
        Confidential<br/>Sign NDA to view
      </div><!-- end ngIf: !listing.invest -->
</div>
</div><!-- end ngIf: listing.confidential -->
<!-- ngIf: listing.early_access_days_remaining > 0 -->
</div>
<div class="tw-hidden @3xl:tw-flex tw-flex-wrap tw-gap-2.5 tw-mt-2.5">
<!-- ngIf: listing.managed_by_flippa --><span class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-orange-50 tw-text-oxford-900 !tw-text-xs !tw-px-2.5 tw-rounded ng-scope" ng-if="listing.managed_by_flippa">

  Managed by Flippa
</span><!-- end ngIf: listing.managed_by_flippa -->
<!-- ngIf: listing.super_seller -->
<!-- ngIf: listing.broker_seller -->
<!-- ngIf: listing.early_access_days_remaining > 0 -->
<!-- ngIf: listing.sponsored --><span class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-yellow-50 tw-text-yellow-600 !tw-text-xs !tw-px-2.5 tw-rounded ng-scope" ng-if="listing.sponsored">

  Sponsored
</span><!-- end ngIf: listing.sponsored -->
<!-- ngIf: listing.editors_choice --><span class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-purple-50 tw-text-purple-600 !tw-text-xs !tw-px-2.5 tw-rounded ng-scope" ng-if="listing.editors_choice">

  Editor's Choice
</span><!-- end ngIf: listing.editors_choice -->
<!-- ngIf: listing.invest -->
</div>
</a>
<a class="!tw-no-underline GTM-search-result-card tw-space-y-2.5 tw-w-full tw-min-w-0" href="https://flippa.com/12857417" ng-href="https://flippa.com/12857417">
<h6 class="!tw-text-xl !tw-font-semibold tw-font-mon tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0 ng-binding">SaaS | Health and Beauty</h6>
<div class="tw-flex tw-flex-wrap tw-items-center tw-gap-2.5">
<!-- ngIf: listing.display_verification_badge --><div class="tw-z-10 ng-scope" ng-if="listing.display_verification_badge">
<div class="tw-inline-flex tw-flex-wrap tw-items-center tw-gap-2.5 tw-rounded-full tw-border tw-border-solid tw-border-gray-300 tw-px-2.5 tw-py-1.5">
<span class="tw-text-primary-700 tw-text-xs tw-font-semibold tw-font-mon">Verified Listing</span>
<!-- ngIf: listing.manually_vetted --><div class="tw-inline-flex ng-scope" data-title="The Flippa vetting team or the broker has verified this listing to ensure accuracy of the stated financial and operational performance." ng-if="listing.manually_vetted" rel="tooltip">

</div><!-- end ngIf: listing.manually_vetted -->
<!-- ngRepeat: badge in listing.integration_icons --><img class="tw-size-3 tw-shrink-0 ng-scope" data-title="" ng-repeat="badge in listing.integration_icons" ng-src="https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg" rel="tooltip" src="https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg"/><!-- end ngRepeat: badge in listing.integration_icons --><img class="tw-size-3 tw-shrink-0 ng-scope" data-title="" ng-repeat="badge in listing.integration_icons" ng-src="https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg" rel="tooltip" src="https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg"/><!-- end ngRepeat: badge in listing.integration_icons -->
</div>
</div><!-- end ngIf: listing.display_verification_badge -->
<!-- ngIf: listing.country_name --><div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-800 ng-scope" ng-if="listing.country_name">

<span class="ng-binding">France</span>
</div><!-- end ngIf: listing.country_name -->
</div>
<!-- ngIf: listing.scores -->
<div><p class="tw-text-gray-900 tw-text-sm !tw-mb-0 ng-binding"> Profitable B2B SaaS for fitness coaches | €188k ARR run-rate | 94% gross margin | Growing +20% MoM | Proprietary algo &amp; real MOATs| Multilingual EN/FR/ES</p></div>
<div class="tw-flex tw-items-center tw-flex-wrap tw-gap-x-8 tw-gap-y-2.5">
<!-- ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Type</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">SaaS</div>
</div><!-- end ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Industry</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">Health and Beauty</div>
</div><!-- end ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Monetization</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">Services &amp; Subscriptions</div>
</div><!-- end ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Site Age</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">3 years</div>
</div><!-- end ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Net Profit</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">USD $13,202 p/mo</div>
</div><!-- end ngRepeat: data in listing.key_data -->
</div>
</a>
<hr class="@3xl:tw-hidden !tw-m-0 !tw-border-gray-300"/>
<div class="tw-shrink-0">
<div class="tw-flex tw-flex-col tw-h-full tw-justify-between @3xl:tw-items-end @3xl:tw-text-right">
<!-- ngIf: listing.invest -->
<!-- ngIf: !listing.invest --><div class="tw-space-y-2 ng-scope" ng-if="!listing.invest">
<div class="tw-flex tw-items-center @3xl:tw-justify-end tw-gap-2.5 tw-text-sm tw-font-medium tw-font-mon">
<!-- ngIf: listing.bid_count -->
<!-- ngIf: listing.bid_count -->
<!-- ngIf: !listing.bid_count && !listing.open_to_offer --><span class="tw-text-gray-600 ng-binding ng-scope" ng-if="!listing.bid_count &amp;&amp; !listing.open_to_offer">Asking Price</span><!-- end ngIf: !listing.bid_count && !listing.open_to_offer -->
<!-- ngIf: !listing.open_to_offer && listing.partial_sale -->
</div>
<!-- ngIf: listing.open_to_offer -->
<!-- ngIf: !listing.price_dropped && !listing.open_to_offer -->
<!-- ngIf: listing.price_dropped --><div class="ng-scope" ng-if="listing.price_dropped">
<span class="tw-text-gray-500 tw-text-lg tw-font-semibold tw-font-mon tw-line-through"><del class="ng-binding">USD $927,250</del></span>
<div class="tw-flex tw-flex-wrap tw-items-center md:tw-justify-between tw-gap-1.5">
<span class="tw-text-xl tw-font-semibold tw-font-mon tw-text-emerald-600 ng-binding">USD $573,006</span>
<span class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-emerald-50 tw-text-emerald-600 !tw-text-xs !tw-px-2.5 tw-rounded @3xl:tw-order-first ng-binding">

                  Reduced 38%
                </span>
</div>
</div><!-- end ngIf: listing.price_dropped -->
<!-- ngIf: listing.show_multiple --><div class="ng-scope" ng-if="listing.show_multiple">
<a class="tw-flex tw-items-center @3xl:tw-justify-end tw-gap-1 tw-text-primary-700 tw-text-sm" href="/data-insights?buy_sell=searchpage" target="_blank">
                View insights on multiples

</a>
<div class="tw-inline-flex tw-rounded-full tw-items-center tw-gap-1.5 tw-border tw-border-solid tw-border-gray-300 tw-text-xs tw-font-semibold tw-text-gray-500 tw-px-1.5 tw-h-5">
<span>Multiple:</span>
<span class="ng-binding">3.6x Profit</span>
<span class="tw-h-full tw-w-px tw-border-solid tw-border-0 tw-border-l tw-border-gray-300"></span>
<span class="ng-binding">2.9x Revenue</span>
</div>
</div><!-- end ngIf: listing.show_multiple -->
</div><!-- end ngIf: !listing.invest -->
<div class="tw-flex tw-flex-col @sm:tw-flex-row tw-items-center tw-gap-2.5 !tw-mt-5">
<!-- ngIf: (listing.early_access_days_remaining < 1) || (false) --><ng-container class="tw-w-full @3xl:tw-w-32 ng-scope" ng-if="(listing.early_access_days_remaining &lt; 1) || (false)">
<span class="watch_12857417">
<!-- ngIf: listing.watched -->
<!-- ngIf: !listing.watched --><a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-transparent hover:!tw-bg-gray-100 hover:!tw-text-primary-700 focus:tw-ring-gray-100 disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:hover:!tw-bg-transparent disabled:!tw-text-gray-300 disabled:hover:!tw-text-gray-300 disabled:tw-pointer-events-none aria-disabled:tw-border-gray-300 aria-disabled:hover:tw-border-gray-300 aria-disabled:hover:!tw-bg-transparent aria-disabled:!tw-text-gray-300 aria-disabled:hover:!tw-text-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full !tw-text-nowrap tw-border-primary-700 !tw-text-primary-700 tw-w-full ng-scope" data-turbo="true" data-turbo-method="post" href="/watch_item?disabled_title=Watching&amp;enabled_title=Watch&amp;id=12857417&amp;origin=search&amp;return_to=%2F&amp;type=listing&amp;unwatch_classes=GTM-search-unwatch+tw-w-full&amp;view_partial=shared%2Fwatch_v2&amp;watch_classes=GTM-search-watch+tw-w-full&amp;locale=en" ng-if="!listing.watched" rel="nofollow">

    Watch
  </a><!-- end ngIf: !listing.watched -->
</span>
</ng-container><!-- end ngIf: (listing.early_access_days_remaining < 1) || (false) -->
<div class="tw-w-full">
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-w-full !tw-text-nowrap GTM-search-result-card ng-binding" data-turbo="false" data-turbo-frame="_top" href="https://flippa.com/12857417" ng-href="https://flippa.com/12857417" rel="nofollow">
                View Listing
              </a>
</div>
</div>
</div>
</div>
</div>
<div class="tw-w-full tw-flex tw-flex-col @3xl:tw-flex-row tw-gap-4 tw-rounded-lg tw-border tw-border-solid tw-border-gray-300 tw-p-4 tw-shadow hover:tw-shadow-lg" id="listing-12230608">
<a class="!tw-no-underline GTM-search-result-card tw-h-full tw-w-full @3xl:tw-max-w-72 tw-shrink-0 tw-flex-col" data-turbo="false" data-turbo-frame="_top" href="https://flippa.com/12230608" ng-href="https://flippa.com/12230608">
<div class="tw-relative tw-group tw-rounded tw-shadow-sm tw-border tw-border-solid tw-border-gray-200">
<img class="tw-aspect-21-9 lg:tw-aspect-video tw-w-full tw-h-full tw-object-cover tw-rounded" data-action="error-&gt;image-fallback#fallback" data-controller="image-fallback" data-image-fallback-default-image-value="https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg" ng-src="https://static2.flippa.com/blurred_thumbnail_2fbc9403-8b3e-77d6-4d1f-47b76f2baf5e.png" src="https://static2.flippa.com/blurred_thumbnail_2fbc9403-8b3e-77d6-4d1f-47b76f2baf5e.png"/>
<!-- ngIf: listing.confidential --><div class="ng-scope" ng-if="listing.confidential">
<div class="tw-absolute tw-top-0 tw-left-0 tw-w-full tw-h-full tw-flex tw-items-center tw-justify-center">
<!-- ngIf: listing.invest -->
<!-- ngIf: !listing.invest --><div class="tw-bg-primary-500 tw-p-2.5 tw-text-white tw-text-center tw-rounded-md tw-font-medium tw-font-mon ng-scope" ng-if="!listing.invest">
        Confidential<br/>Sign NDA to view
      </div><!-- end ngIf: !listing.invest -->
</div>
</div><!-- end ngIf: listing.confidential -->
<!-- ngIf: listing.early_access_days_remaining > 0 -->
</div>
<div class="tw-hidden @3xl:tw-flex tw-flex-wrap tw-gap-2.5 tw-mt-2.5">
<!-- ngIf: listing.managed_by_flippa -->
<!-- ngIf: listing.super_seller -->
<!-- ngIf: listing.broker_seller -->
<!-- ngIf: listing.early_access_days_remaining > 0 -->
<!-- ngIf: listing.sponsored --><span class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-yellow-50 tw-text-yellow-600 !tw-text-xs !tw-px-2.5 tw-rounded ng-scope" ng-if="listing.sponsored">

  Sponsored
</span><!-- end ngIf: listing.sponsored -->
<!-- ngIf: listing.editors_choice -->
<!-- ngIf: listing.invest -->
</div>
</a>
<a class="!tw-no-underline GTM-search-result-card tw-space-y-2.5 tw-w-full tw-min-w-0" href="https://flippa.com/12230608" ng-href="https://flippa.com/12230608">
<h6 class="!tw-text-xl !tw-font-semibold tw-font-mon tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0 ng-binding">SaaS | Design and Style</h6>
<div class="tw-flex tw-flex-wrap tw-items-center tw-gap-2.5">
<!-- ngIf: listing.display_verification_badge --><div class="tw-z-10 ng-scope" ng-if="listing.display_verification_badge">
<div class="tw-inline-flex tw-flex-wrap tw-items-center tw-gap-2.5 tw-rounded-full tw-border tw-border-solid tw-border-gray-300 tw-px-2.5 tw-py-1.5">
<span class="tw-text-primary-700 tw-text-xs tw-font-semibold tw-font-mon">Verified Listing</span>
<!-- ngIf: listing.manually_vetted --><div class="tw-inline-flex ng-scope" data-title="The Flippa vetting team or the broker has verified this listing to ensure accuracy of the stated financial and operational performance." ng-if="listing.manually_vetted" rel="tooltip">

</div><!-- end ngIf: listing.manually_vetted -->
<!-- ngRepeat: badge in listing.integration_icons --><img class="tw-size-3 tw-shrink-0 ng-scope" data-title="" ng-repeat="badge in listing.integration_icons" ng-src="https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg" rel="tooltip" src="https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg"/><!-- end ngRepeat: badge in listing.integration_icons --><img class="tw-size-3 tw-shrink-0 ng-scope" data-title="" ng-repeat="badge in listing.integration_icons" ng-src="https://static.flippa.com/assets/search/paypal-icon-55288012.svg" rel="tooltip" src="https://static.flippa.com/assets/search/paypal-icon-55288012.svg"/><!-- end ngRepeat: badge in listing.integration_icons --><img class="tw-size-3 tw-shrink-0 ng-scope" data-title="" ng-repeat="badge in listing.integration_icons" ng-src="https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg" rel="tooltip" src="https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg"/><!-- end ngRepeat: badge in listing.integration_icons -->
</div>
</div><!-- end ngIf: listing.display_verification_badge -->
<!-- ngIf: listing.country_name --><div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-800 ng-scope" ng-if="listing.country_name">

<span class="ng-binding">Italy</span>
</div><!-- end ngIf: listing.country_name -->
</div>
<!-- ngIf: listing.scores -->
<div><p class="tw-text-gray-900 tw-text-sm !tw-mb-0 ng-binding">Profitable SaaS with $46K annual revenue, 40% margins, 1100 active subscribers, and 30K-user email list; low maintenance and primed for major scaling</p></div>
<div class="tw-flex tw-items-center tw-flex-wrap tw-gap-x-8 tw-gap-y-2.5">
<!-- ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Type</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">SaaS</div>
</div><!-- end ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Industry</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">Design and Style</div>
</div><!-- end ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Monetization</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">Affiliate Sales</div>
</div><!-- end ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Site Age</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">5 years</div>
</div><!-- end ngRepeat: data in listing.key_data --><div class="ng-scope" ng-repeat="data in listing.key_data">
<div class="tw-text-gray-600 tw-text-xs ng-binding">Net Profit</div>
<div class="tw-text-gray-800 tw-text-sm tw-font-semibold ng-binding">USD $551 p/mo</div>
</div><!-- end ngRepeat: data in listing.key_data -->
</div>
</a>
<hr class="@3xl:tw-hidden !tw-m-0 !tw-border-gray-300"/>
<div class="tw-shrink-0">
<div class="tw-flex tw-flex-col tw-h-full tw-justify-between @3xl:tw-items-end @3xl:tw-text-right">
<!-- ngIf: listing.invest -->
<!-- ngIf: !listing.invest --><div class="tw-space-y-2 ng-scope" ng-if="!listing.invest">
<div class="tw-flex tw-items-center @3xl:tw-justify-end tw-gap-2.5 tw-text-sm tw-font-medium tw-font-mon">
<!-- ngIf: listing.bid_count --><a class="tw-text-gray-600 ng-binding ng-scope" href="/auctions/12230608/bids" ng-href="/auctions/12230608/bids" ng-if="listing.bid_count">
                1 bids
              </a><!-- end ngIf: listing.bid_count -->
<!-- ngIf: listing.bid_count --><span class="tw-text-gray-600 ng-binding ng-scope" ng-if="listing.bid_count">Asking Price</span><!-- end ngIf: listing.bid_count -->
<!-- ngIf: !listing.bid_count && !listing.open_to_offer -->
<!-- ngIf: !listing.open_to_offer && listing.partial_sale -->
</div>
<!-- ngIf: listing.open_to_offer -->
<!-- ngIf: !listing.price_dropped && !listing.open_to_offer --><h5 class="ng-binding ng-scope" ng-if="!listing.price_dropped &amp;&amp; !listing.open_to_offer">USD $17,289</h5><!-- end ngIf: !listing.price_dropped && !listing.open_to_offer -->
<!-- ngIf: listing.price_dropped -->
<!-- ngIf: listing.show_multiple --><div class="ng-scope" ng-if="listing.show_multiple">
<a class="tw-flex tw-items-center @3xl:tw-justify-end tw-gap-1 tw-text-primary-700 tw-text-sm" href="/data-insights?buy_sell=searchpage" target="_blank">
                View insights on multiples

</a>
<div class="tw-inline-flex tw-rounded-full tw-items-center tw-gap-1.5 tw-border tw-border-solid tw-border-gray-300 tw-text-xs tw-font-semibold tw-text-gray-500 tw-px-1.5 tw-h-5">
<span>Multiple:</span>
<span class="ng-binding">2.6x Profit</span>
<span class="tw-h-full tw-w-px tw-border-solid tw-border-0 tw-border-l tw-border-gray-300"></span>
<span class="ng-binding">0.4x Revenue</span>
</div>
</div><!-- end ngIf: listing.show_multiple -->
</div><!-- end ngIf: !listing.invest -->
<div class="tw-flex tw-flex-col @sm:tw-flex-row tw-items-center tw-gap-2.5 !tw-mt-5">
<!-- ngIf: (listing.early_access_days_remaining < 1) || (false) --><ng-container class="tw-w-full @3xl:tw-w-32 ng-scope" ng-if="(listing.early_access_days_remaining &lt; 1) || (false)">
<span class="watch_12230608">
<!-- ngIf: listing.watched -->
<!-- ngIf: !listing.watched --><a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-transparent hover:!tw-bg-gray-100 hover:!tw-text-primary-700 focus:tw-ring-gray-100 disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:hover:!tw-bg-transparent disabled:!tw-text-gray-300 disabled:hover:!tw-text-gray-300 disabled:tw-pointer-events-none aria-disabled:tw-border-gray-300 aria-disabled:hover:tw-border-gray-300 aria-disabled:hover:!tw-bg-transparent aria-disabled:!tw-text-gray-300 aria-disabled:hover:!tw-text-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full !tw-text-nowrap tw-border-primary-700 !tw-text-primary-700 tw-w-full ng-scope" data-turbo="true" data-turbo-method="post" href="/watch_item?disabled_title=Watching&amp;enabled_title=Watch&amp;id=12230608&amp;origin=search&amp;return_to=%2F&amp;type=listing&amp;unwatch_classes=GTM-search-unwatch+tw-w-full&amp;view_partial=shared%2Fwatch_v2&amp;watch_classes=GTM-search-watch+tw-w-full&amp;locale=en" ng-if="!listing.watched" rel="nofollow">

    Watch
  </a><!-- end ngIf: !listing.watched -->
</span>
</ng-container><!-- end ngIf: (listing.early_access_days_remaining < 1) || (false) -->
<div class="tw-w-full">
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-w-full !tw-text-nowrap GTM-search-result-card ng-binding" data-turbo="false" data-turbo-frame="_top" href="https://flippa.com/12230608" ng-href="https://flippa.com/12230608" rel="nofollow">
                View Listing
              </a>
</div>
</div>
</div>
</div>
</div>
</div>
<script>
  const STATE = {"results": [{"id": "12857417", "listing_url": "https://flippa.com/12857417", "title": " Profitable B2B SaaS for fitness coaches | €188k ARR run-rate | 94% gross margin | Growing +20% MoM | Proprietary algo & real MOATs| Multilingual EN/FR/ES", "summary": " Profitable B2B SaaS for fitness coaches | €188k ARR run-rate | 94% gross margin | Growing +20% MoM | Proprietary algo & real MOATs| Multilingual EN/FR/ES", "has_verified_traffic": true, "has_verified_revenue": false, "price": 573006, "bid_count": 0, "sale_method": "classified", "status": "open", "category": "Health and Beauty", "monetization": "Services & Subscriptions", "profit_average": "13,202", "end_at": "2027-05-18T23:16:28+10:00", "super_seller": false, "broker_seller": false, "sponsored": true, "editors_choice": true, "multiple": 3.6, "has_multiple?": true, "revenue_multiple": 2.9, "target_raise_amount": null, "ttm_revenue": null, "confidential": true, "primary_platform": null, "property_name": "Sign NDA to view more details →", "established_at": 3, "country_name": "France", "ready_made": false, "thumbnail_url": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "default_image": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "default_image_url": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "blurred_image_url": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "blurred_or_thumbnail_url": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "property_type": "SaaS", "watched": false, "scores": null, "beta_scores": null, "uniques_per_month": 511, "age_label": "Site Age", "formatted_age_in_years": "3 years", "sale_method_title": "Asking Price", "integrations": ["google_analytics", "stripe", "google_analytics", "google_analytics", "google_analytics"], "integration_icons": [{"tooltip_text": null, "path": "https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg", "provider": "google_analytics"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg", "provider": "stripe"}], "currency_label": "USD $", "protect_listing": false, "display_verification_badge": true, "all_verifications": [{"tooltip_text": "The Flippa vetting team or the broker has verified this listing to ensure accuracy of the stated financial and operational performance.", "path": "https://static.flippa.com/assets/search/verified-user-icon-4370f348.svg", "provider": "verified-listing"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg", "provider": "google_analytics"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg", "provider": "stripe"}], "manually_vetted": true, "early_access_listing": false, "early_access_percentage": "100%", "early_access_days_remaining": 0, "early_access_overlay_title_suffix": "in 0 Days", "early_access_open_at": "May 20, 2026", "viewer_has_early_access": null, "special_tags": true, "hover_image_url": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "basic_info": {"name": "SaaS | Health and Beauty", "hover_image": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "padlocked": true}, "open_listing": false, "confidential_thumbnail_url": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "confidential_overlay_class": "confidential-blurred-image-overlay", "display_confidential_label": "", "annual_organic_traffic": 690, "revenue_average": 16671, "authority_score": 8, "app_rating": null, "managed_by_flippa": true, "managed_by": "owner", "broker_name": null, "broker_avatar_url": null, "listing_category": "saas", "original_price": 927250, "price_dropped": true, "price_dropped_percent": 38, "under_offer": false, "badges": [{"text": "Managed by Flippa", "icon": "icons/library/logo/flippa-square.svg", "variant": "light_orange_oxford_text"}, {"text": "Sponsored", "icon": "icons/library/solid/star.svg", "variant": "light_yellow"}, {"text": "Editor's Choice", "icon": "icons/library/solid/thumbs-up.svg", "variant": "light_purple"}], "listing_watchable": true, "show_multiple": true, "invest": false, "action_class": "primary", "key_data": [{"label": "Type", "value": "SaaS"}, {"label": "Industry", "value": "Health and Beauty"}, {"label": "Monetization", "value": "Services & Subscriptions"}, {"label": "Site Age", "value": "3 years"}, {"label": "Net Profit", "value": "USD $13,202 p/mo"}], "open_to_offer": false, "partial_sale": false, "equity_sale_percentage": "100", "hide_profit": false, "domain_only?": false, "action_button_text": "View Listing", "original_price_text": "USD $927,250", "price_text": "USD $573,006", "target_raise_amount_text": null}, {"id": "12230608", "listing_url": "https://flippa.com/12230608", "title": "Profitable SaaS with $46K annual revenue, 40% margins, 1100 active subscribers, and 30K-user email list; low maintenance and primed for major scaling", "summary": "Profitable SaaS with $46K annual revenue, 40% margins, 1100 active subscribers, and 30K-user email list; low maintenance and primed for major scaling", "has_verified_traffic": true, "has_verified_revenue": false, "price": 17289, "bid_count": 1, "sale_method": "classified", "status": "open", "category": "Design and Style", "monetization": "Affiliate Sales", "profit_average": "551", "end_at": "2026-12-05T02:31:04+11:00", "super_seller": false, "broker_seller": false, "sponsored": true, "editors_choice": false, "multiple": 2.6, "has_multiple?": true, "revenue_multiple": 0.4, "target_raise_amount": null, "ttm_revenue": null, "confidential": true, "primary_platform": null, "property_name": "Sign NDA to view more details →", "established_at": 5, "country_name": "Italy", "ready_made": false, "thumbnail_url": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "default_image": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "default_image_url": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "blurred_image_url": "https://static2.flippa.com/blurred_thumbnail_2fbc9403-8b3e-77d6-4d1f-47b76f2baf5e.png", "blurred_or_thumbnail_url": "https://static2.flippa.com/blurred_thumbnail_2fbc9403-8b3e-77d6-4d1f-47b76f2baf5e.png", "property_type": "SaaS", "watched": false, "scores": null, "beta_scores": null, "uniques_per_month": 1368, "age_label": "Site Age", "formatted_age_in_years": "5 years", "sale_method_title": "Asking Price", "integrations": ["google_analytics", "paypal", "stripe", "google_analytics", "google_analytics", "google_analytics"], "integration_icons": [{"tooltip_text": null, "path": "https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg", "provider": "google_analytics"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/paypal-icon-55288012.svg", "provider": "paypal"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg", "provider": "stripe"}], "currency_label": "USD $", "protect_listing": false, "display_verification_badge": true, "all_verifications": [{"tooltip_text": "The Flippa vetting team or the broker has verified this listing to ensure accuracy of the stated financial and operational performance.", "path": "https://static.flippa.com/assets/search/verified-user-icon-4370f348.svg", "provider": "verified-listing"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg", "provider": "google_analytics"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/paypal-icon-55288012.svg", "provider": "paypal"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg", "provider": "stripe"}], "manually_vetted": true, "early_access_listing": false, "early_access_percentage": "100%", "early_access_days_remaining": 0, "early_access_overlay_title_suffix": "in 0 Days", "early_access_open_at": "Dec 26, 2025", "viewer_has_early_access": null, "special_tags": true, "hover_image_url": "https://static2.flippa.com/blurred_thumbnail_2fbc9403-8b3e-77d6-4d1f-47b76f2baf5e.png", "basic_info": {"name": "SaaS | Design and Style", "hover_image": "https://static2.flippa.com/blurred_thumbnail_2fbc9403-8b3e-77d6-4d1f-47b76f2baf5e.png", "padlocked": true}, "open_listing": false, "confidential_thumbnail_url": "https://static2.flippa.com/blurred_thumbnail_2fbc9403-8b3e-77d6-4d1f-47b76f2baf5e.png", "confidential_overlay_class": "confidential-blurred-image-overlay", "display_confidential_label": "", "annual_organic_traffic": 229, "revenue_average": 3244, "authority_score": 22, "app_rating": null, "managed_by_flippa": false, "managed_by": "owner", "broker_name": null, "broker_avatar_url": null, "listing_category": "saas", "original_price": null, "price_dropped": false, "price_dropped_percent": null, "under_offer": false, "badges": [{"text": "Sponsored", "icon": "icons/library/solid/star.svg", "variant": "light_yellow"}], "listing_watchable": true, "show_multiple": true, "invest": false, "action_class": "primary", "key_data": [{"label": "Type", "value": "SaaS"}, {"label": "Industry", "value": "Design and Style"}, {"label": "Monetization", "value": "Affiliate Sales"}, {"label": "Site Age", "value": "5 years"}, {"label": "Net Profit", "value": "USD $551 p/mo"}], "open_to_offer": false, "partial_sale": false, "equity_sale_percentage": "100", "hide_profit": false, "domain_only?": false, "action_button_text": "View Listing", "original_price_text": null, "price_text": "USD $17,289", "target_raise_amount_text": null}], "metadata": {"totalResults": 745, "timeoutDelay": 300, "totalRequests": 1}, "error?": false};
</script><captcha-widgets></captcha-widgets><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/turnstile/hunter.js" data-ts-input="cf-turnstile-response"></script></body></html>"""

FR_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Entreprises en ligne à vendre | Flippa</title><link rel="alternate" hreflang="fr" href="https://flippa.com/fr/search"/><script src="https://static.flippa.com/assets/legacy-head.js"></script></head><body><div ng-controller="SearchV2_SearchController"></div><script>
  const STATE = {"results": [{"id": "12857417", "listing_url": "https://flippa.com/fr/12857417", "title": " SaaS B2B rentable pour coachs sportifs | ARR annualisé de 188 k€ | Marge brute de 94 % | Croissance de 20 % par mois | Algorithme propriétaire et véritables avantages concurrentiels | Multilingue (EN/FR/ES)", "summary": " SaaS B2B rentable pour coachs sportifs | ARR annualisé de 188 k€ | Marge brute de 94 % | Croissance de 20 % par mois | Algorithme propriétaire et véritables avantages concurrentiels | Multilingue (EN/FR/ES)", "has_verified_traffic": true, "has_verified_revenue": false, "price": 380546, "bid_count": 0, "sale_method": "classified", "status": "open", "category": "Santé et beauté", "monetization": "Services et abonnements", "profit_average": "11 501", "end_at": "2027-05-18T23:16:28+10:00", "super_seller": false, "broker_seller": true, "sponsored": true, "editors_choice": true, "multiple": 2.8, "has_multiple?": true, "revenue_multiple": 2.2, "target_raise_amount": 0, "ttm_revenue": null, "confidential": true, "primary_platform": null, "property_name": "Signer un NDA pour voir plus de détails →", "established_at": 3, "country_name": "France", "ready_made": false, "thumbnail_url": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "default_image": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "default_image_url": "https://static.flippa.com/assets/search/placeholders/saas-ad80efb3.svg", "blurred_image_url": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "blurred_or_thumbnail_url": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "property_type": "SaaS", "watched": false, "scores": null, "beta_scores": null, "uniques_per_month": 517, "age_label": "Ancienneté", "formatted_age_in_years": "3 années", "sale_method_title": "Prix demandé", "integrations": ["google_analytics", "stripe", "google_analytics", "google_analytics", "google_analytics"], "integration_icons": [{"tooltip_text": null, "path": "https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg", "provider": "google_analytics"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg", "provider": "stripe"}], "currency_label": "EUR €", "protect_listing": false, "display_verification_badge": true, "all_verifications": [{"tooltip_text": "L'équipe de vérification Flippa ou le broker a vérifié cette annonce afin d'assurer l'exactitude des performances financières et opérationnelles indiquées.", "path": "https://static.flippa.com/assets/search/verified-user-icon-4370f348.svg", "provider": "verified-listing"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/google_analytics-icon-e61c7587.svg", "provider": "google_analytics"}, {"tooltip_text": null, "path": "https://static.flippa.com/assets/search/stripe-icon-9a799f07.svg", "provider": "stripe"}], "manually_vetted": true, "early_access_listing": false, "early_access_percentage": "100%", "early_access_days_remaining": 0, "early_access_overlay_title_suffix": "en 0 jours", "early_access_open_at": "May 20, 2026", "viewer_has_early_access": null, "special_tags": true, "hover_image_url": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "basic_info": {"name": "SaaS | Santé et beauté", "hover_image": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "padlocked": true}, "open_listing": false, "confidential_thumbnail_url": "https://static2.flippa.com/blurred_thumbnail_28946bf8-2e00-9851-1d72-eb00e8bac28c.png", "confidential_overlay_class": "confidential-blurred-image-overlay", "display_confidential_label": "", "annual_organic_traffic": 690, "revenue_average": 16671, "authority_score": 8, "app_rating": null, "managed_by_flippa": true, "managed_by": "flippa_broker", "broker_name": "Broker name redacted for this fixture", "broker_avatar_url": "https://static2.flippa.com/avatar_4170d75d-c8f8-412f-b6d2-e25ee5126cb7.png", "listing_category": "saas", "original_price": 807779, "indicative_price_lower_value": 0, "indicative_price_upper_value": 0, "price_dropped": true, "price_dropped_percent": 53, "under_offer": false, "badges": [{"text": "Géré par Flippa", "icon": "icons/library/logo/flippa-square.svg", "variant": "light_orange_oxford_text"}, {"text": "Broker", "icon": "icons/library/solid/user-circle.svg", "variant": "light_gray"}, {"text": "Sponsorisé", "icon": "icons/library/solid/star.svg", "variant": "light_yellow"}, {"text": "Choix de l'éditeur", "icon": "icons/library/solid/thumbs-up.svg", "variant": "light_purple"}], "listing_watchable": true, "show_multiple": true, "invest": false, "action_class": "primary", "key_data": [{"label": "Type", "value": "SaaS"}, {"label": "Industrie", "value": "Santé et beauté"}, {"label": "Monétisation", "value": "Services et abonnements"}, {"label": "Ancienneté", "value": "3 années"}, {"label": "Profit", "value": "11 501 € (EUR) par mois"}], "open_to_offer": false, "partial_sale": false, "equity_sale_percentage": "100", "hide_profit": false, "domain_only?": false, "action_button_text": "Voir l'Annonce", "original_price_text": "807 779 € (EUR)", "price_text": "380 546 € (EUR)", "target_raise_amount_text": "0 € (EUR)", "price_guide_text": "0 € (EUR) - 0 € (EUR)"}], "metadata": {"totalResults": 722}, "error?": false};
</script></body></html>"""

SIGNUP_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Sign up | Flippa</title>
<script src="https://static.flippa.com/assets/legacy-head-23638d7a.js"></script></head>
<body><form>
<div class="cf-turnstile" data-sitekey="0xFIXTUREFIXTUREFIXTURE"
     data-error-callback="handleInteractiveCaptcha" data-size="flexible"></div>
</form>
<captcha-widgets><captcha-widget data-captcha-type="turnstile"
  data-widget-id="0xFIXTUREFIXTUREFIXTURE" data-sitekey="0xFIXTUREFIXTUREFIXTURE"
  data-loaded="true"></captcha-widget></captcha-widgets>
<script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/turnstile/hunter.js"
        data-ts-input="cf-turnstile-response"></script>
</body></html>"""

# A page the site plainly served, with no listing data in it yet: the app
# shell. Trimmed to the parts the classifier reads.
SHELL_FIXTURE_HTML = ("<!DOCTYPE html><html><head><title>Flippa</title>" +
                      "".join(f'<link rel="preload" href="https://static.flippa.com/assets/a{i}.js">'
                              for i in range(20)) +
                      '</head><body><div ng-controller="SearchV2_SearchController">'
                      '</div></body></html>')

# Chromium's own network-error page. It carries the SITE'S OWN HOSTNAME in its
# title, so every text marker reads it as a real page — only "was this built
# out of the site's own assets?" gets it right.
CHROME_ERROR_FIXTURE_HTML = ("<!DOCTYPE html><html><head><title>flippa.com</title></head>"
                             "<body><div id='main()-message'>This site can’t be reached</div>"
                             "<div class='error-code'>ERR_PROXY_CONNECTION_FAILED</div>"
                             "</body></html>")

CLOUDFLARE_CHALLENGE_FIXTURE_HTML = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>"
    "<div id='cf-wrapper'><script>window._cf_chl_opt={cvId:'3'};</script>"
    "</div></body></html>")


# ---------------------------------------------------------------------------
# 1. The parser, against real markup, asserting VALUES
# ---------------------------------------------------------------------------
def check_parser_values():
    print("\n[parser: real fixture, asserted values]")
    rows = parse_products(PAGE_FIXTURE_HTML, "https://flippa.com/search",
                          category="saas", page=1)
    eq("two rows from the two-card fixture", len(rows), 2)
    by_sku = {r.sku: r for r in rows}

    a = by_sku.get("12857417")
    check("the price-dropped card parsed", a is not None)
    if a:
        eq("title is the card heading", a.title, "SaaS | Health and Beauty")
        eq("url is the listing's own", a.url, "https://flippa.com/12857417")
        eq("price is the reduced figure", a.price, 573006.0)
        eq("original_price is the struck-through figure", a.original_price, 927250.0)
        eq("currency is read, not defaulted", a.currency, "USD")
        # 927250 -> 573006 is 38.2%; the site's own badge says "Reduced 38%".
        # The COMPUTED figure is what goes in the column.
        eq("discount is computed from the two prices", a.discount_pct, 38.2)
        eq("asset type", a.asset_type, "SaaS")
        eq("industry", a.industry, "Health and Beauty")
        eq("monetization", a.monetization, "Services & Subscriptions")
        eq("monthly profit", a.monthly_profit, 13202.0)
        eq("monthly revenue", a.monthly_revenue, 16671.0)
        eq("profit multiple", a.profit_multiple, 3.6)
        eq("revenue multiple", a.revenue_multiple, 2.9)
        eq("age text as the site writes it", a.age_text, "3 years")
        eq("country", a.country, "France")
        eq("status", a.status, "open")
        eq("sale method", a.sale_method, "classified")
        eq("bid count on a classified listing", a.bid_count, 0)
        eq("confidential badge", a.confidential, True)
        eq("managed-by-Flippa badge", a.managed_by_flippa, True)
        eq("category label from the run", a.category, "saas")
        eq("page column", a.page, 1)
        eq("position column", a.position, 1)
        eq("price_source records both views agreed", a.price_source, "state+dom")
        check("description is the seller's own line",
              a.description and a.description.startswith("Profitable B2B SaaS"))

    b = by_sku.get("12230608")
    check("the auction card parsed", b is not None)
    if b:
        eq("auction price", b.price, 17289.0)
        eq("auction bid count", b.bid_count, 1)
        eq("no original price on a listing that did not drop", b.original_price, None)
        eq("no discount without an original price", b.discount_pct, None)
        eq("position is 1-based within the page", b.position, 2)

    # page+position must be unique across a multi-page run: `position`
    # restarts at 1 on every page, so the column is worthless without the pair
    # (measured elsewhere in this family: 60 of 119 rows once claimed a position another row had).
    page2 = parse_products(PAGE_FIXTURE_HTML, "https://flippa.com/search", page=2)
    pairs = [(r.page, r.position) for r in rows + page2]
    eq("page+position is unique across pages", len(set(pairs)), len(pairs))


def check_second_locale():
    print("\n[a second locale]")
    # The rule this family settled on: run a SECOND locale before believing the first. Flippa's
    # own hreflang set publishes /fr/ and /es/ paths, and that single step
    # found two things the English pages could never show.
    rows = parse_products(FR_FIXTURE_HTML, "https://flippa.com/fr/search",
                          category=category_from_url("https://flippa.com/fr/search"),
                          page=1)
    eq("the French page parses", len(rows), 1)
    row = rows[0]
    eq("the same listing, same id", row.sku, "12857417")
    # 1. Prices are CONVERTED, not just relabelled. The currency column has to
    #    follow, and a defaulted "USD" would have been silently wrong.
    eq("the price is in euros on the French page", row.currency, "EUR")
    eq("and is the converted amount", row.price, 380546.0)
    # 2. The key-data LABELS are localised, so anything keyed on the English
    #    word "Net Profit" comes back null. The profit is read by the SHAPE of
    #    the value instead.
    eq("net profit survives localised labels", row.monthly_profit, 11501.0)
    eq("age comes from the record's own formatted field", row.age_text, "3 années")
    eq("industry is the site's own localised value", row.industry, "Santé et beauté")
    # 3. The locale segment is not a category.
    eq("a locale prefix is not read as a category",
       category_from_url("https://flippa.com/fr/search"), None)
    eq("but a filter in the same URL still is",
       category_from_url("https://flippa.com/fr/search?filter%5Bproperty_type%5D=saas"),
       "saas")
    eq("the locale is reported so a run can say so",
       product_parser.locale_from_url("https://flippa.com/es/websites"), "es")
    eq("and is None on the default English path",
       product_parser.locale_from_url("https://flippa.com/search"), None)
    eq("pagination works the same on a localised path",
       page_url("https://flippa.com/fr/search", 2),
       "https://flippa.com/fr/search?page%5Bnumber%5D=2")


def check_known_limitations_are_pinned():
    print("\n[pinned limitations]")
    # Where a defence would be worse than the gap, the CURRENT behaviour is
    # asserted with the reason, so a future change is a decision rather than a
    # surprise.
    #
    # Flippa's own multiple is NOT always price / (12 x monthly profit):
    # measured 2026-09-19, 53 of 63 listings agreed within 0.15 and 10 did
    # not. It is computed over a profit window this repo cannot see, so it is
    # reported AS PUBLISHED rather than recomputed or "corrected".
    record = {"id": "1", "listing_url": "https://flippa.com/1", "price": 9082,
              "currency_label": "USD $", "multiple": 2.6, "revenue_multiple": 1.8,
              "show_multiple": True, "profit_average": "386",
              "basic_info": {"name": "SaaS | Internet"},
              "key_data": [{"label": "Net Profit", "value": "USD $386 p/mo"}]}
    html = ('<html><body><script>const STATE = '
            + json.dumps({"results": [record], "metadata": {"totalResults": 1}})
            + ';</script></body></html>')
    row = parse_products(html, "https://flippa.com/search")[0]
    eq("the site's own multiple is reported as published", row.profit_multiple, 2.6)
    check("even though price / (12 x profit) is 1.96 here — the two disagree "
          "on ~1 listing in 6, and inventing our own figure would be worse",
          abs(row.price / (12 * row.monthly_profit) - 2.0) < 0.1)

    # The rendered-card fallback cannot read a localised label set. It runs
    # only when the embedded JSON is absent, which has not been observed;
    # pinning it here keeps the gap visible instead of half-guarded.
    fr_card_labels = {"industrie", "monétisation", "ancienneté"}
    english_only = {"industry", "monetization"}
    check("the card fallback keys industry/monetization on English labels "
          "(the JSON path is locale-proof; the card path is not)",
          english_only.isdisjoint(fr_card_labels))


def check_parser_fallback_path():
    print("\n[parser: the rendered-card fallback]")
    # The same bytes with the embedded JSON disabled: the DOM path has to
    # stand on its own, and it has to AGREE with the JSON path. On the full
    # 25-card capture these two paths agree on all 22 shared columns.
    stripped = PAGE_FIXTURE_HTML.replace("const STATE =", "const NOT_STATE =")
    dom_rows = {r.sku: r for r in parse_products(stripped, "https://flippa.com/search",
                                                 category="saas", page=1)}
    state_rows = {r.sku: r for r in parse_products(PAGE_FIXTURE_HTML,
                                                   "https://flippa.com/search",
                                                   category="saas", page=1)}
    eq("the fallback finds the same listings", sorted(dom_rows), sorted(state_rows))
    shared = ("title", "price", "currency", "original_price", "discount_pct",
              "description", "asset_type", "industry", "monetization",
              "monthly_profit", "profit_multiple", "revenue_multiple",
              "age_text", "country", "bid_count", "managed_by_flippa",
              "sponsored", "editors_choice", "confidential", "verified_listing",
              "url", "position")
    disagreements = [
        (sku, field, getattr(state_rows[sku], field), getattr(dom_rows[sku], field))
        for sku in state_rows for field in shared
        if getattr(state_rows[sku], field) != getattr(dom_rows[sku], field)]
    check(f"both paths agree on every shared column ({len(shared)} columns x "
          f"{len(state_rows)} rows)", not disagreements)
    if disagreements:
        for d in disagreements[:5]:
            print(f"        {d}")
    eq("the fallback labels its own provenance",
       {r.price_source for r in dom_rows.values()}, {"dom"})


def check_price_parsing():
    print("\n[prices]")
    eq("plain ISO + symbol", prices_in("USD $573,006"), ([573006.0], "USD"))
    # Flippa writes a loss as "-USD $315 p/mo". Dropping the sign turns a
    # loss-making business into a profitable one; this was null before.
    eq("a negative profit keeps its sign", prices_in("-USD $315 p/mo"),
       ([-315.0], "USD"))
    eq("single-digit amounts", prices_in("USD $1 p/mo"), ([1.0], "USD"))
    # A bare [A-Z]{3} would make MRR and MAU currencies and invent prices.
    eq("MRR is not a currency", prices_in("3k MRR")[1], None)
    eq("MAU is not a currency", prices_in("30K MAU")[1], None)
    eq("a bare symbol still reads as USD here", prices_in("$1,234"), ([1234.0], "USD"))
    eq("space-grouped thousands", prices_in("1 234,56 kr")[0], [1234.56])
    eq("three trailing digits is a grouping, not a decimal",
       prices_in("$1,234")[0], [1234.0])
    eq("two trailing digits is a decimal", prices_in("$12.34")[0], [12.34])
    # Percentages are removed BEFORE matching: a rejected match has already
    # consumed the symbol, and Flippa's own badge renders before the price.
    eq("a percentage badge is not a price",
       prices_in(strip_percentages("Reduced 38% USD $573,006")), ([573006.0], "USD"))
    eq("discount computed", discount_pct(573006.0, 927250.0, 38.0), 38.2)
    eq("no discount when the 'original' is at or below the price",
       discount_pct(100.0, 100.0), None)
    eq("no negative discount", discount_pct(120.0, 100.0), None)
    eq("no discount without an original", discount_pct(100.0, None), None)


def check_pagination_convention():
    print("\n[pagination]")
    # From Flippa's own legacy-foot JS: page 1 carries no page parameter, and
    # a bare `page` is deleted rather than honoured.
    eq("page 1 carries no page parameter",
       page_url("https://flippa.com/search", 1), "https://flippa.com/search")
    eq("page 2 uses the site's own parameter",
       page_url("https://flippa.com/search", 2),
       "https://flippa.com/search?page%5Bnumber%5D=2")
    eq("existing filters are preserved",
       page_url("https://flippa.com/search?filter%5Bsitetype%5D=saas", 3),
       "https://flippa.com/search?filter%5Bsitetype%5D=saas&page%5Bnumber%5D=3")
    eq("a stale bare ?page= is replaced, not appended to",
       page_url("https://flippa.com/search?page=7", 2),
       "https://flippa.com/search?page%5Bnumber%5D=2")
    eq("a stale page[number] is replaced",
       page_url("https://flippa.com/search?page%5Bnumber%5D=9", 4),
       "https://flippa.com/search?page%5Bnumber%5D=4")
    eq("category shortcuts paginate the same way",
       page_url("https://flippa.com/websites", 2),
       "https://flippa.com/websites?page%5Bnumber%5D=2")
    eq("category label from a filter", category_from_url(
        "https://flippa.com/search?filter%5Bproperty_type%5D=saas"), "saas")
    eq("category label from a shortcut path",
       category_from_url("https://flippa.com/websites"), "websites")
    eq("no label invented for a bare search",
       category_from_url("https://flippa.com/search"), None)


# ---------------------------------------------------------------------------
# 2. Page states
# ---------------------------------------------------------------------------
def check_page_states():
    print("\n[page_flow]")
    content = page_flow.classify(PAGE_FIXTURE_HTML)
    eq("a page with records is content", content.state, page_flow.CONTENT)
    check("content is not retried", not content.policy.retry)

    empty_state = json.dumps({"results": [{"id": "1"}, {"id": "2"}],
                              "metadata": {"totalResults": 0}})
    empty = page_flow.classify("<html><body><script>const STATE = "
                               + empty_state + ";</script></body></html>")
    eq("totalResults 0 is an empty listing", empty.state, page_flow.EMPTY)
    check("an empty listing is a complete answer", empty.policy.complete)
    check("an empty listing is not blocked", not empty.policy.blocked)
    # Flippa answers a zero-match query with five PROMOTED listings. They are
    # placements, not matches — reporting them would be fabricated rows.
    eq("promoted listings on a zero-match page are not reported",
       parse_products("<html><body><script>const STATE = " + empty_state
                      + ";</script></body></html>", "https://flippa.com/search"), [])

    past_end = page_flow.classify(
        '<html><body><script>const STATE = {"results": [], "metadata": '
        '{"totalResults": 6183}};</script></body></html>')
    eq("an empty page past the end is exhausted", past_end.state, page_flow.EXHAUSTED)
    check("being past the end is a complete answer", past_end.policy.complete)

    shell = page_flow.classify(SHELL_FIXTURE_HTML)
    eq("the app shell is unpainted, not blocked", shell.state, page_flow.UNPAINTED)
    check("an unpainted page waits before retrying", shell.policy.wait_first)
    check("an unpainted page is not worth a solve", not shell.policy.may_solve)

    error_page = page_flow.classify(CHROME_ERROR_FIXTURE_HTML)
    eq("Chromium's own error page is blocked, despite carrying the site's "
       "hostname in its title", error_page.state, page_flow.BLOCKED)
    challenge = page_flow.classify(CLOUDFLARE_CHALLENGE_FIXTURE_HTML)
    eq("a Cloudflare challenge is blocked", challenge.state, page_flow.BLOCKED)
    eq("and names the vendor", challenge.vendor, "cloudflare")
    eq("a refusal status is blocked",
       page_flow.classify("<html>x</html>", status_code=403).state, page_flow.BLOCKED)

    # The rule that cost two releases on another site: count a marker on a
    # page you KNOW is good before adding it. Cloudflare's JS-detections tag
    # is on every page Flippa serves.
    good = open(os.path.join(REPO, "captures",
                             "live_search_page1_2026-09-19.html"),
                encoding="utf-8", errors="replace").read() \
        if os.path.exists(os.path.join(REPO, "captures",
                                       "live_search_page1_2026-09-19.html")) else None
    if good:
        eq("a real served page is content, though it references "
           "cdn-cgi/challenge-platform", page_flow.classify(good).state,
           page_flow.CONTENT)
    else:
        skip("a real served page is content",
             "captures/live_search_page1_2026-09-19.html is not in this "
             "checkout — see CONTRIBUTING.md on recording one")
    if good:
        # The rule itself, applied to the real marker set: NOT ONE of the
        # strings this repo calls a challenge may appear on a page the site
        # plainly served. Checked against the bytes rather than by reading the
        # list, because that is the mistake the list is trying to avoid.
        firing = [m for markers in product_parser.BOT_CHALLENGE_MARKERS.values()
                  for m in markers if m in good]
        check("no challenge marker appears on a good 964 KB listing page"
              + (f" — {firing} does" if firing else ""), not firing)
        # And the strings that WOULD have fired, to keep the measurement in
        # the suite rather than only in a comment.
        for tempting in ("cdn-cgi", "challenge-platform", "cloudflare"):
            check(f"(measured: {tempting!r} appears {good.count(tempting)}x on "
                  f"that good page, which is why it is not a marker)",
                  good.count(tempting) > 0)


# ---------------------------------------------------------------------------
# 3. Captcha detection
# ---------------------------------------------------------------------------
def check_shortfall_arithmetic():
    print("\n[page coverage]")
    # The site publishes its own match count, so "did this page bring back
    # everything it should have?" is arithmetic rather than a threshold.
    eq("a full first page is 25", page_flow.expected_records(724, 1), 25)
    eq("the last page holds the remainder", page_flow.expected_records(60, 3), 10)
    eq("a page past the end expects nothing", page_flow.expected_records(60, 9), 0)
    eq("no total means no expectation", page_flow.expected_records(None, 1), None)
    # 10000 is the site's CAP, printed as "10,000+" — a cap is not a count,
    # and treating it as one would report a phantom shortfall on every large
    # listing.
    eq("the 10,000 cap is not treated as a count",
       page_flow.expected_records(10000, 2), None)
    check("a full page reports no shortfall",
          page_flow.shortfall(724, 1, 25) is None)
    message = page_flow.shortfall(724, 1, 18)
    check("a short page is named, with both numbers in the message",
          message and "18" in message and "25" in message)


def check_fingerprint_kwargs_are_ones_playwright_accepts():
    print("\n[fingerprint kwargs]")
    # An unknown key in new_context(**kwargs) is a TypeError at launch, on the
    # PAID path, at runtime. Checked against the driver's real
    # signature rather than against a list written from memory.
    import fingerprint_client
    sample = {"id": "x", "country": "de", "userAgent": {"value": "UA/1"},
              "screen": {"width": 1920, "height": 1080}, "locale": "de-DE",
              "timezone": "Europe/Berlin",
              "navigator": {"platform": "Win32", "hardwareConcurrency": 8},
              "webgl": {"vendor": "Google Inc.", "renderer": "ANGLE"}}
    kwargs = fingerprint_client.playwright_context_kwargs(sample)
    check("the fingerprint produces some context kwargs at all", bool(kwargs))
    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        SKIPPED_GROUPS.append("fingerprint kwargs (playwright not installed)")
        check("fingerprint kwargs vs the real new_context signature "
              "(playwright not installed — SKIPPED)", True)
        return
    from playwright.sync_api import Browser
    accepted = set(inspect.signature(Browser.new_context).parameters)
    unknown = sorted(set(kwargs) - accepted)
    check("every fingerprint kwarg is one Browser.new_context accepts"
          + (f" (unknown: {unknown})" if unknown else ""), not unknown)
    # And the init script must be valid JS shape, since a syntax error there
    # fails silently inside the browser.
    script = fingerprint_client.playwright_init_script(sample)
    check("the init script bakes its values in as JSON",
          '"platform": "Win32"' in script or '"platform":"Win32"' in script)
    check("and patches both WebGL contexts",
          "WebGL2RenderingContext" in script and "37446" in script)


def check_captcha_detection():
    print("\n[captcha]")
    # The fixture carries the Scraping Browser extension's own injected
    # turnstile hunter, exactly as the real capture does. A detector that does
    # not strip extension tags reports a challenge on a page holding the full
    # catalogue.
    check("extension-injected markup is present in the fixture",
          "chrome-extension://" in PAGE_FIXTURE_HTML and
          "cf-turnstile-response" in PAGE_FIXTURE_HTML)
    eq("no challenge is detected on a good listing page",
       captcha_solver.detect_in_html(PAGE_FIXTURE_HTML, "https://flippa.com/search"),
       None)
    # The empty <captcha-widgets> in the listing fixture is the AUTOSOLVER
    # EXTENSION's, not the site's — verified 2026-09-21 by loading the same
    # pages in a plain Chromium with no extensions, where the element does
    # not exist at all. Detecting it is still worth it (a filled one means
    # the extension found a challenge); reading it as the site's markup is
    # not, and the fixtures say which is which.
    eq("an empty autosolver mount is not a challenge",
       captcha_solver.detect_autosolver_mount(PAGE_FIXTURE_HTML), None)
    check("a filled autosolver mount IS reported",
          captcha_solver.detect_autosolver_mount(SIGNUP_FIXTURE_HTML) is not None)
    check("and the site's OWN widget is what the detector keys on: the "
          "signup fixture carries a cf-turnstile div with a sitekey",
          'class="cf-turnstile"' in SIGNUP_FIXTURE_HTML)

    signup = captcha_solver.detect_in_html(SIGNUP_FIXTURE_HTML,
                                           "https://flippa.com/signup")
    check("the signup page's Turnstile is detected", signup is not None)
    if signup:
        eq("and classified as Turnstile", signup.kind, "turnstile")
        eq("with its sitekey", signup.sitekey, "0xFIXTUREFIXTUREFIXTURE")
        eq("and mapped to the right paid task type",
           captcha_solver._task_for(signup, 0.7)["type"], "TurnstileTaskProxyless")

    # A widget with no readable sitekey must report itself unsolvable rather
    # than send a null sitekey to a paid API.
    keyless = captcha_solver.detect_turnstile(
        '<html><body><script src="https://challenges.cloudflare.com/turnstile/v0/api.js">'
        '</script><div class="cf-turnstile"></div></body></html>', "https://flippa.com/x")
    check("a sitekey-less widget is reported, not ignored", keyless is not None)
    if keyless:
        eq("with no sitekey", keyless.sitekey, None)
        try:
            captcha_solver.solve(keyless, "0" * 32)
            check("solving a sitekey-less challenge is refused", False)
        except RuntimeError as e:
            check("solving a sitekey-less challenge is refused with the reason",
                  "sitekey" in str(e))

    # Both token-injection spellings ship, because the drivers disagree.
    check("an arrow-function injector ships for Playwright/pyppeteer",
          captcha_solver.INJECT_TOKEN_FN.strip().startswith("(token) =>"))
    check("a function-body injector ships for Selenium",
          "arguments[0]" in captcha_solver.INJECT_TOKEN_BODY and
          "return true;" in captcha_solver.INJECT_TOKEN_BODY)
    check("both injectors write the Turnstile response field",
          "cf-turnstile-response" in captcha_solver.INJECT_TOKEN_FN and
          "cf-turnstile-response" in captcha_solver.INJECT_TOKEN_BODY)

    # v3 minScore is not free-form.
    v3 = captcha_solver.CaptchaChallenge(kind="recaptcha_v3", sitekey="6L" + "a" * 30,
                                         page_url="https://flippa.com/x")
    eq("an out-of-range minScore is snapped to a documented one",
       captcha_solver._task_for(v3, 0.55)["minScore"], 0.7)


def check_credentials_never_leak():
    print("\n[credentials]")
    masked = proxy_pool.mask("http://user:secret@gate.example.com:9999")
    check("a masked proxy keeps host and port", "gate.example.com:9999" in masked)
    check("and drops the credentials",
          "secret" not in masked and "user" not in masked)
    pw = proxy_pool.to_playwright("http://user:secret@gate.example.com:9999")
    check("credentials never reach the browser's argv",
          "secret" not in pw["server"] and "user" not in pw["server"])
    eq("they go in their own fields instead", (pw["username"], pw["password"]),
       ("user", "secret"))
    arg, creds = proxy_pool.to_pyppeteer("http://user:secret@gate.example.com:9999")
    check("pyppeteer's switch carries no credentials", "secret" not in arg)
    eq("they go through page.authenticate instead", creds["password"], "secret")
    arg, warning = proxy_pool.to_selenium("http://user:secret@gate.example.com:9999")
    check("Selenium's switch carries no credentials", "secret" not in arg)
    check("and the user is WARNED rather than misled",
          warning and "cannot authenticate" in warning)

    # An exception message is a log. The v1 captcha endpoint and the
    # fingerprint API both take the key as a query parameter.
    leaky = ("HTTPSConnectionPool: /res.php?key=0123456789abcdef0123456789abcdef"
             "&action=get failed; also ws://login:pass@cb.2captcha.com:9222")
    redacted = proxy_pool.redact_secret_patterns(leaky)
    check("a key in a query string is redacted",
          "0123456789abcdef" not in redacted)
    check("a password in a URL is redacted", "pass@" not in redacted)
    check("the endpoint itself survives redaction",
          "res.php" in redacted and "cb.2captcha.com:9222" in redacted)
    # Globally, not once.
    twice = proxy_pool.redact_secret_patterns("key=aaa and key=bbb")
    check("every occurrence is redacted, not just the first",
          "aaa" not in twice and "bbb" not in twice)

    for bad, why in (("gate.example.com:9999", "a bare host:port is refused"),
                     ("socks5://user:pass@host:1080",
                      "an authenticated socks5 exit is refused, not silently stripped")):
        try:
            proxy_pool.parse_proxy_line(bad)
            check(why, False)
        except proxy_pool.ProxyError as e:
            check(why, True)
            check("and the refusal itself carries no credentials",
                  "pass" not in str(e).replace("password", ""))


def check_remote_connect_failures_are_redacted():
    print("\n[remote connect failures]")
    # Measured live on 2026-09-19 against a real Scraping Browser endpoint
    # that answered 401: Playwright put the endpoint — password included —
    # into the exception message AND into a four-line call log under it, five
    # occurrences in one traceback. An exception message is a log, and a
    # traceback printed on the way out is a log too.
    secret = "ws://acct-zone-scraping_browser-pid-1:s3cr3tpassw0rd@cb.2captcha.com:9222"
    library_error = ("connect_over_cdp: WebSocket error\n"
                     f"  - <ws unexpected response> {secret}/ 401 Unauthorized\n"
                     f"  - <ws error> {secret}/ closed before established\n"
                     f"  - <ws connect error> {secret}/ closed\n")
    redacted = proxy_pool.redact_secret_patterns(library_error)
    check("a credentialled endpoint is redacted out of a library error",
          "s3cr3tpassw0rd" not in redacted)
    eq("every occurrence of it, not just the first",
       redacted.count("***:***@cb.2captcha.com:9222"), 3)
    check("and the endpoint, the host and the status all survive",
          "cb.2captcha.com:9222" in redacted and "401 Unauthorized" in redacted)

    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
        source = open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read()
        check(f"{name} wraps its remote connect rather than letting the "
              f"library's own error escape",
              "redact_secret_patterns(str(e))" in source)
        check(f"{name} redacts a traceback before printing it",
              "redact_secret_patterns(traceback.format_exc())" in source)

    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        check("the engine's own connect path redacts (playwright not "
              "installed — SKIPPED)", True)
        return

    class _StubChromium:
        def connect_over_cdp(self, endpoint, timeout=None):
            raise RuntimeError(library_error)

    class _StubPW:
        chromium = _StubChromium()

    class Args:
        cdp_endpoint = secret

    try:
        engine._connect_remote(_StubPW(), Args())
        check("the engine refuses a failed connect", False)
    except RuntimeError as e:
        check("the engine's own connect failure carries no password",
              "s3cr3tpassw0rd" not in str(e))
        check("and still names what failed and where",
              "cb.2captcha.com:9222" in str(e))


def check_proxy_rotation():
    print("\n[proxy rotation]")
    pool = proxy_pool.ProxyPool(["http://a:1", "http://b:2", "http://c:3"],
                                rotate="per-page")
    eq("starts on the first exit", pool.current, "http://a:1")
    pool.advance("test")
    eq("advances in order", pool.current, "http://b:2")
    pool.advance("test")
    pool.advance("test")
    eq("wraps rather than exhausting", pool.current, "http://a:1")
    eq("counts its rotations", pool.rotations, 3)
    single = proxy_pool.ProxyPool(["http://a:1"])
    single.advance("nowhere to go")
    eq("a one-exit pool stays put", single.current, "http://a:1")
    eq("and does not claim a rotation", single.rotations, 0)
    check("per-run does not rotate per page", not single.rotates_per_page())
    copy = pool.proxies
    copy.append("http://d:4")
    eq("the pool hands out a copy, so a worker cannot mutate it", len(pool), 3)


# ---------------------------------------------------------------------------
# 4. Output contract
# ---------------------------------------------------------------------------
def _row(**kw):
    base = dict(sku="1", url="https://flippa.com/1", price=1.0)
    base.update(kw)
    return Product(**base)


def check_proxy_preflight():
    print("\n[proxy preflight]")
    # Measured 2026-09-21: an exit whose password had been rotated answered
    # 407 in 0.2s to a plain request, while the SAME exit under Chromium gave
    # nothing but navigation timeouts — three 60s attempts per page, with the
    # proxy never mentioned. The family rule is that a proxy failure is not a
    # timeout; this is the case where the browser reports one anyway, so the
    # exit is checked before a browser is involved.
    eq("407 is credentials, not slowness",
       proxy_pool.classify_preflight("Tunnel connection failed: 407 Proxy "
                                     "Authentication Required"),
       proxy_pool.PREFLIGHT_REJECTED)
    eq("a refused tunnel is an unusable exit",
       proxy_pool.classify_preflight("ProxyError('Unable to connect to proxy', "
                                     "ConnectionRefusedError)"),
       proxy_pool.PREFLIGHT_UNUSABLE)
    eq("a read timeout is not blamed on the exit",
       proxy_pool.classify_preflight("Read timed out. (read timeout=15)"),
       proxy_pool.PREFLIGHT_SLOW)
    eq("and an unrecognised failure is not either",
       proxy_pool.classify_preflight("something nobody has seen yet"),
       proxy_pool.PREFLIGHT_SLOW)

    # A rejected exit ends the run as bad usage; a slow one only warns.
    pool = proxy_pool.ProxyPool(["http://user:pass@gate.example.com:9999"])
    real = proxy_pool.preflight
    try:
        proxy_pool.preflight = lambda url, target, timeout=15.0: (
            proxy_pool.PREFLIGHT_REJECTED, "407 Proxy Authentication Required")
        try:
            proxy_pool.check_exit_or_raise(pool, "https://flippa.com/search")
            check("a rejected exit stops the run", False)
        except proxy_pool.ProxyError as e:
            check("a rejected exit stops the run before a browser starts", True)
            check("and the message says it is the exit, not the site",
                  "not the site" in str(e))
            check("and carries no credentials", "pass@" not in str(e))
        proxy_pool.preflight = lambda url, target, timeout=15.0: (
            proxy_pool.PREFLIGHT_SLOW, "read timed out")
        proxy_pool.check_exit_or_raise(pool, "https://flippa.com/search")
        check("a slow exit only warns — the target may be what is slow", True)
        proxy_pool.preflight = lambda url, target, timeout=15.0: (
            proxy_pool.PREFLIGHT_OK, "HTTP 200")
        proxy_pool.check_exit_or_raise(pool, "https://flippa.com/search")
        check("a working exit says so and continues", True)
        proxy_pool.check_exit_or_raise(None, "https://flippa.com/search")
        check("and with no pool there is nothing to check", True)
    finally:
        proxy_pool.preflight = real

    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
        source = open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read()
        check(f"{name} preflights its exit before launching a browser",
              "check_exit_or_raise(pool, args.url)" in source)


def check_output_contract():
    print("\n[output contract]")
    columns = list(asdict(Product()).keys())
    eq("the family prefix leads the schema, in order", columns[:11],
       ["source", "scraped_at", "url", "sku", "title", "price", "currency",
        "original_price", "discount_pct", "category", "price_source"])
    eq("source names the site", Product.source, "flippa.com")
    for gone in ("brand", "rating", "review_count", "in_stock"):
        check(f"{gone!r} is not a column (measured null on every row)",
              gone not in columns)

    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "out")
        # An empty result still carries a header row.
        write_csv([], f"{prefix}.csv")
        with open(f"{prefix}.csv", newline="", encoding="utf-8") as f:
            header = next(csv.reader(f))
        eq("an empty CSV still carries its header", header, columns)

        # A run that finds nothing writes nothing.
        with open(f"{prefix}.json", "w", encoding="utf-8") as f:
            f.write('[{"sku": "yesterday"}]')
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = save([], prefix, "json")
        eq("an empty run exits 4", rc, EXIT_NO_PRODUCTS)
        eq("and leaves last night's good output alone",
           json.load(open(f"{prefix}.json", encoding="utf-8")), [{"sku": "yesterday"}])
        with redirect_stdout(io.StringIO()):
            rc = save([], prefix, "json", allow_empty=True)
        eq("--allow-empty writes the empty result",
           json.load(open(f"{prefix}.json", encoding="utf-8")), [])
        eq("and still exits 4", rc, EXIT_NO_PRODUCTS)

        # A failed run writes no sidecar beside the previous good output.
        os.remove(f"{prefix}.json")
        with redirect_stdout(io.StringIO()):
            rc = finish_run([], prefix, "json", False, blocked=True,
                            stop_reason="blocked_cloudflare", pages_requested=1,
                            pages_completed=0, start_url="u", final_url="u")
        eq("a blocked run exits 3", rc, EXIT_BLOCKED)
        check("and writes no sidecar", not os.path.exists(f"{prefix}.meta.json"))

        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row()], prefix, "json", False, blocked=False,
                            stop_reason="page_load_timeout", pages_requested=3,
                            pages_completed=1, pages_failed=[2],
                            start_url="u", final_url="u")
        eq("a run that stopped early exits 6", rc, EXIT_PARTIAL)
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        eq("the sidecar says partial", meta["status"], "partial")
        eq("and names WHICH pages failed, not just how many",
           meta["pages_failed"], [2])
        eq("and records the site's own result count", "total_results" in meta, True)

        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row()], prefix, "json", False, blocked=False,
                            stop_reason="listing_exhausted", pages_requested=9,
                            pages_completed=2, start_url="u", final_url="u",
                            total_results=42, addressable=True)
        eq("running out of listings is a complete run", rc, 0)
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        eq("the sidecar says complete", meta["status"], "complete")
        eq("and carries the site's own total", meta["total_results"], 42)
        eq("and whether pages could be addressed by URL", meta["addressable"], True)

    seen = set()
    rows = dedupe_by_sku([_row(sku="a"), _row(sku="b"), _row(sku="a")], seen)
    eq("duplicates within a page are dropped", [r.sku for r in rows], ["a", "b"])
    rows = dedupe_by_sku([_row(sku="b"), _row(sku="c")], seen)
    eq("and across pages, through the shared set()", [r.sku for r in rows], ["c"])
    rows = dedupe_by_sku([_row(sku=None), _row(sku=None)], set())
    eq("a row with no sku is never dropped as a duplicate", len(rows), 2)


# ---------------------------------------------------------------------------
# 5. The engines: parity, and the five checks §17 says to steal
# ---------------------------------------------------------------------------
# The flag contract every engine in the family exposes, as
# argparse destinations. Asserted in BOTH directions: a missing flag fails,
# and so does an engine growing one its twins do not have.
CONTRACT_FLAGS = {
    "url", "pages", "category", "format", "out", "delay", "retries",
    "retry_delay", "concurrency", "proxy", "proxy_file", "proxy_rotate",
    "proxy_shuffle", "proxy_block_retries", "twocaptcha_key", "captcha_api",
    "solve_captcha", "min_score", "cdp_endpoint", "allow_empty", "dump_html",
    "headless", "fingerprint", "fp_tags", "fp_country",
}


def _engine_flags(module):
    """The destinations `module.parse_args()` produces, via the real entry point.

    Exercised through parse_args rather than by reading add_argument calls:
    a signature that drifts from its callers is invisible to a test that only
    touches the internals underneath.
    """
    argv = sys.argv
    sys.argv = [module.__name__, "--url", "https://flippa.com/search"]
    try:
        return set(vars(module.parse_args()))
    finally:
        sys.argv = argv


def check_engine_parity():
    print("\n[engine parity]")
    if not ENGINES:
        check("engine parity (no engine library installed here — SKIPPED)", True)
        return
    flag_sets = {}
    for name, module in ENGINES.items():
        flags = _engine_flags(module)
        flag_sets[name] = flags
        missing = CONTRACT_FLAGS - flags
        extra = flags - CONTRACT_FLAGS
        check(f"{name} carries every flag in the contract"
              + (f" (missing {sorted(missing)})" if missing else ""), not missing)
        check(f"{name} adds no flag outside the contract"
              + (f" (extra {sorted(extra)})" if extra else ""), not extra)
    if len(flag_sets) > 1:
        first = next(iter(flag_sets.values()))
        check("every engine exposes the SAME flags as its twins",
              all(s == first for s in flag_sets.values()))

    for name, module in ENGINES.items():
        eq(f"{name} uses the shared card selector", module.ITEM_CARD_SELECTOR,
           product_parser.SELECTORS["item_card"])
        check(f"{name}'s MIN_CARD_MATCHES is > 1 (one match resolves on an "
              f"unrelated element long before the grid paints)",
              module.MIN_CARD_MATCHES > 1)
    if len(ENGINES) > 1:
        values = {m.MIN_CARD_MATCHES for m in ENGINES.values()}
        eq("every engine agrees on MIN_CARD_MATCHES", len(values), 1)


def check_readiness_wait_is_csp_safe():
    print("\n[CSP-safe readiness]")
    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper"):
        source = open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read()
        check(f"{name} does not wait on an evaluated string "
              f"(wait_for_function would break under a CSP without unsafe-eval)",
              not re.search(r"\.wait_for_function\s*\(", source))


def check_engine_imports_driver_at_module_level():
    print("\n[driver imports]")
    # An engine that imports its driver inside the launch path imports cleanly
    # with the driver absent: the offline suite's group never skips, and the
    # CI job that exists to fail on an unexpected skip cannot catch a broken
    # import. This drifts back silently, so it is asserted.
    expected = {"playwright_scraper": "playwright",
                "puppeteer_scraper": "pyppeteer",
                "selenium_scraper": "selenium"}
    for name, driver in expected.items():
        tree = ast.parse(open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read())
        top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        found = any(
            (isinstance(n, ast.ImportFrom) and (n.module or "").startswith(driver))
            or (isinstance(n, ast.Import) and any(a.name.startswith(driver) for a in n.names))
            for n in top_level)
        check(f"{name} imports {driver} at module level", found)


def _module_sources():
    for name in sorted(os.listdir(REPO)):
        if name.endswith(".py") and name != "smoke_test.py":
            yield name, open(os.path.join(REPO, name), encoding="utf-8").read()


def check_no_undefined_names():
    print("\n[undefined names]")
    # compileall proves a file PARSES, not that its names RESOLVE. A live run
    # elsewhere in this family died with NameError on a line reached only
    # while fetching, after an import had been removed — invisible to import,
    # --help, compileall and 400 green assertions. Deliberately coarse (one
    # pool of bindings, no scope tracking) so it under-reports rather than
    # inventing problems.
    import builtins
    for name, source in _module_sources():
        tree = ast.parse(source)
        bound = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(node.name)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a = node.args
                    for arg in (a.posonlyargs + a.args + a.kwonlyargs
                                + ([a.vararg] if a.vararg else [])
                                + ([a.kwarg] if a.kwarg else [])):
                        bound.add(arg.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.Lambda):
                a = node.args
                for arg in (a.posonlyargs + a.args + a.kwonlyargs
                            + ([a.vararg] if a.vararg else [])
                            + ([a.kwarg] if a.kwarg else [])):
                    bound.add(arg.arg)
            elif isinstance(node, ast.comprehension):
                for target in ast.walk(node.target):
                    if isinstance(target, ast.Name):
                        bound.add(target.id)
            elif isinstance(node, ast.Global):
                bound.update(node.names)
        used = {n.id for n in ast.walk(tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        unknown = sorted(used - bound)
        check(f"{name}: every name it loads is imported, defined or assigned"
              + (f" (unknown: {unknown})" if unknown else ""), not unknown)


def check_shared_calls_bind():
    print("\n[shared call signatures]")
    # The check that found six broken call sites in one pass on another repo:
    # `classify(html, status, url)` took `status` positionally while two of
    # three engines called it `classify(html, url=...)`, and both crashed on
    # their FIRST fetch — invisible to import, --help, compileall and the
    # undefined-name walk above, because none of those calls a function the
    # way a live run does.
    shared_modules = {"product_parser": product_parser, "page_flow": page_flow,
                      "output_writer": output_writer, "captcha_solver": captcha_solver,
                      "proxy_pool": proxy_pool, "env_config": env_config}
    placeholder = object()
    for name, source in _module_sources():
        tree = ast.parse(source)
        # local name -> callable, for `from x import y` and `import x`
        callables, modules = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in shared_modules:
                for alias in node.names:
                    target = getattr(shared_modules[node.module], alias.name, None)
                    if callable(target):
                        callables[alias.asname or alias.name] = target
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in shared_modules:
                        modules[alias.asname or alias.name] = shared_modules[alias.name]
        problems = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = None
            if isinstance(node.func, ast.Name):
                target = callables.get(node.func.id)
            elif (isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id in modules):
                target = getattr(modules[node.func.value.id], node.func.attr, None)
                if not callable(target):
                    target = None
            if target is None or isinstance(target, type):
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or \
                    any(k.arg is None for k in node.keywords):
                continue    # *args/**kwargs: nothing to bind against
            try:
                signature = inspect.signature(target)
            except (TypeError, ValueError):
                continue
            try:
                signature.bind(*([placeholder] * len(node.args)),
                               **{k.arg: placeholder for k in node.keywords})
            except TypeError as e:
                problems.append(f"line {node.lineno}: {getattr(target, '__name__', target)} {e}")
        check(f"{name}: every call into a shared module binds against its real "
              f"signature" + (f" — {problems}" if problems else ""), not problems)


def check_banned_wording():
    print("\n[wording]")
    # Built from pieces so this file does not itself contain the banned
    # strings it is scanning for.
    banned = ["anti" + "detect", "cloud " + "browser", "gate." + "2prx.com",
              "ANTI" + "DETECT_LOCAL_API"]
    scanned = 0
    hits = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", "captures", "legacy", ".venv"}]
        for filename in files:
            if not filename.endswith((".py", ".md", ".yml", ".yaml", ".txt", ".example", ".toml")):
                continue
            if filename == "smoke_test.py":
                continue
            path = os.path.join(root, filename)
            text = open(path, encoding="utf-8", errors="replace").read().lower()
            scanned += 1
            for phrase in banned:
                if phrase.lower() in text:
                    hits.append(f"{os.path.relpath(path, REPO)}: {phrase!r}")
    check(f"no shipped file uses a banned product name ({scanned} files scanned)"
          + (f" — {hits}" if hits else ""), not hits)
    readme = os.path.join(REPO, "README.md")
    if os.path.exists(readme):
        text = open(readme, encoding="utf-8").read()
        check("the README names the Scraping Browser API",
              "Scraping Browser API" in text)


def check_removed_flags_stay_removed():
    print("\n[removed flags]")
    # Scoped to the ENGINES: --country is banned on a scraper (it could
    # disagree with the URL) and legitimate on fingerprint_client.py, where it
    # picks a fingerprint's locale.
    for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper",
                 "scraper_api_client"):
        source = open(os.path.join(REPO, f"{name}.py"), encoding="utf-8").read()
        check(f"{name} does not reintroduce --country",
              '"--country"' not in source)
        check(f"{name} does not reintroduce the removed local-solver flag",
              '"--' + "antidetect" + '"' not in source)
    fingerprint = open(os.path.join(REPO, "fingerprint_client.py"), encoding="utf-8").read()
    check("fingerprint_client.py DOES still have --country (it picks a "
          "fingerprint's locale, which is a different thing)",
          '"--country"' in fingerprint)
    check("and defaults --fp-tags to ONE OS-family tag, which is what the API "
          "accepts", all('"--fp-tags", default="Windows"' in
                         open(os.path.join(REPO, f"{n}.py"), encoding="utf-8").read()
                         for n in ("playwright_scraper", "puppeteer_scraper",
                                   "selenium_scraper")))


def check_env_example_matches_code():
    print("\n[.env.example]")
    example_path = os.path.join(REPO, ".env.example")
    if not os.path.exists(example_path):
        check(".env.example exists", False)
        return
    documented = set(env_config.documented_keys(example_path))
    known = set(env_config.ENV_KEYS)
    eq("every documented variable is read by the code", documented - known, set())
    eq("every variable the code reads is documented", known - documented, set())

    # Round-trip a COPIED example through the real loader: every credential
    # must read as unset. The braced placeholders 2Captcha's own docs use
    # ({login}, {password}) once sailed through a literal-only check and
    # produced a 401 a long way from its cause.
    saved = {k: os.environ.get(k) for k in known}
    try:
        for line in open(example_path, encoding="utf-8"):
            parsed = env_config._parse_line(line)
            if parsed:
                os.environ[parsed[0]] = parsed[1]
        credentials = {k for k in known
                       if "KEY" in k or "PROXY" in k or "CDP" in k}
        for key in sorted(credentials):
            eq(f"{key} from a copied .env.example reads as unset",
               env_config.env_value(key), None)
        # The other half of the same check: a NON-credential default must
        # survive being copied, or the example is useless. FLIPPA_URL is a
        # real listing URL on purpose.
        for key in sorted(known - credentials):
            check(f"{key} from a copied .env.example is still usable",
                  bool(env_config.env_value(key)))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    check("an empty value is unset and is NOT warned about "
          "(an unset CI secret arrives empty)", env_config.is_placeholder(""))
    check("a braced vendor example is treated as unset",
          env_config.is_placeholder("ws://{login}-zone-x:{password}@cb.2captcha.com:9222"))
    check("a real credentialled URL is NOT treated as a placeholder",
          not env_config.is_placeholder("ws://real-login:realpass@cb.2captcha.com:9222"))


def check_env_precedence():
    print("\n[.env precedence]")
    class Args:
        twocaptcha_key = None
        url = None
    saved = os.environ.get("TWOCAPTCHA_KEY")
    try:
        os.environ["TWOCAPTCHA_KEY"] = "from-the-environment"
        args = Args()
        env_config.apply(args, keys={"TWOCAPTCHA_KEY": "twocaptcha_key"}, quiet=True)
        eq("an unset flag is filled from the environment",
           args.twocaptcha_key, "from-the-environment")
        args = Args()
        args.twocaptcha_key = "typed-on-the-command-line"
        env_config.apply(args, keys={"TWOCAPTCHA_KEY": "twocaptcha_key"}, quiet=True)
        eq("an explicit flag always wins", args.twocaptcha_key,
           "typed-on-the-command-line")
    finally:
        if saved is None:
            os.environ.pop("TWOCAPTCHA_KEY", None)
        else:
            os.environ["TWOCAPTCHA_KEY"] = saved


def check_policy_constants_have_consumers():
    print("\n[policy constants]")
    # A policy constant nothing reads is the same defect as dead code, and
    # harder to see, because the prose around it reads like enforcement
    #.
    # A consumer may reach the constant through an accessor its own module
    # exposes — `state.policy` reads STATE_POLICY — so each entry names the
    # spellings that count as reading it. What is NOT allowed is a constant
    # with a paragraph of justification and no reader at all, or a second
    # copy of its values somewhere else (which is how `--proxy-rotate`'s
    # choices drifted away from ROTATE_MODES).
    constants = {
        "STATE_POLICY": ("page_flow.py", ("STATE_POLICY", ".policy")),
        "MIN_ASSET_REFERENCES": ("product_parser.py", ("MIN_ASSET_REFERENCES",)),
        "BOT_CHALLENGE_MARKERS": ("product_parser.py",
                                  ("BOT_CHALLENGE_MARKERS", "detect_bot_challenge")),
        "COMPLETE_STOP_REASONS": ("output_writer.py",
                                  ("COMPLETE_STOP_REASONS", "finish_run")),
        "ROTATE_MODES": ("proxy_pool.py", ("ROTATE_MODES",)),
        "ENV_KEYS": ("env_config.py", ("ENV_KEYS", "env_config.apply")),
    }
    for constant, (home, spellings) in constants.items():
        consumers = [name for name, source in _module_sources()
                     if name != home and any(sp in source for sp in spellings)]
        check(f"{constant} is read outside {home}"
              + (f" (by {consumers})" if consumers else " — NOTHING reads it"),
              bool(consumers))
    # The values themselves must not be copied: an engine spelling out
    # ["per-run", "per-page"] would look correct and drift silently.
    copies = [name for name, source in _module_sources()
              if name != "proxy_pool.py" and '"per-run", "per-page"' in source]
    check("no module re-spells ROTATE_MODES' values instead of importing them"
          + (f" ({copies} does)" if copies else ""), not copies)


def check_dockerfile_copies_what_it_imports():
    print("\n[Dockerfile]")
    path = os.path.join(REPO, "Dockerfile")
    if not os.path.exists(path):
        check("Dockerfile exists", False)
        return
    dockerfile = open(path, encoding="utf-8").read()
    # Join line continuations first: a multi-line COPY is the normal shape
    # here, and reading only its first line would let this check pass while
    # the image was missing everything after the first backslash.
    joined = re.sub(r"\\\s*\n", " ", dockerfile)
    copied = set()
    for line in re.findall(r"^COPY\s+(.+)$", joined, re.MULTILINE):
        for token in line.split():
            if token.endswith(".py"):
                copied.add(token)
    # The entrypoint's transitive local imports — the image should carry
    # exactly these. All three repos in this family once shipped an image that
    # died with ModuleNotFoundError on every invocation, --help included,
    # because one module was missing from this list, and CI never built it.
    local = {n[:-3] for n, _ in _module_sources()}
    needed, queue = set(), ["playwright_scraper"]
    while queue:
        module = queue.pop()
        if module in needed:
            continue
        needed.add(module)
        source = open(os.path.join(REPO, f"{module}.py"), encoding="utf-8").read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in local:
                queue.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in local:
                        queue.append(alias.name)
    missing = {f"{m}.py" for m in needed} - copied
    check("the Dockerfile COPY list carries every module the entrypoint "
          "imports" + (f" (missing {sorted(missing)})" if missing else ""),
          not missing)
    check("and carries no test suite or fixtures",
          "smoke_test.py" not in copied and "captures" not in dockerfile)
    check("and no .env is baked into the image",
          not re.search(r"^COPY\s+.*\.env(\s|$)", dockerfile, re.MULTILINE))


def check_sample_output():
    print("\n[sample output]")
    columns = list(asdict(Product()).keys())
    json_path = os.path.join(REPO, "sample_output.json")
    csv_path = os.path.join(REPO, "sample_output.csv")
    if not (os.path.exists(json_path) and os.path.exists(csv_path)):
        check("sample_output.json and sample_output.csv are committed", False)
        return
    rows = json.load(open(json_path, encoding="utf-8"))
    check("the sample holds rows from a real run", bool(rows))
    eq("its columns match the Product schema", list(rows[0].keys()), columns)
    with open(csv_path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    eq("the CSV header matches too", header, columns)
    blob = json.dumps(rows).lower()
    for marker in ("sample-listing", "lorem ipsum", "example.com", "your_api_key"):
        check(f"the sample is not fabricated ({marker!r} absent)", marker not in blob)
    check("every sample row names its provenance",
          all(r.get("price_source") for r in rows))


def check_oldest_supported_python_can_parse_it():
    print("\n[Python floor]")
    # pyproject.toml and the CI matrix both claim 3.9. Claiming a floor
    # without testing it is how a walrus operator or an `X | None` annotation
    # ships and breaks it for everyone on that version. This
    # parses every module under 3.9's grammar; CI additionally RUNS the suite
    # on 3.9, which is the half this cannot do.
    for name, source in _module_sources():
        try:
            ast.parse(source, filename=name, feature_version=(3, 9))
            ok, why = True, ""
        except SyntaxError as e:
            ok, why = False, f" — {e}"
        check(f"{name} parses under Python 3.9's grammar{why}", ok)
    for extra in ("smoke_test.py", os.path.join("tests", "test_smoke.py"),
                  os.path.join(".github", "ci_checks.py")):
        path = os.path.join(REPO, extra)
        if not os.path.exists(path):
            continue
        try:
            ast.parse(open(path, encoding="utf-8").read(), filename=extra,
                      feature_version=(3, 9))
            ok = True
        except SyntaxError as e:
            ok = False
            print(f"        {e}")
        check(f"{extra} parses under Python 3.9's grammar", ok)


def check_packaging_matches_the_tree():
    print("\n[packaging]")
    path = os.path.join(REPO, "pyproject.toml")
    if not os.path.exists(path):
        check("pyproject.toml exists", False)
        return
    try:
        import tomllib
    except ImportError:
        check("pyproject.toml cross-check (tomllib needs 3.11 — SKIPPED here, "
              "CI's 3.12 leg runs it)", True)
        return
    with open(path, "rb") as handle:
        config = tomllib.load(handle)
    declared = set(config["tool"]["setuptools"]["py-modules"])
    on_disk = {name[:-3] for name, _ in _module_sources()}
    eq("every module on disk is declared in pyproject", on_disk - declared, set())
    eq("and nothing declared is missing from disk", declared - on_disk, set())
    # requirements.txt and the dependency list are kept in sync BY HAND, and
    # CI installs only requirements.txt — so drift here goes unnoticed there.
    requirements = [line.split("#")[0].strip()
                    for line in open(os.path.join(REPO, "requirements.txt"),
                                     encoding="utf-8")
                    if line.strip() and not line.strip().startswith("#")]
    eq("pyproject's dependencies match requirements.txt",
       sorted(config["project"]["dependencies"]), sorted(requirements))
    for engine in ("playwright", "puppeteer", "selenium"):
        extra = config["project"]["optional-dependencies"][engine]
        pins = [line.split("#")[0].strip()
                for line in open(os.path.join(REPO, f"requirements-{engine}.txt"),
                                 encoding="utf-8")
                if line.strip() and not line.strip().startswith("#")]
        eq(f"the {engine} extra matches requirements-{engine}.txt",
           sorted(extra), sorted(pins))


def check_ci_checks_are_one_implementation():
    print("\n[CI checks]")
    # ONE implementation, invoked from here AND from the workflow. The older
    # repos in this family carried this script plus a narrower inline grep in
    # tests.yml, and the two disagreed: the inline one matched only ws:// and
    # wss://, so an http://user:pass@ credential would have sailed past CI,
    # while the script itself failed on its own main branch.
    path = os.path.join(REPO, ".github", "ci_checks.py")
    if not os.path.exists(path):
        check(".github/ci_checks.py exists", False)
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("ci_checks", path)
    ci_checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci_checks)

    failures = ci_checks.secret_check()
    check("nothing credential-shaped, no session material and no personal data "
          "is committed" + (f" — {failures[:3]}" if failures else ""), not failures)
    failures = ci_checks.sample_check()
    check("the committed sample is real and matches the schema"
          + (f" — {failures[:3]}" if failures else ""), not failures)

    # And the CLI itself, not only the functions underneath it: a signature
    # or a call that drifts inside main() is invisible to a test that only
    # exercises the internals, and CI invokes exactly this command line.
    import subprocess
    result = subprocess.run([sys.executable, path, "--all"],
                            capture_output=True, text=True, cwd=REPO)
    check("`python3 .github/ci_checks.py --all` exits 0"
          + ("" if result.returncode == 0 else
             f" — {(result.stdout + result.stderr).strip().splitlines()[-1][:120]}"),
          result.returncode == 0)

    workflow = os.path.join(REPO, ".github", "workflows", "tests.yml")
    if os.path.exists(workflow):
        text = open(workflow, encoding="utf-8").read()
        check("the workflow CALLS ci_checks.py rather than reimplementing it",
              "ci_checks.py" in text)
        check("and carries no inline credential grep of its own",
              not re.search(r"grep\s+-[a-zA-Z]*\s*['\"]?\(ws\|wss\)", text))
    else:
        check(".github/workflows/tests.yml exists", False)


# ---------------------------------------------------------------------------
# 6. The concurrency machinery, with the browser stubbed out
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Just enough of a `requests` response for the paid paths to run."""

    def __init__(self, payload, status=200, headers=None):
        self._payload, self.status_code = payload, status
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def check_credentialled_paths_run():
    print("\n[paths a credential gates]")
    # The paths nobody runs are the paths with no evidence behind them, and in
    # this family they are where the copied core rotted: a key printed to a
    # terminal, a user agent never applied, a documented flag that returns 400.
    # This repo has no 2Captcha key to test with, so the HTTP call is stubbed
    # and everything around it is executed for real — which is what catches a
    # name that does not resolve, a signature that drifted, or a response key
    # that is read but never returned.
    import requests
    import captcha_solver as cs
    import fingerprint_client as fc
    import scraper_api_client as sac

    calls = []
    params = []

    def fake_post(url, **kwargs):
        calls.append(url)
        if url.endswith("/createTask"):
            return _FakeResponse({"errorId": 0, "taskId": "T1"})
        if url.endswith("/getTaskResult"):
            return _FakeResponse({"errorId": 0, "status": "ready",
                                  "solution": {"token": "TOKEN-V2"}})
        if url.endswith("/getBalance"):
            return _FakeResponse({"errorId": 0, "balance": "12.5"})
        if "in.php" in url:
            return _FakeResponse({"status": 1, "request": "ID1"})
        if "tasks/sync" in url:
            return _FakeResponse({"status": 200, "body": PAGE_FIXTURE_HTML},
                                 headers={"x-debug": "cost=0.0005"})
        raise AssertionError(url)

    def fake_get(url, **kwargs):
        calls.append(url)
        params.append(kwargs.get("params") or {})
        if "res.php" in url:
            return _FakeResponse({"status": 1, "request": "TOKEN-V1"})
        if "fingerprint" in url:
            return _FakeResponse({
                "id": "fp1", "country": "de", "userAgent": {"value": "UA/9"},
                "screen": {"width": 1920, "height": 1080}, "locale": "de-DE",
                "timezone": "Europe/Berlin",
                "navigator": {"platform": "Win32", "hardwareConcurrency": 8},
                "webgl": {"vendor": "V", "renderer": "R"}})
        raise AssertionError(url)

    real_post, real_get, real_sleep = requests.post, requests.get, cs.time.sleep
    try:
        requests.post, requests.get, cs.time.sleep = fake_post, fake_get, lambda s: None
        turnstile = cs.CaptchaChallenge(kind="turnstile", sitekey="0xFIXTUREFIXTURE",
                                        page_url="https://flippa.com/signup")
        eq("the v2 solver returns its token", cs.solve(turnstile, "k" * 32), "TOKEN-V2")
        eq("the v1 fallback returns its token",
           cs.solve(turnstile, "k" * 32, api_version="v1"), "TOKEN-V1")
        v3 = cs.CaptchaChallenge(kind="recaptcha_v3", sitekey="6L" + "a" * 30,
                                 page_url="https://flippa.com/x")
        eq("and both speak reCAPTCHA too", cs.solve(v3, "k" * 32), "TOKEN-V2")
        eq("the balance check parses its answer", cs.get_balance("k" * 32), 12.5)
        check("the key never rides in a URL on the v2 path",
              all("key=" not in c for c in calls if "api.2captcha.com" in c))

        fp = fc.get_fingerprint("k" * 32, tags="Windows,Chrome,Desktop", cache_dir=None)
        kwargs = fc.playwright_context_kwargs(fp)
        eq("a fingerprint's user agent is actually applied",
           kwargs.get("user_agent"), "UA/9")
        eq("its locale comes from the response, not from the country",
           kwargs.get("locale"), "de-DE")
        eq("and its timezone is applied at all", kwargs.get("timezone_id"),
           "Europe/Berlin")
        # The defect this pins: every engine in the family shipped
        # --fp-tags "Windows,Chrome,Desktop", and the API answers 400 to a
        # list. A caller that passes one anyway gets it trimmed, and says so.
        sent = [p.get("tags") for p in params if "tags" in p]
        eq("a three-tag --fp-tags is trimmed to the one tag the API accepts",
           sent, ["Windows"])
        # The key DOES ride in this endpoint's query string — that is the
        # API's shape, not a choice — which is why the client wraps the call
        # and redacts before re-raising. Asserted in check_credentials_never_leak.
        check("the fingerprint call is the one that carries its key in the URL",
              any("key" in p for p in params))

        with tempfile.TemporaryDirectory() as tmp:
            class Args:
                url = "https://flippa.com/search"
                key = "k" * 32
                timeout = 60
                cdp_url = None
                wait_text = wait_element = wait_state = None
                pages = 1
                dump_html = None
                out = os.path.join(tmp, "api_run")
                category = "saas"
                format = "json"
                allow_empty = False
                retries = 0
                retry_delay = 0
                delay = 0

            with redirect_stdout(io.StringIO()):
                rc = sac.scrape(Args())
            eq("the browserless engine parses a real response and exits 0", rc, 0)
            rows = json.load(open(f"{os.path.join(tmp, 'api_run')}.json", encoding="utf-8"))
            eq("with the same rows the browser engines produce", len(rows), 2)
    finally:
        requests.post, requests.get, cs.time.sleep = real_post, real_get, real_sleep


def check_concurrency_machinery():
    print("\n[concurrency]")
    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        SKIPPED_GROUPS.append("concurrency (playwright not installed)")
        check("concurrency machinery (playwright not installed — SKIPPED)", True)
        return

    # A live run cannot always reach this code: pages 1 and 2 are fetched
    # alone and decide whether the rest may be addressed, so a blocked page 1
    # means the workers never start.
    class _StubSession:
        def __init__(self, *a, **kw):
            self.pool = None

        def open(self):
            return self

        def close(self):
            pass

    class _StubPlaywright:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Args:
        pages = 12
        delay = 0
        out = "stub"
        retries = 1

    original = (engine.sync_playwright, engine._BrowserSession, engine._fetch_one_page)
    fetched = []
    lock = __import__("threading").Lock()
    try:
        engine.sync_playwright = lambda: _StubPlaywright()
        engine._BrowserSession = _StubSession

        def fake_fetch(session, args, pool, page_num, url):
            with lock:
                fetched.append(page_num)
            outcome = engine.PageOutcome(page_num=page_num, url=url)
            outcome.state = page_flow.PageState(page_flow.CONTENT, "stub")
            outcome.products = [_row(sku=f"p{page_num}-{i}") for i in range(3)]
            return outcome

        engine._fetch_one_page = fake_fetch
        specs = [(n, f"u{n}") for n in range(3, 13)]
        results, unattempted, ran_out, empty_at = engine._fetch_pages_concurrently(
            Args(), None, specs, 4)
        check("no page reports an empty page when none was empty", empty_at is None)
        eq("every queued page is fetched exactly once",
           sorted(fetched), [n for n, _ in specs])
        eq("nothing is left unattempted when all pages succeed", unattempted, [])
        eq("outcomes are restorable to page order",
           [o.page_num for o in sorted(results, key=lambda o: o.page_num)],
           [n for n, _ in specs])

        # A page with no listings ends the listing and stops dispatch, so
        # asking for 50 pages of a 5-page search costs at most N-1 extra.
        fetched.clear()

        def fetch_until_empty(session, args, pool, page_num, url):
            with lock:
                fetched.append(page_num)
            outcome = engine.PageOutcome(page_num=page_num, url=url)
            outcome.state = page_flow.PageState(page_flow.CONTENT, "stub")
            outcome.products = [] if page_num >= 5 else [_row(sku=f"p{page_num}")]
            return outcome

        engine._fetch_one_page = fetch_until_empty
        results, unattempted, ran_out, empty_at = engine._fetch_pages_concurrently(
            Args(), None, specs, 2)
        check("the end of the listing stops dispatch", ran_out)
        eq("and the page that ended it is named, so the caller can tell a "
           "legitimate stop from a gap below it", empty_at, 5)
        check("so most of the queue is never fetched "
              f"({len(fetched)} fetched of {len(specs)} queued)",
              len(fetched) < len(specs))
        check("and the unattempted pages are REPORTED, not counted as failed",
              sorted(unattempted) == sorted(n for n, _ in specs if n not in fetched))

        # A worker that raises must neither hang the run nor lose its
        # siblings' pages.
        fetched.clear()

        def sometimes_explodes(session, args, pool, page_num, url):
            with lock:
                fetched.append(page_num)
            if page_num == 4:
                raise RuntimeError("stub worker failure")
            outcome = engine.PageOutcome(page_num=page_num, url=url)
            outcome.state = page_flow.PageState(page_flow.CONTENT, "stub")
            outcome.products = [_row(sku=f"p{page_num}")]
            return outcome

        engine._fetch_one_page = sometimes_explodes
        results, unattempted, ran_out, empty_at = engine._fetch_pages_concurrently(
            Args(), None, specs, 3)
        check("and its siblings' pages still come back", len(results) >= 1)

        # The regression this group exists for. The page that raised was
        # already off the queue, so before the fix it appeared in NO list —
        # not results, not unattempted, not pages_failed — and the run went on
        # to report itself complete with that page's listings missing.
        returned = {o.page_num for o in results}
        check("the page whose fetch raised is reported, not lost",
              4 in returned)
        lost = [o for o in results if o.page_num == 4][0]
        check("and it is reported as a page that yielded nothing",
              not lost.ok)
        eq("with a stop reason that names what happened",
           output_writer.failure_stop_reason(lost), "worker_lost_page")
        eq("every requested page is in exactly one of results/unattempted",
           sorted(returned | set(unattempted)), [n for n, _ in specs])

        # A worker can also die BEFORE the per-page handler above can run —
        # between taking a page off the queue and the fetch itself. The
        # reconciliation pass is the backstop for that, and it is tested
        # through a real failure rather than a stubbed one: the inter-page
        # pause is the code that sits in that gap, so a delay it cannot sleep
        # on kills the worker exactly there.
        fetched.clear()

        class HostileDelay:
            """Unusable as a number, so time.sleep(args.delay) raises."""

        class ArgsBadDelay(Args):
            delay = HostileDelay()

        engine._fetch_one_page = fake_fetch
        results, unattempted, ran_out, empty_at = (
            engine._fetch_pages_concurrently(ArgsBadDelay(), None, specs, 1))
        taken = sorted({o.page_num for o in results} | set(unattempted))
        eq("a worker dying between the queue and the fetch still leaves every "
           "page accounted for", taken, [n for n, _ in specs])
        reconciled = [o for o in results if o.lost]
        check("and the page it was holding is reconciled back as lost",
              len(reconciled) == 1 and reconciled[0].page_num == 4)
    finally:
        engine.sync_playwright, engine._BrowserSession, engine._fetch_one_page = original


def check_worker_pools_start_on_different_exits():
    print("\n[worker pools]")
    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        check("worker pools (playwright not installed — SKIPPED)", True)
        return
    pool = proxy_pool.ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    starts = [engine._worker_pool(pool, i).current for i in range(3)]
    eq("each worker starts on a different exit", len(set(starts)), 3)
    check("and holds its own pool object, so no thread needs a lock",
          engine._worker_pool(pool, 0) is not engine._worker_pool(pool, 0))
    eq("with no pool there is nothing to hand out",
       engine._worker_pool(None, 0), None)



def check_run_integrity():
    """No page may go missing, and no window may pass for a whole listing.

    Every check here is a case that used to exit 0 with status=complete while
    the output was short of what it claimed.
    """
    print("\n[run integrity]")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "run")

        # 1. A page that went missing without anything noticing. stop_reason
        #    still says "completed" because the engine never saw it go.
        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row()], prefix, "json", False, blocked=False,
                            stop_reason="completed", pages_requested=6,
                            pages_completed=5, start_url="u", final_url="u")
        eq("fewer pages back than asked for cannot be a complete run",
           rc, EXIT_PARTIAL)
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        eq("the sidecar says partial", meta["status"], "partial")
        eq("and the stop reason names the gap", meta["stop_reason"],
           "pages_missing")

        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row()], prefix, "json", False, blocked=False,
                            stop_reason="completed", pages_requested=6,
                            pages_completed=6, pages_missing=[4],
                            start_url="u", final_url="u")
        eq("a page reported missing is partial even when the count adds up",
           rc, EXIT_PARTIAL)
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        eq("and the sidecar names which page", meta["pages_missing"], [4])

        # 2. coverage: what the run went for, beside whether it got it.
        with redirect_stdout(io.StringIO()):
            finish_run([_row()], prefix, "json", False, blocked=False,
                       stop_reason="completed", pages_requested=3,
                       pages_completed=3, start_url="u", final_url="u")
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        eq("three pages of a longer listing is a complete WINDOW",
           (meta["status"], meta["coverage"]), ("complete", "window"))
        with redirect_stdout(io.StringIO()):
            finish_run([_row()], prefix, "json", False, blocked=False,
                       stop_reason="listing_exhausted", pages_requested=9,
                       pages_completed=4, start_url="u", final_url="u")
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        eq("reaching the end of the listing is exhaustive coverage",
           (meta["status"], meta["coverage"]), ("complete", "exhaustive"))

        # 3. A page that carried listings and parsed to nothing is a parse
        #    failure, never the end of the listing.
        for reason in ("content_unparsed", "page_never_painted",
                       "worker_lost_page", "pages_unattempted", "pages_missing"):
            check(f"{reason} is not a complete stop reason",
                  reason not in output_writer.COMPLETE_STOP_REASONS)

        class _Outcome:
            def __init__(self, state):
                self.state = state
                self.parse_failed = True
                self.load_failed = False
                self.lost = False

        eq("a page full of listings that would not parse is a schema failure",
           output_writer.failure_stop_reason(
               _Outcome(page_flow.PageState(page_flow.CONTENT, "25 records"))),
           "content_unparsed")
        eq("a page that never painted is named as that instead",
           output_writer.failure_stop_reason(
               _Outcome(page_flow.PageState(page_flow.UNPAINTED, "app shell"))),
           "page_never_painted")
        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row()], prefix, "json", False, blocked=False,
                            stop_reason="content_unparsed", pages_requested=3,
                            pages_completed=1, start_url="u", final_url="u")
        eq("a page that would not parse ends the run as partial",
           rc, EXIT_PARTIAL)

        # 4. The catalogue moving underneath the run is recorded, not hidden.
        with redirect_stdout(io.StringIO()):
            finish_run([_row()], prefix, "json", False, blocked=False,
                       stop_reason="completed", pages_requested=2,
                       pages_completed=2, total_results_first=672,
                       total_results_last=671, start_url="u", final_url="u")
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        check("a total_results that moved mid-run is flagged",
              meta["catalog_mutated"] is True)
        with redirect_stdout(io.StringIO()):
            finish_run([_row()], prefix, "json", False, blocked=False,
                       stop_reason="completed", pages_requested=2,
                       pages_completed=2, total_results_first=672,
                       total_results_last=672, start_url="u", final_url="u")
        meta = json.load(open(f"{prefix}.meta.json", encoding="utf-8"))
        check("and a steady one is not", meta["catalog_mutated"] is False)

    # 5. merge_pages records the overlap instead of gating on it: an
    #    insertion ahead of the cursor repeats one listing and loses nothing,
    #    so failing on it would turn every insertion into a false partial.
    merged, stats = output_writer.merge_pages([
        (1, [_row(sku="a"), _row(sku="b")]),
        (2, [_row(sku="b"), _row(sku="c")]),
    ])
    eq("an overlapping page still merges to one row per sku",
       [r.sku for r in merged], ["a", "b", "c"])
    eq("with the rows it started from counted", stats.rows_before_dedupe, 4)
    eq("and the repeat recorded", stats.duplicate_skus_across_pages, 1)
    eq("against the page it came from", stats.duplicate_pages, [2])
    merged, stats = output_writer.merge_pages([
        (2, [_row(sku="c")]), (1, [_row(sku="a")])])
    eq("pages merge in page order however they arrived",
       [r.sku for r in merged], ["a", "c"])


def check_csv_is_not_a_formula():
    """A scraped title is site-controlled text, and a CSV is opened in Excel."""
    print("\n[csv injection]")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "out.csv")
        output_writer.write_csv(
            [_row(sku="1", title='=HYPERLINK("http://evil","Click")'),
             _row(sku="2", title="+1-800-EVIL"),
             _row(sku="3", title="Normal SaaS business")], path)
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        check("a formula in a title is neutralised",
              rows[0]["title"].startswith("'="))
        check("and so is one that starts with a sign",
              rows[1]["title"].startswith("'+"))
        eq("ordinary text is left exactly as it was",
           rows[2]["title"], "Normal SaaS business")

    # The column that made the naive fix wrong: profit is legitimately
    # negative, and a quote in front of it would corrupt the number.
    eq("a negative number keeps its sign and its type",
       output_writer.csv_safe(-1234.5), -1234.5)
    eq("and None stays None", output_writer.csv_safe(None), None)


def check_writes_are_atomic():
    """A crash mid-write must not leave a truncated file where a good one was."""
    print("\n[atomic output]")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "out.json")
        output_writer.write_json([_row(sku="good")], path)

        def explode(f):
            f.write('[{"sku": "hal')
            raise RuntimeError("disk full")

        try:
            output_writer._atomic_write(path, explode)
        except RuntimeError:
            pass
        eq("a failed write leaves the previous file intact",
           [r["sku"] for r in json.load(open(path, encoding="utf-8"))], ["good"])
        leftovers = [n for n in os.listdir(tmp) if n.startswith(".tmp-")]
        eq("and no temporary file behind", leftovers, [])


def check_cli_refuses_impossible_values():
    """Bounds on the flags whose out-of-range values change what a run means."""
    print("\n[cli bounds]")
    import argparse as _argparse
    for value in ("0", "-1"):
        try:
            cli_types.positive_int(value)
            check(f"--pages/--retries {value} is refused", False)
        except _argparse.ArgumentTypeError:
            check(f"--pages/--retries {value} is refused", True)
    eq("a sane page count passes through", cli_types.positive_int("3"), 3)
    try:
        cli_types.non_negative_float("-1")
        check("a negative delay is refused", False)
    except _argparse.ArgumentTypeError:
        check("a negative delay is refused", True)
    eq("zero delay is allowed, it means no pause",
       cli_types.non_negative_float("0"), 0.0)
    check("a URL with no scheme is refused",
          bool(cli_types.check_listing_url("flippa.com/search")))
    check("and a non-http scheme too",
          bool(cli_types.check_listing_url("ftp://flippa.com/search")))
    eq("a real listing URL passes",
       cli_types.check_listing_url("https://flippa.com/search?x=1"), [])
    check("the expected host is recognised",
          cli_types.host_is_expected("https://flippa.com/search"))
    check("including a subdomain", cli_types.host_is_expected("https://www.flippa.com/x"))
    check("and a lookalike is not",
          not cli_types.host_is_expected("https://flippa.com.evil.test/x"))



def check_diff_refuses_a_window_comparison():
    """`complete` answers a different question from `covers the whole listing`.

    Two three-page runs of a 27-page listing are both complete. A listing that
    merely moved from page 3 to page 4 between them is absent from the second
    file, and reporting that as `removed` reads as "this business was sold".
    """
    print("\n[diff coverage]")
    import diff_runs

    def _write(prefix, skus, coverage, status="complete", **extra):
        with open(f"{prefix}.json", "w", encoding="utf-8") as f:
            json.dump([{"sku": s, "price": 100, "price_source": "state"}
                       for s in skus], f)
        meta = {"status": status, "coverage": coverage, "pages_completed": 3,
                "pages_requested": 3, "stop_reason": "completed",
                "total_results": 672}
        meta.update(extra)
        with open(f"{prefix}.meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f)

    with tempfile.TemporaryDirectory() as tmp:
        old = os.path.join(tmp, "old")
        new = os.path.join(tmp, "new")
        out = os.path.join(tmp, "diff.json")
        real_argv = sys.argv

        def run(*flags):
            sys.argv = ["diff_runs.py", "--old", f"{old}.json",
                        "--new", f"{new}.json", "--out", out, *flags]
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = diff_runs.main()
            return rc, buf.getvalue(), json.load(open(out, encoding="utf-8"))

        try:
            # Two window runs: the listing that "vanished" may just be on the
            # next page, so the assortment halves are not conclusions.
            _write(old, ["a", "b"], "window")
            _write(new, ["a", "c"], "window")
            rc, text, result = run()
            eq("a window diff still runs", rc, 0)
            check("but says the assortment is not comparable",
                  result["assortment_comparable"] is False)
            check("and explains why in words the reader can act on",
                  "WINDOW" in text and "left-window" in text)
            rc, _text, _result = run("--fail-on-change")
            eq("--fail-on-change does not fire on a window's added/removed",
               rc, 0)

            # Same two files, both runs exhaustive: now removed means removed.
            _write(old, ["a", "b"], "exhaustive")
            _write(new, ["a", "c"], "exhaustive")
            rc, _text, result = run()
            check("two exhaustive runs ARE comparable",
                  result["assortment_comparable"] is True)
            eq("and the diff says what left the listing",
               [p["sku"] for p in result["removed"]], ["b"])
            rc, _text, _result = run("--fail-on-change")
            eq("--fail-on-change fires on a real assortment change", rc, 1)

            # A price change is comparable either way.
            _write(old, ["a"], "window")
            with open(f"{new}.json", "w", encoding="utf-8") as f:
                json.dump([{"sku": "a", "price": 200, "price_source": "state"}], f)
            _write(new, [], "window")
            with open(f"{new}.json", "w", encoding="utf-8") as f:
                json.dump([{"sku": "a", "price": 200, "price_source": "state"}], f)
            rc, _text, _result = run("--fail-on-change")
            eq("a price change on a sku both runs hold still fires", rc, 1)

            # A catalogue edited mid-run is called out on its own.
            _write(old, ["a"], "exhaustive")
            _write(new, ["a"], "exhaustive", catalog_mutated=True,
                   total_results_first=672, total_results_last=671)
            _rc, text, result = run()
            check("a run that raced the catalogue is flagged in the diff",
                  result["assortment_comparable"] is False
                  and "edited" in text)

            # An older sidecar with no coverage key at all.
            _write(old, ["a"], None)
            _write(new, ["a"], None)
            _rc, text, _result = run()
            check("a sidecar written before coverage existed says so",
                  "unknown" in text)

            # And the refusal that was already there still refuses.
            _write(old, ["a"], "exhaustive", status="partial")
            _write(new, ["a"], "exhaustive")
            sys.argv = ["diff_runs.py", "--old", f"{old}.json",
                        "--new", f"{new}.json"]
            with redirect_stdout(io.StringIO()):
                rc = diff_runs.main()
            eq("a partial run is still refused outright", rc, 2)
        finally:
            sys.argv = real_argv


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    print("flippa-scraper offline suite")
    print("=" * 62)
    for group in (check_parser_values, check_second_locale,
                  check_known_limitations_are_pinned,
                  check_parser_fallback_path,
                  check_price_parsing, check_pagination_convention,
                  check_page_states, check_shortfall_arithmetic,
                  check_fingerprint_kwargs_are_ones_playwright_accepts,
                  check_captcha_detection,
                  check_credentials_never_leak,
                  check_remote_connect_failures_are_redacted,
                  check_proxy_rotation, check_proxy_preflight,
                  check_output_contract, check_engine_parity,
                  check_readiness_wait_is_csp_safe,
                  check_engine_imports_driver_at_module_level,
                  check_no_undefined_names, check_shared_calls_bind,
                  check_banned_wording, check_removed_flags_stay_removed,
                  check_env_example_matches_code, check_env_precedence,
                  check_policy_constants_have_consumers,
                  check_dockerfile_copies_what_it_imports, check_sample_output,
                  check_ci_checks_are_one_implementation,
                  check_oldest_supported_python_can_parse_it,
                  check_packaging_matches_the_tree,
                  check_credentialled_paths_run,
                  check_concurrency_machinery,
                  check_worker_pools_start_on_different_exits,
                  check_run_integrity, check_csv_is_not_a_formula,
                  check_writes_are_atomic,
                  check_cli_refuses_impossible_values,
                  check_diff_refuses_a_window_comparison):
        group()

    print("\n" + "=" * 62)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed, {len(SKIPPED)} skipped")
    for line in SKIPPED:
        print(f"  SKIPPED: {line}")
    if SKIPPED_GROUPS:
        # Printed in a shape CI greps for: "skipped, engine absent" reads
        # identically to a real import error, so the engine-smoke job fails
        # when this line appears in a run where every engine IS installed.
        print(f"{len(SKIPPED_GROUPS)} group(s) of checks SKIPPED: "
              + ", ".join(SKIPPED_GROUPS))
    for line in FAILED:
        print(f"  FAILED: {line}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
