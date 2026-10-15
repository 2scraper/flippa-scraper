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

**The fixtures are real captures, trimmed and verified.** All are from
2026-10-14, after Flippa's 2026-09-29 rebuild. PAGE_FIXTURE_HTML is five cards
cut from the daily canary's own browser capture of
/search?filter[property_type]=saas (CI run 38561094467); VARIANTS, LAST_PAGE,
FR, PAST_END and NO_MATCH are cut from plain-HTTP captures taken the same day
(/search, page 14 and page 15 of the same filter, /fr/search, and a keyword
that matches nothing). Each keeps its cards verbatim plus the site's own
result-count element and one filter counter that shares the card id prefix;
the <head> is cut down to a title and two asset references. Before commit,
every column of every row was compared with the same rows parsed from the
untrimmed capture and found identical (only `position` differs).

What is NOT verbatim: each card's watch-button <form> was removed (it carries
a CSRF `authenticity_token`), and the broker's photo URL and name in a card's
header strip were replaced with placeholders ("Broker Placeholder"). The two
tags at the end of PAGE_FIXTURE_HTML (`<captcha-widgets>` and a
`chrome-extension://` script) are the 2Captcha Scraping Browser extension's
injection, carried over from the previous fixture so the captcha checks keep
running against that shape; the canary's plain Chromium capture has neither.

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
                            parse_products, prices_in, strip_percentages)

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


PAGE_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Online Businesses for Sale | Flippa</title><link rel="stylesheet" href="https://static.flippa.com/assets/application-0.css"><script src="https://static.flippa.com/assets/turbo-0.js"></script></head><body><span class="tw-text-gray-400 peer-checked:tw-text-primary-600 tw-text-xs" id="listing-category-count-saas">671</span><ul><li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-11816135">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-oxford-900 tw-text-gray-100">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Brokered by Flippa
  </p>
<img alt="Broker Placeholder" class="tw-size-12 @3xl:tw-size-16 tw-shrink-0 tw-rounded-full tw-object-cover tw-border-2 !tw-border-solid tw-border-white tw-bg-white tw-shadow-md" loading="lazy" src="https://static2.flippa.com/avatar_placeholder.png"/>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/11816135-social-media-entertainment-business-founded-in-2014-generating-2m-in-revenue-with-a-33-profit-margin-operates-multiple-websites-in-the-online-dating-sector">
          SaaS | Internet
        </a>
</h4>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">Social media entertainment business founded in 2014, generating $2M in revenue with a 33% profit margin. Operates multiple websites in the online dating sector.</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>Canada</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $160K</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $51.7K</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Profit Multiple</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">4.1x</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">12 years</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">SaaS</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Internet</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-hidden @3xl:tw-block tw-leading-tight tw-font-medium tw-font-mon tw-text-black">Asking price</p>
<div class="tw-flex tw-flex-wrap tw-items-baseline tw-gap-2">
<span class="tw-order-1 @3xl:tw-order-2 tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-green-700 tw-font-bold tw-font-mon tw-whitespace-nowrap">USD $2,520,000</span>
<span class="tw-order-2 @3xl:tw-order-1 tw-font-medium tw-font-mon tw-text-gray-400 tw-line-through">USD $3,500,000</span>
</div>
<div class="tw-w-fit !tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-emerald-50 tw-text-emerald-600 !tw-text-xs !tw-px-2.5 tw-rounded">
<svg class="tw-size-2.5" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/solid/arrow-down-46cbedbb.svg#svg" xlink:href="https://flippa.com/assets/icons/library/solid/arrow-down-46cbedbb.svg#svg"></use> </svg>
  Reduced 28%
</div>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_11816135">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/11816135-social-media-entertainment-business-founded-in-2014-generating-2m-in-revenue-with-a-33-profit-margin-operates-multiple-websites-in-the-online-dating-sector">
    View Listing
</a></div>
</div>
</div>
</li>
<li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-14025709">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-primary-900 tw-text-white">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Listed by Partner Broker
  </p>
<img alt="Broker Placeholder" class="tw-size-12 @3xl:tw-size-16 tw-shrink-0 tw-rounded-full tw-object-cover tw-border-2 !tw-border-solid tw-border-white tw-bg-white tw-shadow-md" loading="lazy" src="https://static2.flippa.com/avatar_placeholder.png"/>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/14025709-micro-saas-vectorization-tool-earning-subscription-ads-revenue">
          SaaS | Design and Style
        </a>
</h4>
<div class="tw-flex tw-flex-wrap tw-items-center tw-gap-1.5">
<div class="">
<div class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-bg-gray-100 !tw-text-gray-900 !tw-text-xs !tw-px-2.5 tw-relative tw-gap-2 tw-rounded-md tw-py-1">
    Data Verified
  <button aria-label="Google Analytics" class="tw-flex tw-p-0 tw-border-0 tw-bg-transparent" data-title="Google Analytics" data-tooltip-ready="" data-trigger="hover" rel="tooltip" type="button">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="18" viewbox="0 0 45 40" width="18" xmlns="http://www.w3.org/2000/svg">
<path d="M25.3335 4.77273V34.7727C25.3335 38.1295 27.6494 40 30.1062 40C32.379 40 34.879 38.4091 34.879 34.7727V5C34.879 1.92273 32.6063 0 30.1062 0C27.6062 0 25.3335 2.12045 25.3335 4.77273Z" fill="#F9AB00"></path>
<path d="M12.8335 20V34.7727C12.8335 38.1296 15.1494 40 17.6062 40C19.879 40 22.379 38.4091 22.379 34.7727V20.2273C22.379 17.15 20.1063 15.2273 17.6062 15.2273C15.1062 15.2273 12.8335 17.3477 12.8335 20Z" fill="#E37400"></path>
<path d="M5.10625 40C7.74216 40 9.879 37.8632 9.879 35.2273C9.879 32.5914 7.74216 30.4545 5.10625 30.4545C2.47033 30.4545 0.333496 32.5914 0.333496 35.2273C0.333496 37.8632 2.47033 40 5.10625 40Z" fill="#E37400"></path>
</svg>
<span class="popper-tooltip tw-hidden tw-text-sm tw-absolute tw-bg-slate-950 tw-text-white tw-font-light tw-font-sans tw-max-w-72 tw-text-center tw-rounded-md tw-p-2.5" id="tooltip-content">Google Analytics<div class="popper-arrow" data-popper-arrow=""></div></span></button>
</div>
</div>
</div>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">Micro SaaS Vectorization Tool Earning Subscription &amp; Ads revenue! </p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>United Kingdom</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $554</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $386</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Profit Multiple</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">1.6x</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">2 years</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">SaaS</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Design and Style</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-hidden @3xl:tw-block tw-leading-tight tw-font-medium tw-font-mon tw-text-black">Asking price</p>
<p class="tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-oxford-900 @3xl:tw-text-black tw-font-bold tw-font-mon tw-whitespace-nowrap !tw-mb-0">USD $7,500</p>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_14025709">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/14025709-micro-saas-vectorization-tool-earning-subscription-ads-revenue">
    View Listing
</a></div>
</div>
</div>
</li>
<li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-14178878">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-gradient-to-r tw-from-primary-200/35 tw-to-primary-200/50 tw-text-primary-600">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Marketplace · Listed by Owner
  </p>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/14178878-established-newbie-friendly-website-business-2000-mo-profit-high-growth-potential-no-marketing-cost-easy-to-operate-sign-nda-watch-marketing-demo">
          SaaS | Internet
        </a>
</h4>
<div class="tw-flex tw-flex-wrap tw-items-center tw-gap-1.5">
<div class="">
<div class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-bg-gray-100 !tw-text-gray-900 !tw-text-xs !tw-px-2.5 tw-relative tw-gap-2 tw-rounded-md tw-py-1">
    Data Verified
  <button aria-label="Paypal" class="tw-flex tw-p-0 tw-border-0 tw-bg-transparent" data-title="Paypal" data-tooltip-ready="" data-trigger="hover" rel="tooltip" type="button">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="40" viewbox="0 0 33 40" width="33" xmlns="http://www.w3.org/2000/svg">
<path d="M9.37459 38.644L10.0494 34.2406L8.54625 34.2049H1.36865L6.35672 1.71256C6.37157 1.61328 6.42065 1.52279 6.49502 1.45758C6.56938 1.39237 6.66408 1.35679 6.76185 1.35732H18.8643C22.8821 1.35732 25.6548 2.21625 27.1025 3.91158C27.7811 4.70689 28.2134 5.53799 28.4224 6.45259C28.6417 7.41226 28.6456 8.55883 28.4314 9.95725L28.4159 10.0593V10.9554L29.0946 11.3504C29.6127 11.62 30.0784 11.9848 30.4687 12.4267C31.0493 13.1067 31.4248 13.9709 31.5835 14.9955C31.7473 16.0493 31.6931 17.3032 31.4248 18.7229C31.1151 20.3559 30.6145 21.7782 29.9384 22.942C29.3419 23.9846 28.5399 24.8875 27.5837 25.593C26.6857 26.2478 25.6187 26.7449 24.4123 27.063C23.2434 27.3758 21.9106 27.5335 20.4487 27.5335H19.5068C18.8333 27.5335 18.1792 27.7827 17.6657 28.2294C17.1529 28.6809 16.8127 29.3047 16.7057 29.9897L16.6348 30.3861L15.4426 38.1469L15.3884 38.4319C15.3742 38.522 15.3497 38.5671 15.3136 38.5976C15.2786 38.627 15.2349 38.6434 15.1897 38.644H9.37459Z" fill="#253B80"></path>
<path d="M29.7378 10.1627C29.7017 10.4 29.6604 10.6425 29.614 10.8917C28.018 19.3101 22.5577 22.2182 15.5839 22.2182H12.0332C11.1803 22.2182 10.4617 22.8545 10.3288 23.7187L8.51083 35.5635L7.99602 38.921C7.97551 39.0542 7.98334 39.1904 8.01897 39.3202C8.0546 39.4499 8.11718 39.5702 8.2024 39.6728C8.28762 39.7753 8.39347 39.8577 8.51264 39.9142C8.63181 39.9706 8.76149 39.9999 8.89274 40H15.1904C15.9362 40 16.5697 39.4433 16.6871 38.6877L16.749 38.359L17.9347 30.6286L18.0109 30.2045C18.127 29.4463 18.7618 28.8896 19.5075 28.8896H20.4494C26.5509 28.8896 31.3274 26.3446 32.7235 18.98C33.3066 15.9035 33.0047 13.3347 31.4616 11.528C30.9725 10.9691 30.3884 10.5064 29.7378 10.1627Z" fill="#179BD7"></path>
<path d="M28.0676 9.47874C27.558 9.32729 27.0396 9.20908 26.5155 9.12483C25.4798 8.96132 24.4331 8.88286 23.3853 8.89021H13.8995C13.5385 8.88993 13.1893 9.02233 12.9151 9.26349C12.6408 9.50465 12.4596 9.83865 12.4041 10.2051L10.3862 23.3357L10.3281 23.7187C10.391 23.3007 10.5974 22.9196 10.91 22.6444C11.2226 22.3692 11.6208 22.218 12.0325 22.2182H15.5833C22.557 22.2182 28.0173 19.3087 29.6133 10.8917C29.6611 10.6425 29.7011 10.4 29.7372 10.1627C29.316 9.93581 28.8771 9.7456 28.425 9.59406C28.3065 9.55366 28.1873 9.51521 28.0676 9.47874Z" fill="#222D65"></path>
<path d="M12.4042 10.2051C12.4592 9.83855 12.6404 9.50439 12.9148 9.26335C13.1891 9.02232 13.5386 8.89039 13.8996 8.89154H23.3855C24.5093 8.89154 25.5582 8.9671 26.5156 9.12616C27.1634 9.23076 27.8023 9.3872 28.4264 9.59406C28.8974 9.75445 29.3348 9.944 29.7386 10.1627C30.2134 7.05173 29.7347 4.93356 28.0974 3.01554C26.2924 0.904 23.0345 0 18.8658 0H6.76332C5.91176 0 5.18536 0.636246 5.05375 1.50181L0.0127859 34.3281C-0.0106941 34.4805 -0.00176074 34.6364 0.0389714 34.7849C0.0797035 34.9334 0.151268 35.0711 0.248741 35.1885C0.346215 35.3059 0.467285 35.4002 0.603622 35.4649C0.73996 35.5297 0.88833 35.5633 1.03853 35.5635H8.5103L10.3863 23.3357L12.4042 10.2051Z" fill="#253B80"></path>
</svg>
<span class="popper-tooltip tw-hidden tw-text-sm tw-absolute tw-bg-slate-950 tw-text-white tw-font-light tw-font-sans tw-max-w-72 tw-text-center tw-rounded-md tw-p-2.5" id="tooltip-content">Paypal<div class="popper-arrow" data-popper-arrow=""></div></span></button>
</div>
</div>
</div>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">Established Newbie-Friendly Website Business | $2000/mo Profit | High Growth Potential | No Marketing Cost | Easy to Operate | Sign NDA &amp; Watch Marketing Demo
</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>India</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $4.37K</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $2.02K</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Profit Multiple</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">1.4x</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">8 years</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">SaaS</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Internet</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-hidden @3xl:tw-block tw-leading-tight tw-font-medium tw-font-mon tw-text-black">Auction</p>
<p class="tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-oxford-900 @3xl:tw-text-black tw-font-bold tw-font-mon tw-whitespace-nowrap !tw-mb-0">USD $4,000</p>
<div class="tw-w-fit !tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-orange-50 !tw-text-orange-800 !tw-text-xs !tw-px-2.5 tw-rounded">
    21 bids · 4 days left
</div>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_14178878">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/14178878-established-newbie-friendly-website-business-2000-mo-profit-high-growth-potential-no-marketing-cost-easy-to-operate-sign-nda-watch-marketing-demo">
    View Listing
</a></div>
</div>
</div>
</li>
<li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-13774133">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-gradient-to-r tw-from-primary-200/35 tw-to-primary-200/50 tw-text-primary-600">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Marketplace · Listed by Owner
  </p>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/13774133-glammypet-is-an-ai-pet-grooming-saas-on-an-8-year-aged-domain-1-250-value-earn-recurring-income-from-groomer-subscriptions-ready-to-launch-business">
          SaaS &amp; AI Marketplace | Luxury Pet Grooming Finder (GlamMyPet.com)
        </a>
</h4>
<div class="tw-flex tw-flex-wrap tw-items-center tw-gap-1.5">
<div class="">
<div class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-bg-gray-100 !tw-text-gray-900 !tw-text-xs !tw-px-2.5 tw-relative tw-gap-2 tw-rounded-md tw-py-1">
    Data Verified
  <button aria-label="Google Analytics" class="tw-flex tw-p-0 tw-border-0 tw-bg-transparent" data-title="Google Analytics" data-tooltip-ready="" data-trigger="hover" rel="tooltip" type="button">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="18" viewbox="0 0 45 40" width="18" xmlns="http://www.w3.org/2000/svg">
<path d="M25.3335 4.77273V34.7727C25.3335 38.1295 27.6494 40 30.1062 40C32.379 40 34.879 38.4091 34.879 34.7727V5C34.879 1.92273 32.6063 0 30.1062 0C27.6062 0 25.3335 2.12045 25.3335 4.77273Z" fill="#F9AB00"></path>
<path d="M12.8335 20V34.7727C12.8335 38.1296 15.1494 40 17.6062 40C19.879 40 22.379 38.4091 22.379 34.7727V20.2273C22.379 17.15 20.1063 15.2273 17.6062 15.2273C15.1062 15.2273 12.8335 17.3477 12.8335 20Z" fill="#E37400"></path>
<path d="M5.10625 40C7.74216 40 9.879 37.8632 9.879 35.2273C9.879 32.5914 7.74216 30.4545 5.10625 30.4545C2.47033 30.4545 0.333496 32.5914 0.333496 35.2273C0.333496 37.8632 2.47033 40 5.10625 40Z" fill="#E37400"></path>
</svg>
<span class="popper-tooltip tw-hidden tw-text-sm tw-absolute tw-bg-slate-950 tw-text-white tw-font-light tw-font-sans tw-max-w-72 tw-text-center tw-rounded-md tw-p-2.5" id="tooltip-content">Google Analytics<div class="popper-arrow" data-popper-arrow=""></div></span></button>
</div>
</div>
</div>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">GlamMyPet is an AI Pet Grooming SaaS on an 8-Year Aged Domain ($1,250 Value). Earn recurring income from groomer subscriptions. Ready-to-launch business.</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>CA, United States</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $2</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $2</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">8 years</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">SaaS</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Home and Garden</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-hidden @3xl:tw-block tw-leading-tight tw-font-medium tw-font-mon tw-text-black">Auction</p>
<p class="tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-oxford-900 @3xl:tw-text-black tw-font-bold tw-font-mon tw-whitespace-nowrap !tw-mb-0">USD $105</p>
<div class="tw-w-fit !tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-orange-50 !tw-text-orange-800 !tw-text-xs !tw-px-2.5 tw-rounded">
    1 bid · 7 days left
</div>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_13774133">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/13774133-glammypet-is-an-ai-pet-grooming-saas-on-an-8-year-aged-domain-1-250-value-earn-recurring-income-from-groomer-subscriptions-ready-to-launch-business">
    View Listing
</a></div>
</div>
</div>
</li>
<li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-13829251">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-gradient-to-r tw-from-primary-200/35 tw-to-primary-200/50 tw-text-primary-600">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Marketplace · Listed by Owner
  </p>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/13829251-profitable-3d-product-design-saas-in-the-generative-ai-space-with-1k-mrr-and-growing">
          SaaS | AI Product Design Platform | Lightloop.io
        </a>
</h4>
<div class="tw-flex tw-flex-wrap tw-items-center tw-gap-1.5">
<div class="">
<div class="!tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-bg-gray-100 !tw-text-gray-900 !tw-text-xs !tw-px-2.5 tw-relative tw-gap-2 tw-rounded-md tw-py-1">
    Data Verified
  <button aria-label="Stripe" class="tw-flex tw-p-0 tw-border-0 tw-bg-transparent" data-title="Stripe" data-tooltip-ready="" data-trigger="hover" rel="tooltip" type="button">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="18" viewbox="0 0 18 18" width="18" xmlns="http://www.w3.org/2000/svg">
<rect fill="#635BFF" height="18" rx="4" width="18"></rect>
<path clip-rule="evenodd" d="M8.28921 6.97802C8.28921 6.54945 8.64199 6.38462 9.22629 6.38462C10.0642 6.38462 11.1225 6.63736 11.9604 7.08791V4.50549C11.0453 4.14286 10.1413 4 9.22629 4C6.98831 4 5.5 5.16484 5.5 7.10989C5.5 10.1429 9.68932 9.65934 9.68932 10.967C9.68932 11.4725 9.24834 11.6374 8.63097 11.6374C7.71593 11.6374 6.54733 11.2637 5.62127 10.7582V13.3736C6.64655 13.8132 7.68286 14 8.63097 14C10.9241 14 12.5006 12.8681 12.5006 10.9011C12.4896 7.62637 8.28921 8.20879 8.28921 6.97802Z" fill="white" fill-rule="evenodd"></path>
</svg>
<span class="popper-tooltip tw-hidden tw-text-sm tw-absolute tw-bg-slate-950 tw-text-white tw-font-light tw-font-sans tw-max-w-72 tw-text-center tw-rounded-md tw-p-2.5" id="tooltip-content">Stripe<div class="popper-arrow" data-popper-arrow=""></div></span></button>
</div>
</div>
</div>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">Profitable 3D product design SaaS in the generative AI space with $1K+ MRR and growing.</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>Canada</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $1.45K</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $1.4K</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">&lt;1 year</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">SaaS</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Business</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-hidden @3xl:tw-block tw-leading-tight tw-font-medium tw-font-mon tw-text-black">Auction</p>
<p class="tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-oxford-900 @3xl:tw-text-black tw-font-bold tw-font-mon tw-whitespace-nowrap !tw-mb-0">USD $9,000</p>
<div class="tw-w-fit !tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-orange-50 !tw-text-orange-800 !tw-text-xs !tw-px-2.5 tw-rounded">
    Starting Price
</div>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_13829251">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/13829251-profitable-3d-product-design-saas-in-the-generative-ai-space-with-1k-mrr-and-growing">
    View Listing
</a></div>
</div>
</div>
</li></ul><span id="filters-show-results-count">Show 671 results</span><captcha-widgets></captcha-widgets><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/turnstile/hunter.js" data-ts-input="cf-turnstile-response"></script></body></html>"""

# Two cards the canary page did not happen to carry, from a plain-HTTP capture
# of /search the same day: an open-to-offers listing (a price GUIDE, which is
# a range and not an asking price) and a first-access listing whose card has
# a countdown strip below the price column.
VARIANTS_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Online Businesses for Sale | Flippa</title><link rel="stylesheet" href="https://static.flippa.com/assets/application-0.css"><script src="https://static.flippa.com/assets/turbo-0.js"></script></head><body><span class="tw-text-gray-400 peer-checked:tw-text-primary-600 tw-text-xs" id="listing-category-count-saas">676</span><ul><li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-12282016">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-primary-900 tw-text-white">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Listed by Partner Broker
  </p>
<img alt="Broker Placeholder" class="tw-size-12 @3xl:tw-size-16 tw-shrink-0 tw-rounded-full tw-object-cover tw-border-2 !tw-border-solid tw-border-white tw-bg-white tw-shadow-md" loading="lazy" src="https://static2.flippa.com/avatar_placeholder.png"/>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/12282016-ai-powered-child-safety-saas-with-50-state-predator-database-integration-100-functional-tech-98-margin-potential-and-untapped-commercial-market">
          Child Overwatch
        </a>
</h4>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">AI-powered child safety SaaS with 50-state predator database integration, 100% functional tech, 98% margin potential, and untapped commercial market</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>CA, United States</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">N/A</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">1 year</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Saas &amp; Software Projects</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Business</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-text-xl tw-leading-tight tw-text-black tw-font-bold tw-font-mon !tw-mb-0">Open to Offers</p>
<div class="tw-w-fit !tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-gray-100 !tw-text-gray-900 tw-rounded-md !tw-px-3 !tw-text-sm">
  Price Guide: USD $75,000 - USD $100,000
</div>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_12282016">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/12282016-ai-powered-child-safety-saas-with-50-state-predator-database-integration-100-functional-tech-98-margin-potential-and-untapped-commercial-market">
    View Listing
</a></div>
</div>
</div>
</li>
<li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-14273497">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-oxford-900 tw-text-gray-100">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Brokered by Flippa
  </p>
<img alt="Broker Placeholder" class="tw-size-12 @3xl:tw-size-16 tw-shrink-0 tw-rounded-full tw-object-cover tw-border-2 !tw-border-solid tw-border-white tw-bg-white tw-shadow-md" loading="lazy" src="https://static2.flippa.com/avatar_placeholder.png"/>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/14273497-3-6m-followers-235k-profit-85-margin-2-hrs-week-wellness-social-portfolio">
          Facebook Account | Health and Beauty
        </a>
</h4>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">3.6M+ Followers | $235K Profit | ~85% Margin | 2 Hrs/Week | Wellness Social Portfolio</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>FL, United States</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $31.9K</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">USD $14K</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Profit Multiple</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">1.6x</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">&lt;1 year</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Facebook Account</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Health and Beauty</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-hidden @3xl:tw-block tw-leading-tight tw-font-medium tw-font-mon tw-text-black">Asking price</p>
<p class="tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-oxford-900 @3xl:tw-text-black tw-font-bold tw-font-mon tw-whitespace-nowrap !tw-mb-0">USD $200,000</p>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_14273497">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/14273497-3-6m-followers-235k-profit-85-margin-2-hrs-week-wellness-social-portfolio">
    View Listing
</a></div>
</div>
</div>
<div class="tw-min-h-11 @3xl:tw-h-11 tw-px-5 @3xl:tw-px-6 tw-py-2 tw-rounded-b-xl tw-font-mon tw-flex tw-items-center tw-justify-between @3xl:tw-justify-center tw-gap-2.5 @3xl:tw-gap-5 tw-bg-primary-100 tw-text-primary-900">
<div class="tw-flex tw-items-center tw-gap-2.5 tw-min-w-0">
<svg class="tw-size-4 tw-shrink-0" fill="none" height="24" viewbox="0 0 24 24" width="24" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/solid/lock-0237c35e.svg#svg" xlink:href="https://flippa.com/assets/icons/library/solid/lock-0237c35e.svg#svg"></use> </svg>
<p class="tw-text-xs @3xl:tw-text-sm tw-leading-snug !tw-mb-0">
<a class="tw-relative !tw-text-primary-900 !tw-underline tw-font-medium" data-turbo="false" href="https://flippa.com/product/premium">
          Purchase
          <span class="tw-font-bold">First Access</span>
</a>
        to view this listing before everyone else. Goes public on
        <strong><span class="@3xl:tw-hidden">23 Oct</span><span class="tw-hidden @3xl:tw-inline">Oct 23, 2026</span></strong>
</p>
</div>
<div class="tw-size-14 tw-flex tw-flex-col tw-justify-center tw-overflow-hidden tw-text-white tw-text-center tw-whitespace-nowrap tw-bg-blue-500 tw-transition-width tw-duration-600 tw-ease-in-out tw-rounded-full tw-shrink-0" style="background:
  radial-gradient(closest-side, white 79%, transparent 80% 100%),
  conic-gradient(#003CAF 56.99999999999999%,#D3DBE3 0);
}">
<span class="tw-text-black tw-font-semibold"><div class="tw-leading-none tw-font-bold tw-text-primary-900 tw-font-mon">9</div><div class="!tw-text-[7px] tw-leading-tight tw-font-semibold tw-uppercase tw-text-primary-900 tw-font-mon">Days Left</div></span>
</div>
</div>
</li></ul><span id="filters-show-results-count">Show 6,073 results</span></body></html>"""

# The last page of the canary's own filter (page 14 of a 676-match listing):
# a partial-equity sale with no price and a NEGATIVE monthly profit, and an
# auction with no bids yet.
LAST_PAGE_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Online Businesses for Sale | Flippa</title><link rel="stylesheet" href="https://static.flippa.com/assets/application-0.css"><script src="https://static.flippa.com/assets/turbo-0.js"></script></head><body><span class="tw-text-gray-400 peer-checked:tw-text-primary-600 tw-text-xs" id="listing-category-count-saas">676</span><ul><li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-12274326">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-gradient-to-r tw-from-primary-200/35 tw-to-primary-200/50 tw-text-primary-600">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Marketplace · Listed by Owner
  </p>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/12274326-25-of-evaficy-com-mvp-qa-test-cases-generator-ai-integration-and-editor">
          Evaficy Smart Test
        </a>
</h4>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">25% of Evaficy.com MVP QA test cases generator(AI integration) and editor.</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>Romania</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">N/A</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">-USD $9</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">1 year</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">SaaS</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Internet</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-oxford-900 @3xl:tw-text-black tw-font-bold tw-font-mon tw-whitespace-nowrap !tw-mb-0">
  Partial Sale: 25% Equity
</p>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_12274326">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/12274326-25-of-evaficy-com-mvp-qa-test-cases-generator-ai-integration-and-editor">
    View Listing
</a></div>
</div>
</div>
</li>
<li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-14319943">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-gradient-to-r tw-from-primary-200/35 tw-to-primary-200/50 tw-text-primary-600">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Marketplace · Listed by Owner
  </p>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/14319943-production-ready-space-tech-saas-for-satellite-risk-monitoring-orbital-data-visualization-and-conjunction-analysis">
          Orbital Guard
        </a>
</h4>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">Production-ready space-tech SaaS for satellite risk monitoring, orbital data visualization, and conjunction analysis.</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>Pakistan</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Revenue</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">N/A</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Monthly Profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">N/A</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Age</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">&lt;1 year</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">SaaS</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Industry</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Internet</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-hidden @3xl:tw-block tw-leading-tight tw-font-medium tw-font-mon tw-text-black">Auction</p>
<p class="tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-oxford-900 @3xl:tw-text-black tw-font-bold tw-font-mon tw-whitespace-nowrap !tw-mb-0">USD $500</p>
<div class="tw-w-fit !tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-orange-50 !tw-text-orange-800 !tw-text-xs !tw-px-2.5 tw-rounded">
    Starting Price
</div>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_14319943">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/14319943-production-ready-space-tech-saas-for-satellite-risk-monitoring-orbital-data-visualization-and-conjunction-analysis">
    View Listing
</a></div>
</div>
</div>
</li></ul><span id="filters-show-results-count">Show 676 results</span></body></html>"""

FR_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Entreprises en ligne à vendre | Flippa</title><link rel="stylesheet" href="https://static.flippa.com/assets/application-0.css"><script src="https://static.flippa.com/assets/turbo-0.js"></script></head><body><span class="tw-text-gray-400 peer-checked:tw-text-primary-600 tw-text-xs" id="listing-category-count-saas">676</span><ul><li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-11816135">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-oxford-900 tw-text-gray-100">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Négocié par Flippa
  </p>
<img alt="Broker Placeholder" class="tw-size-12 @3xl:tw-size-16 tw-shrink-0 tw-rounded-full tw-object-cover tw-border-2 !tw-border-solid tw-border-white tw-bg-white tw-shadow-md" loading="lazy" src="https://static2.flippa.com/avatar_placeholder.png"/>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/fr/11816135-social-media-entertainment-business-founded-in-2014-generating-2m-in-revenue-with-a-33-profit-margin-operates-multiple-websites-in-the-online-dating-sector" rel="nofollow">
          SaaS | Internet
        </a>
</h4>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">Entreprise de divertissement sur les réseaux sociaux fondée en 2014, générant un chiffre d'affaires de 2 millions de dollars avec une marge bénéficiaire de 33 %. Elle exploite plusieurs sites web dans le secteur des rencontres en ligne.</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>Canada</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Revenu mensuel</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">144K € (EUR)</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Bénéfice mensuel</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">46,4K € (EUR)</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Multiple de profit</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">4.1x</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Ancienneté</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">12 années</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">SaaS</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Secteur</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Internet</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-hidden @3xl:tw-block tw-leading-tight tw-font-medium tw-font-mon tw-text-black">Prix demandé</p>
<div class="tw-flex tw-flex-wrap tw-items-baseline tw-gap-2">
<span class="tw-order-1 @3xl:tw-order-2 tw-text-xl @3xl:tw-text-2xl tw-leading-none @3xl:tw-leading-tight tw-text-green-700 tw-font-bold tw-font-mon tw-whitespace-nowrap">2 262 363 € (EUR)</span>
<span class="tw-order-2 @3xl:tw-order-1 tw-font-medium tw-font-mon tw-text-gray-400 tw-line-through">3 142 171 € (EUR)</span>
</div>
<div class="tw-w-fit !tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-emerald-50 tw-text-emerald-600 !tw-text-xs !tw-px-2.5 tw-rounded">
<svg class="tw-size-2.5" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/solid/arrow-down-46cbedbb.svg#svg" xlink:href="https://flippa.com/assets/icons/library/solid/arrow-down-46cbedbb.svg#svg"></use> </svg>
  Réduit de 28%
</div>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_11816135">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/fr/11816135-social-media-entertainment-business-founded-in-2014-generating-2m-in-revenue-with-a-33-profit-margin-operates-multiple-websites-in-the-online-dating-sector" rel="nofollow">
    Voir l'Annonce
</a></div>
</div>
</div>
</li>
<li class="tw-relative tw-w-full tw-@container tw-bg-white tw-rounded-xl tw-border tw-border-solid tw-border-gray-200 tw-shadow-sm hover:tw-shadow-lg tw-duration-200" id="listing-12282016">
<div class="tw-rounded-t-xl tw-px-5 @3xl:tw-px-6 tw-font-mon tw-flex tw-items-center tw-justify-end tw-gap-2.5 @3xl:tw-gap-4 tw-h-9 @3xl:tw-h-10 tw-bg-primary-900 tw-text-white">
<p class="tw-text-xs @3xl:tw-text-sm tw-font-medium !tw-mb-0">
    Publié par un Broker Partenaire
  </p>
<img alt="Broker Placeholder" class="tw-size-12 @3xl:tw-size-16 tw-shrink-0 tw-rounded-full tw-object-cover tw-border-2 !tw-border-solid tw-border-white tw-bg-white tw-shadow-md" loading="lazy" src="https://static2.flippa.com/avatar_placeholder.png"/>
</div>
<div class="tw-p-5 @3xl:tw-p-6 tw-flex tw-flex-col tw-gap-5 @3xl:tw-grid @3xl:tw-grid-cols-[minmax(0,1fr)_auto] @3xl:tw-gap-x-16 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-3 @3xl:tw-gap-3.5 @3xl:tw-col-start-1 @3xl:tw-row-start-1">
<h4 class="!tw-text-lg @3xl:!tw-text-xl !tw-font-bold tw-font-mon !tw-leading-normal !tw-text-primary-700 tw-truncate tw-whitespace-normal !tw-mb-0">
<a class="!tw-no-underline !tw-text-primary-700 after:tw-content-[''] after:tw-absolute after:tw-inset-0 GTM-search-result-card" data-turbo="false" href="https://flippa.com/fr/12282016-ai-powered-child-safety-saas-with-50-state-predator-database-integration-100-functional-tech-98-margin-potential-and-untapped-commercial-market" rel="nofollow">
          Child Overwatch
        </a>
</h4>
<p class="tw-text-sm @3xl:tw-text-base tw-text-gray-700 @3xl:tw-leading-normal tw-line-clamp-3 !tw-mb-0">Logiciel SaaS de sécurité pour enfants alimenté par l'IA, avec intégration de la base de données des prédateurs des 50 États, technologie fonctionnelle à 100 %, potentiel de marge de 98 % et marché commercial inexploité.</p>
<div class="tw-flex tw-items-center tw-gap-1 tw-text-sm tw-text-gray-700">
<svg class="tw-size-3.5 tw-shrink-0" fill="none" height="20" viewbox="0 0 20 20" width="20" xmlns="http://www.w3.org/2000/svg"> <use href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg" xlink:href="https://flippa.com/assets/icons/library/outline/map-pin-outline-12cd78af.svg#svg"></use> </svg>
<span>CA, États-Unis</span>
</div>
</div>
<div class="tw-min-w-0 @3xl:tw-col-start-1 @3xl:tw-row-start-3">
<div class="tw-grid tw-grid-cols-3 tw-gap-x-3 tw-gap-y-2.5 @3xl:tw-flex @3xl:tw-flex-wrap @3xl:tw-gap-x-9 @3xl:max-xl:tw-gap-x-6 @3xl:tw-gap-y-4">
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Revenu mensuel</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">N/A</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Ancienneté</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">1 année</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Type</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Projets SaaS &amp; Logiciels</p>
</div>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-gap-0.5 @3xl:tw-gap-1">
<p class="tw-text-[11px] @5xl:tw-text-[13px] @6xl:tw-text-sm tw-text-gray-500 @3xl:tw-text-gray-600 !tw-mb-0 tw-truncate">Secteur</p>
<p class="tw-text-sm @3xl:tw-text-[13px] @5xl:tw-text-[15px] @6xl:tw-text-base tw-font-bold tw-text-gray-800 tw-font-mon !tw-mb-0 tw-truncate">Entreprise</p>
</div>
</div>
</div>
<hr class="!tw-m-0 tw-border-0 tw-border-t tw-border-solid tw-border-gray-200 @3xl:tw-col-span-full @3xl:tw-row-start-2"/>
<div class="tw-min-w-0 tw-flex tw-flex-col tw-items-start @3xl:tw-items-end tw-gap-2.5 @3xl:tw-col-start-2 @3xl:tw-row-start-1 @3xl:tw-self-start">
<p class="tw-text-xl tw-leading-tight tw-text-black tw-font-bold tw-font-mon !tw-mb-0">Ouvert aux Offres</p>
<div class="tw-w-fit !tw-py-0.5 tw-inline-flex tw-font-medium tw-font-mon tw-items-center tw-gap-1 tw-bg-gray-100 !tw-text-gray-900 tw-rounded-md !tw-px-3 !tw-text-sm">
  Fourchette de Prix : 67 332 € (EUR) - 89 776 € (EUR)
</div>
</div>
<div class="tw-mt-auto @3xl:tw-mt-0 @3xl:tw-col-start-2 @3xl:tw-row-start-3 @3xl:tw-self-center @3xl:tw-justify-self-end">
<div class="tw-relative tw-flex tw-items-center tw-gap-2.5">
<div class="tw-contents" id="watch_button_12282016">
</div>
<a class="tw-inline-flex tw-justify-center tw-items-center tw-gap-2 tw-font-medium !tw-font-mon !tw-no-underline focus:!tw-outline-0 focus-visible:!tw-outline-0 focus:!tw-ring-4 focus-visible:!tw-ring-4 tw-duration-200 tw-border tw-border-solid tw-cursor-pointer !tw-bg-primary-700 hover:!tw-bg-primary-800 !tw-text-white focus:tw-ring-primary-200 tw-border-primary-700 hover:tw-border-primary-800 disabled:!tw-bg-gray-300 disabled:hover:!tw-bg-gray-300 disabled:!tw-text-white disabled:tw-border-gray-300 disabled:hover:tw-border-gray-300 disabled:tw-pointer-events-none aria-disabled:!tw-bg-gray-300 aria-disabled:hover:!tw-bg-gray-300 aria-disabled:!tw-text-white aria-disabled:tw-border-gray-300 aria-disabled:tw-pointer-events-none !tw-text-sm tw-px-5 tw-py-2.5 !tw-rounded-full tw-flex-1 @3xl:tw-flex-none GTM-search-result-card" data-turbo="false" href="https://flippa.com/fr/12282016-ai-powered-child-safety-saas-with-50-state-predator-database-integration-100-functional-tech-98-margin-potential-and-untapped-commercial-market" rel="nofollow">
    Voir l'Annonce
</a></div>
</div>
</div>
</li></ul><span id="filters-show-results-count">Afficher 6 073 résultats</span></body></html>"""

# Page 15 of that same 676-match filter: HTTP 200, no cards, and the site's
# own count still in the filter panel.
PAST_END_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Online Businesses for Sale | Flippa</title><link rel="stylesheet" href="https://static.flippa.com/assets/application-0.css"><script src="https://static.flippa.com/assets/turbo-0.js"></script></head><body><span class="tw-text-gray-400 peer-checked:tw-text-primary-600 tw-text-xs" id="listing-category-count-saas">676</span><ul></ul><span id="filters-show-results-count">Show 676 results</span></body></html>"""

# A keyword that matches nothing: no cards, and "Show 0 results".
NO_MATCH_FIXTURE_HTML = """<!DOCTYPE html><html><head><title>Online Businesses for Sale | Flippa</title><link rel="stylesheet" href="https://static.flippa.com/assets/application-0.css"><script src="https://static.flippa.com/assets/turbo-0.js"></script></head><body><span class="tw-text-gray-400 peer-checked:tw-text-primary-600 tw-text-xs" id="listing-category-count-saas">0</span><ul></ul><span id="filters-show-results-count">Show 0 results</span></body></html>"""

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

# A page built out of the site's own assets with no cards and no result count
# in it: served, but not (yet) a listing. Trimmed to what the classifier reads.
SHELL_FIXTURE_HTML = ("<!DOCTYPE html><html><head><title>Flippa</title>" +
                      "".join(f'<link rel="preload" href="https://static.flippa.com/assets/a{i}.js">'
                              for i in range(20)) +
                      '</head><body><div id="search-scrollable-results">'
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
    eq("five rows from the five-card fixture", len(rows), 5)
    eq("rows come out in page order",
       [r.sku for r in rows],
       ["11816135", "14025709", "14178878", "13774133", "13829251"])
    eq("the filter counter that shares the id prefix is not a card",
       product_parser.count_cards(PAGE_FIXTURE_HTML), 5)
    by_sku = {r.sku: r for r in rows}

    a = by_sku.get("11816135")
    check("the price-dropped card parsed", a is not None)
    if a:
        eq("title is the card heading", a.title, "SaaS | Internet")
        eq("url is the listing's own link, slug and all", a.url,
           "https://flippa.com/11816135-social-media-entertainment-business-"
           "founded-in-2014-generating-2m-in-revenue-with-a-33-profit-margin-"
           "operates-multiple-websites-in-the-online-dating-sector")
        eq("price is the reduced figure", a.price, 2520000.0)
        eq("original_price is the struck-through figure", a.original_price, 3500000.0)
        eq("currency is read, not defaulted", a.currency, "USD")
        # 3,500,000 -> 2,520,000 is 28.0%; the site's own badge says
        # "Reduced 28%". The COMPUTED figure is what goes in the column.
        eq("discount is computed from the two prices", a.discount_pct, 28.0)
        eq("asset type", a.asset_type, "SaaS")
        eq("industry", a.industry, "Internet")
        # The card ABBREVIATES both monthly figures: "USD $51.7K", "USD $160K".
        eq("monthly profit, scaled from the abbreviation", a.monthly_profit, 51700.0)
        eq("monthly revenue, scaled from the abbreviation", a.monthly_revenue, 160000.0)
        eq("profit multiple as printed (4.1x)", a.profit_multiple, 4.1)
        eq("age text as the site writes it", a.age_text, "12 years")
        eq("country", a.country, "Canada")
        eq("brokered by Flippa", a.managed_by_flippa, True)
        eq("broker named on the card (placeholder in this fixture)",
           a.broker_name, "Broker Placeholder")
        eq("no bid count on a fixed-price listing", a.bid_count, None)
        eq("category label from the run", a.category, "saas")
        eq("page column", a.page, 1)
        eq("position column", a.position, 1)
        eq("price_source says the card was read", a.price_source, "dom")
        check("description is the seller's own line",
              a.description and a.description.startswith(
                  "Social media entertainment business founded in 2014"))
        # Not on the card since the 2026-09-29 rebuild: null, never guessed.
        for column in ("monetization", "revenue_multiple", "status",
                       "sale_method", "sponsored", "editors_choice",
                       "confidential", "verified_listing", "image_url"):
            eq(f"{column} is null (not on the card)", getattr(a, column), None)

    b = by_sku.get("14025709")
    check("the partner-broker card parsed", b is not None)
    if b:
        eq("a plain asking price", b.price, 7500.0)
        eq("no original price on a listing that did not drop", b.original_price, None)
        eq("no discount without an original price", b.discount_pct, None)
        eq("a partner broker is not Flippa", b.managed_by_flippa, False)
        eq("position is 1-based within the page", b.position, 2)

    c = by_sku.get("14178878")
    check("the auction card parsed", c is not None)
    if c:
        eq("auction price is the figure, not the bid count", c.price, 4000.0)
        eq("auction bid count ('21 bids · 4 days left')", c.bid_count, 21)
        eq("an owner listing is not brokered by Flippa", c.managed_by_flippa, False)
        eq("and names no broker", c.broker_name, None)
    eq("a single bid ('1 bid')",
       getattr(by_sku.get("13774133"), "bid_count", "missing"), 1)
    eq("an auction with no bids yet has a starting price",
       getattr(by_sku.get("13829251"), "price", "missing"), 9000.0)

    # page+position must be unique across a multi-page run: `position`
    # restarts at 1 on every page, so the column is worthless without the pair.
    page2 = parse_products(PAGE_FIXTURE_HTML, "https://flippa.com/search", page=2)
    pairs = [(r.page, r.position) for r in rows + page2]
    eq("page+position is unique across pages", len(set(pairs)), len(pairs))


def check_second_locale():
    print("\n[a second locale]")
    # Flippa's own hreflang set publishes /fr/ and /es/ paths. The French
    # page CONVERTS the prices and translates the labels — the same listing
    # as PAGE_FIXTURE_HTML's first card, captured the same day.
    rows = parse_products(FR_FIXTURE_HTML, "https://flippa.com/fr/search",
                          category=category_from_url("https://flippa.com/fr/search"),
                          page=1)
    if not eq("the French page parses", len(rows), 2):
        return
    row = rows[0]
    eq("the same listing, same id", row.sku, "11816135")
    eq("the link carries the locale", row.url.startswith(
        "https://flippa.com/fr/11816135-"), True)
    # "2 262 363 € (EUR)": the ISO code comes AFTER the amount, in brackets,
    # and the thousands are space-grouped.
    eq("the price is in euros on the French page", row.currency, "EUR")
    eq("and is the converted amount", row.price, 2262363.0)
    eq("the struck-through original too", row.original_price, 3142171.0)
    # "46,4K € (EUR)": a decimal COMMA before the abbreviation.
    eq("monthly profit with a decimal comma and a K", row.monthly_profit, 46400.0)
    eq("monthly revenue", row.monthly_revenue, 144000.0)
    eq("profit multiple under a French label", row.profit_multiple, 4.1)
    eq("age under a French label", row.age_text, "12 années")
    eq("industry under a French label ('Secteur')", row.industry, "Internet")
    eq("'Négocié par Flippa' is brokered by Flippa", row.managed_by_flippa, True)
    eq("open to offers in French has no price either", rows[1].price, None)
    # The locale segment is not a category.
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
       "https://flippa.com/fr/search?page=2")
    eq("the French result count is read through its grouping space",
       product_parser.results_count(FR_FIXTURE_HTML), 6073)


def check_known_limitations_are_pinned():
    print("\n[pinned limitations]")
    # Where a defence would be worse than the gap, the CURRENT behaviour is
    # asserted with the reason, so a future change is a decision rather than a
    # surprise.
    #
    # 1. The card abbreviates the monthly figures, so they carry three
    #    significant figures. Nothing on the search page states them exactly.
    rows = parse_products(PAGE_FIXTURE_HTML, "https://flippa.com/search")
    if not check("the fixture parses (needed for the pins below)", rows):
        return
    a = rows[0]
    eq("monthly profit is the site's rounded figure, not an exact one",
       a.monthly_profit, 51700.0)
    # 2. The multiple is reported AS PUBLISHED, never recomputed:
    #    2,520,000 / (12 x 51,700) is 4.06, which the site prints as 4.1x.
    eq("the site's own multiple is reported as published", a.profit_multiple, 4.1)
    check("even though it is not exactly price / (12 x profit)",
          abs(a.price / (12 * a.monthly_profit) - a.profit_multiple) > 0.01)
    # 3. Key-data labels are read from a table of the three languages the site
    #    publishes. A fourth language would leave those columns null rather
    #    than guess; the fixture-backed check above proves the French table.
    check("the label table covers en, fr and es for every column it fills",
          all(sum(1 for c in product_parser._KEY_DATA_LABELS.values() if c == col) >= 2
              for col in set(product_parser._KEY_DATA_LABELS.values())))


def check_parser_fallback_path():
    print("\n[parser: card variants]")
    rows = {r.sku: r for r in parse_products(VARIANTS_FIXTURE_HTML,
                                             "https://flippa.com/search", page=1)}
    eq("both variant cards parse", sorted(rows), ["12282016", "14273497"])
    offers = rows.get("12282016")
    if offers:
        # "Price Guide: USD $75,000 - USD $100,000" is a RANGE. The site
        # prints no asking price, and neither does the row.
        eq("open to offers: no price", offers.price, None)
        eq("open to offers: no currency invented", offers.currency, None)
        eq("open to offers: no discount", offers.discount_pct, None)
        eq("a partner broker's card names the broker",
           offers.broker_name, "Broker Placeholder")
        eq("and is not brokered by Flippa", offers.managed_by_flippa, False)
    first = rows.get("14273497")
    if first:
        # The price sits in a <p>, not the <span> of a reduced card, and a
        # countdown strip follows the price column.
        eq("first-access listing: the asking price", first.price, 200000.0)
        eq("first-access listing: currency", first.currency, "USD")
        eq("a sub-1-year age as written", first.age_text, "<1 year")
        eq("a state-qualified country", first.country, "FL, United States")

    last = {r.sku: r for r in parse_products(LAST_PAGE_FIXTURE_HTML,
                                             "https://flippa.com/search", page=14)}
    partial = last.get("12274326")
    if partial:
        # "Partial Sale: 25% Equity" carries a percentage and no money.
        eq("a partial-equity sale has no asking price", partial.price, None)
        eq("a loss keeps its sign ('-USD $9')", partial.monthly_profit, -9.0)
        eq("'N/A' revenue is null, not zero", partial.monthly_revenue, None)
        eq("no multiple printed, none reported", partial.profit_multiple, None)
    auction = last.get("14319943")
    if auction:
        eq("an auction with no bids yet: the starting price", auction.price, 500.0)
        eq("and no bid count is invented from 'Starting Price'",
           auction.bid_count, None)


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
    # The site's own next link is ?page=2&pagination=true; page 1 carries no
    # page parameter. ?page=N alone returns the same listings (measured
    # 2026-10-14), so that is what is built.
    eq("page 1 carries no page parameter",
       page_url("https://flippa.com/search", 1), "https://flippa.com/search")
    eq("page 2 uses the site's own parameter",
       page_url("https://flippa.com/search", 2),
       "https://flippa.com/search?page=2")
    eq("existing filters are preserved",
       page_url("https://flippa.com/search?filter%5Bproperty_type%5D=saas", 3),
       "https://flippa.com/search?filter%5Bproperty_type%5D=saas&page=3")
    eq("a stale ?page= is replaced, not appended to",
       page_url("https://flippa.com/search?page=7", 2),
       "https://flippa.com/search?page=2")
    eq("the pre-rebuild page[number] spelling is replaced too, never doubled",
       page_url("https://flippa.com/search?page%5Bnumber%5D=9", 4),
       "https://flippa.com/search?page=4")
    eq("the site's own next link is re-addressed, keeping its other parameter",
       page_url("https://flippa.com/search?page=2&pagination=true", 5),
       "https://flippa.com/search?pagination=true&page=5")
    eq("category shortcuts paginate the same way",
       page_url("https://flippa.com/websites", 2),
       "https://flippa.com/websites?page=2")
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
    eq("a page with listing cards is content", content.state, page_flow.CONTENT)
    eq("and records the site's own count", content.total_results, 671)
    check("content is not retried", not content.policy.retry)

    empty = page_flow.classify(NO_MATCH_FIXTURE_HTML)
    eq("'Show 0 results' is an empty listing", empty.state, page_flow.EMPTY)
    check("an empty listing is a complete answer", empty.policy.complete)
    check("an empty listing is not blocked", not empty.policy.blocked)
    # The site's own zero outranks any card: a card on a zero-match page
    # could only be a placement, and reporting it would be a fabricated row.
    with_card = NO_MATCH_FIXTURE_HTML.replace(
        "<ul></ul>", '<ul><li id="listing-12345678"></li></ul>')
    eq("a card on a zero-match page does not make it content",
       page_flow.classify(with_card).state, page_flow.EMPTY)
    eq("and is not reported", parse_products(with_card, "https://flippa.com/search"), [])

    past_end = page_flow.classify(PAST_END_FIXTURE_HTML)
    eq("a page past the end (no cards, 676 results) is exhausted",
       past_end.state, page_flow.EXHAUSTED)
    check("being past the end is a complete answer", past_end.policy.complete)
    eq("past-the-end keeps the site's count", past_end.total_results, 676)

    shell = page_flow.classify(SHELL_FIXTURE_HTML)
    eq("a served page with no cards and no count is unpainted, not blocked",
       shell.state, page_flow.UNPAINTED)
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

    # Count a marker on a page you KNOW is good before adding it. Cloudflare's
    # JS-detections tag is on every page Flippa serves. Run against a local,
    # uncommitted full capture when one is present (see CONTRIBUTING.md).
    capture = os.path.join(REPO, "captures", "live_search_page1_2026-10-14.html")
    good = (open(capture, encoding="utf-8", errors="replace").read()
            if os.path.exists(capture) else None)
    if good:
        eq("a real served page is content, though it references "
           "cdn-cgi/challenge-platform", page_flow.classify(good).state,
           page_flow.CONTENT)
        firing = [m for markers in product_parser.BOT_CHALLENGE_MARKERS.values()
                  for m in markers if m in good]
        check("no challenge marker appears on a good full listing page"
              + (f" — {firing} does" if firing else ""), not firing)
        for tempting in ("cdn-cgi", "challenge-platform", "cloudflare"):
            check(f"(measured: {tempting!r} appears {good.count(tempting)}x on "
                  f"that good page, which is why it is not a marker)",
                  good.count(tempting) > 0)
    else:
        skip("a real served page is content",
             "captures/live_search_page1_2026-10-14.html is not in this "
             "checkout — see CONTRIBUTING.md on recording one")


# ---------------------------------------------------------------------------
# 3. Captcha detection
# ---------------------------------------------------------------------------
def check_shortfall_arithmetic():
    print("\n[page coverage]")
    # The site publishes its own match count, so "did this page bring back
    # everything it should have?" is arithmetic rather than a threshold.
    eq("a full first page is 50", page_flow.expected_records(724, 1), 50)
    # Measured 2026-10-14: page 14 of a 676-match filter held 26 cards.
    eq("the last page holds the remainder", page_flow.expected_records(676, 14), 26)
    eq("a page past the end expects nothing", page_flow.expected_records(676, 15), 0)
    eq("no total means no expectation", page_flow.expected_records(None, 1), None)
    # 10000 is the site's CAP, printed as "10,000+" — a cap is not a count,
    # and treating it as one would report a phantom shortfall on every large
    # listing.
    eq("the 10,000 cap is not treated as a count",
       page_flow.expected_records(10000, 2), None)
    check("a full page reports no shortfall",
          page_flow.shortfall(724, 1, 50) is None)
    message = page_flow.shortfall(724, 1, 38)
    check("a short page is named, with both numbers in the message",
          message and "38" in message and "50" in message)


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
        skip('fingerprint kwargs vs the real new_context signature', 'playwright not installed')
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
        skip("the engine's own connect path redacts", 'playwright not installed')
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
# 5. The engines: parity, signatures, undefined names and imports
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
        skip('engine parity', 'no engine library installed here')
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


def check_no_private_citations():
    print("\n[no citations of private instruction files]")
    # The rules this repo follows are stated in its own words. A pointer into
    # a file the public cannot open ("see section N of the template") reads as
    # a reference nobody can follow. Assembled from pieces so this file does
    # not match itself; covers this file too, and dotfiles like .gitignore.
    section = chr(0xA7)
    patterns = [re.compile(re.escape(section) + r"\s*\d"),
                re.compile(re.escape("CLAUDE" + ".md"), re.I),
                re.compile(re.escape("claude_" + "2scraper"), re.I),
                re.compile(re.escape("2scraper_" + "repo_rules"), re.I),
                re.compile(r"\b(?:play" + r"book|family temp" + r"late)\b", re.I)]
    scanned, hits = 0, []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", "captures", "legacy"}
                   and not os.path.isfile(os.path.join(root, d, "pyvenv.cfg"))]
        for filename in files:
            if not filename.endswith((".py", ".md", ".yml", ".yaml", ".txt",
                                      ".example", ".toml", ".cfg")) \
                    and filename not in (".gitignore", ".dockerignore", "Dockerfile"):
                continue
            path = os.path.join(root, filename)
            text = open(path, encoding="utf-8", errors="replace").read()
            scanned += 1
            for pattern in patterns:
                m = pattern.search(text)
                if m:
                    line = text[:m.start()].count("\n") + 1
                    hits.append(f"{os.path.relpath(path, REPO)}:{line}")
    check(f"no shipped file cites a private instruction file ({scanned} files "
          f"scanned)" + (f" — {hits}" if hits else ""), not hits and scanned > 10)


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
        skip('pyproject.toml cross-check', "tomllib needs 3.11; here, CI's 3.12 leg runs it")
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
            return _FakeResponse({"status": "ok", "http_code": 200, "body": PAGE_FIXTURE_HTML},
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
            eq("with the same rows the browser engines produce", len(rows), 5)
    finally:
        requests.post, requests.get, cs.time.sleep = real_post, real_get, real_sleep


def check_concurrency_machinery():
    print("\n[concurrency]")
    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        SKIPPED_GROUPS.append("concurrency (playwright not installed)")
        skip('concurrency machinery', 'playwright not installed')
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
        skip('worker pools', 'playwright not installed')
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
               _Outcome(page_flow.PageState(page_flow.CONTENT, "50 cards"))),
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


def check_zero_rows_exit_codes():
    """0 rows is 'empty' (4) only when something PROVES the listing empty."""
    print("\n[zero-row exit codes]")
    with tempfile.TemporaryDirectory() as tmp:
        def run(reason, blocked=False):
            with redirect_stdout(io.StringIO()):
                return finish_run([], os.path.join(tmp, "z"), "both", False,
                                  blocked=blocked, stop_reason=reason,
                                  pages_requested=1, pages_completed=0,
                                  start_url="u", final_url="u")
        # Previously every one of these returned 4, "the catalogue is empty",
        # for a run that never saw the catalogue.
        for reason in ("content_unparsed", "page_never_painted",
                       "page_load_timeout", "worker_lost_page", "api_error",
                       "a_reason_added_later"):
            eq(f"0 rows after {reason!r} is exit 5, content never obtained",
               run(reason), output_writer.EXIT_CONTENT_NOT_OBTAINED)
        for reason in ("no_results", "listing_exhausted", "no_new_listings"):
            eq(f"0 rows after {reason!r} is exit 4, a genuinely empty listing",
               run(reason), EXIT_NO_PRODUCTS)
        eq("a block outranks both", run("page_load_timeout", blocked=True),
           EXIT_BLOCKED)
        eq("and nothing is written for any of them", sorted(os.listdir(tmp)), [])
        with redirect_stdout(io.StringIO()):
            rc = finish_run([_row(sku="1")], os.path.join(tmp, "p"), "json",
                            False, blocked=False, stop_reason="page_load_timeout",
                            pages_requested=2, pages_completed=1,
                            start_url="u", final_url="u")
        eq("rows, then a failure, is still exit 6", rc, EXIT_PARTIAL)
    eq("the engines' remote-API code is the same number",
       output_writer.EXIT_API_ERROR, 5)


def check_output_file_modes():
    """Outputs get the mode a plain open() would give them, not mkstemp's 0600."""
    print("\n[output file modes]")
    if os.name != "posix":
        skip("output file modes", "not a POSIX system")
        return
    old = os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for umask, want in ((0o022, 0o644), (0o002, 0o664), (0o077, 0o600)):
                os.umask(umask)
                path = os.path.join(tmp, f"new_{umask:o}.json")
                output_writer.write_json([_row(sku="1")], path)
                eq(f"a new file under umask {umask:03o} is {want:o}",
                   oct(os.stat(path).st_mode & 0o777), oct(want))
            os.umask(0o022)
            path = os.path.join(tmp, "existing.csv")
            open(path, "w").close()
            os.chmod(path, 0o664)
            output_writer.write_csv([_row(sku="1")], path)
            eq("an existing file keeps its own mode (0664 under umask 022)",
               oct(os.stat(path).st_mode & 0o777), oct(0o664))
            with redirect_stdout(io.StringIO()):
                output_writer.write_run_meta(os.path.join(tmp, "run"), {"a": 1})
            eq("the sidecar is not owner-only either",
               oct(os.stat(os.path.join(tmp, "run.meta.json")).st_mode & 0o777),
               oct(0o644))
    finally:
        os.umask(old)


def check_csv_escape_count_reaches_the_sidecar():
    print("\n[csv escapes are declared]")
    eq("a leading newline is neutralised too",
       output_writer.csv_safe("\nfoo"), "'\nfoo")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "run")
        with redirect_stdout(io.StringIO()):
            finish_run([_row(sku="1", title='=HYPERLINK("x")', description="@me"),
                        _row(sku="2", title="plain")], prefix, "both", False,
                       blocked=False, stop_reason="completed",
                       pages_requested=1, pages_completed=1,
                       start_url="u", final_url="u")
        meta = json.load(open(prefix + ".meta.json", encoding="utf-8"))
        eq("the sidecar counts the escaped CSV cells", meta.get("csv_cells_escaped"), 2)
        rows = json.load(open(prefix + ".json", encoding="utf-8"))
        eq("and the JSON keeps the site's own bytes", rows[0]["title"],
           '=HYPERLINK("x")')
        eq("a run field is never overwritten by the count",
           meta.get("status"), "complete")


def _engine_sources():
    return {name: open(os.path.join(REPO, name + ".py"), encoding="utf-8").read()
            for name in ("playwright_scraper", "puppeteer_scraper", "selenium_scraper")}


def check_one_solve_allowance_per_page():
    """Every paid solve goes through the page's one SolveBudget."""
    print("\n[captcha solve budget]")
    budget = page_flow.SolveBudget()
    eq("the first solve is allowed", budget.spend(), True)
    eq("the second is refused, whatever retry or exit asks for it",
       budget.spend(), False)
    eq("the allowance is one per page", page_flow.SOLVES_PER_PAGE, 1)
    # Read off disk, never behind an import: an import-gated version of this
    # check would skip for exactly the engines whose driver is absent.
    for name, src in _engine_sources().items():
        tree = ast.parse(src)
        funcs = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        handler = funcs.get("handle_captcha_if_present")
        check(f"{name}: has handle_captcha_if_present", handler is not None)
        if handler is None:
            continue
        # The guard must be exactly `if not budget.spend(): return ...`
        # ahead of the solver call — a mention of budget.spend() that does
        # not gate the purchase is not a budget.
        def _is_guard(node):
            t = node.test
            return (isinstance(node, ast.If) and isinstance(t, ast.UnaryOp)
                    and isinstance(t.op, ast.Not) and isinstance(t.operand, ast.Call)
                    and getattr(t.operand.func, "attr", None) == "spend"
                    and getattr(t.operand.func.value, "id", None) == "budget"
                    and any(isinstance(b, ast.Return) for b in node.body))
        guards = [n.lineno for n in ast.walk(handler)
                  if isinstance(n, ast.If) and _is_guard(n)]
        solves = [n.lineno for n in ast.walk(handler) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", None) == "solve"]
        check(f"{name}: the budget is claimed BEFORE the solver is called",
              bool(guards) and bool(solves) and min(guards) < min(solves))
        calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                 and getattr(c.func, "id", getattr(c.func, "attr", None))
                 == "handle_captcha_if_present"]
        check(f"{name}: every call site passes the page's budget "
              f"({len(calls)} call(s))",
              calls and all(len(c.args) >= 3 and getattr(c.args[2], "id", None)
                            == "budget" for c in calls))
        fetch = funcs.get("_fetch_one_page")
        fetch_src = ast.get_source_segment(src, fetch) if fetch else ""
        before_loop = fetch_src.split("for block_attempt")[0]
        check(f"{name}: the budget is created once per page, outside the "
              f"retry/rotation loop",
              "page_flow.SolveBudget()" in before_loop)


def check_status_reaches_the_classifier():
    print("\n[HTTP status reaches the classifier]")
    for name, src in _engine_sources().items():
        tree = ast.parse(src)
        calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Attribute) and c.func.attr == "classify"
                 and getattr(c.func.value, "id", None) == "page_flow"]
        bound = [c for c in calls if any(k.arg == "status_code" for k in c.keywords)
                 or len(c.args) >= 2]
        check(f"{name}: every page_flow.classify call is given a status "
              f"({len(bound)} of {len(calls)})", calls and len(bound) == len(calls))
        if name != "selenium_scraper":
            check(f"{name}: the status is read from what goto() returned",
                  "response.status" in src)
    eq("a refusal status with no listing on the page is blocked",
       page_flow.classify("<html><body>Error</body></html>", status_code=403).state,
       page_flow.BLOCKED)


def check_scraper_api_payload_and_logging():
    print("\n[Scraper API payload]")
    import scraper_api_client as sac

    class Args:
        wait_text = wait_element = None
        wait_state = "load"
        cdp_url = None
        timeout = 60
        key = "k"

    wait_for = sac._build_wait_for(Args())
    eq("waitFor is an object, not a JSON string", wait_for, {"state": "load"})
    sent, records = {}, []

    class Resp:
        status_code = 200
        headers = {"x-debug": "cost=0.0005 key=0123456789abcdef0123456789abcdef"}
        text = ""

        def json(self):
            return {"status": "warn", "http_code": 403, "body": "<html></html>"}

    def fake_post(url, **kw):
        sent.update(kw["json"])
        return Resp()

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Grab()
    sac.logger.addHandler(handler)
    level = sac.logger.level
    sac.logger.setLevel(logging.INFO)
    real = sac.requests.post
    sac.requests.post = fake_post
    try:
        html, upstream = sac.fetch_html(Args(), "https://flippa.com/search")
    finally:
        sac.requests.post = real
        sac.logger.removeHandler(handler)
        sac.logger.setLevel(level)
    check("the payload carries the object form",
          isinstance(sent.get("waitFor"), dict))
    eq("the target's status is read from http_code, not the verdict string",
       upstream, 403)
    logged = " ".join(records)
    check("the x-debug header is logged redacted",
          "x-debug" in logged and "0123456789abcdef0123456789abcdef" not in logged)


def check_credential_scan_uses_relative_paths():
    print("\n[credential scan: relative paths]")
    import importlib.util
    path = os.path.join(REPO, ".github", "ci_checks.py")
    if not os.path.exists(path):
        skip("credential scan paths", "no .github/ci_checks.py in this tree")
        return
    spec = importlib.util.spec_from_file_location("ci_checks_paths", path)
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    import pathlib
    with tempfile.TemporaryDirectory() as tmp:
        # A clone whose ANCESTOR is called like a skipped directory.
        repo = pathlib.Path(tmp) / "legacy" / "clone"
        repo.mkdir(parents=True)
        (repo / "leak.py").write_text("KEY = '" + "ab" * 16 + "'\n")
        ci.REPO = repo
        ci._VENV_CACHE.clear()
        with redirect_stdout(io.StringIO()):
            failures = ci.secret_check()
        check("a key in a clone under a 'legacy' ancestor is still found",
              any("leak.py" in f for f in failures))
        (repo / "leak.py").unlink()
        with redirect_stdout(io.StringIO()):
            failures = ci.secret_check()
        check("a scan that read 0 files fails instead of printing ok",
              any("0 files" in f for f in failures))


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


# The shape of 2Captcha's own reCAPTCHA v2 demo page (2captcha.com/demo/
# recaptcha-v2, 2026-09-28), reduced to what a detector reads: an explicit
# loader and a widget element. The sitekey is a made-up one of the right shape.
_V2_KEY = "6L" + "Xx" * 19
_V2_EXPLICIT = ('<script src="https://www.google.com/recaptcha/api.js?'
                'onload=onRecaptchaLoad&render=explicit"></script>'
                '<div class="g-recaptcha" data-sitekey="' + _V2_KEY + '"></div>'
                '<script>function onRecaptchaLoad(){grecaptcha.render("x")}</script>')


def check_captcha_javascript_actually_runs():
    """The discovery script shipped as ` => {` — no parameter list — so it was
    a SyntaxError in every engine, logged at debug level and never noticed.
    Nothing offline had ever parsed it, let alone run it."""
    print("\n[captcha javascript]")
    js = captcha_solver.CAPTCHA_DISCOVERY_JS.strip()
    check("the discovery script is an arrow function with a parameter list",
          js.startswith("() =>"))
    check("the injection script is an arrow function taking the token",
          captcha_solver.INJECT_TOKEN_FN.strip().startswith("(token) =>"))
    selenium_src = open(os.path.join(REPO, "selenium_scraper.py"), encoding="utf-8").read()
    check("Selenium INVOKES the discovery function rather than returning it",
          "return ({CAPTCHA_DISCOVERY_JS})();" in selenium_src)

    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        skip("the captcha scripts parse as JavaScript", "node not installed")
    else:
        sources = {
            "discovery": f"const f = {captcha_solver.CAPTCHA_DISCOVERY_JS};",
            "injection": f"const f = {captcha_solver.INJECT_TOKEN_FN};",
            "selenium injection body":
                f"function f() {{{captcha_solver.INJECT_TOKEN_BODY}}}",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for label, code in sources.items():
                path = os.path.join(tmp, "s.js")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(code)
                rc = subprocess.run([node, "--check", path], capture_output=True,
                                    text=True, timeout=60).returncode
                check(f"the {label} script parses as JavaScript", rc == 0)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        skip("the discovery script runs in a real page", "playwright not installed")
        return
    # Only a browser that will not START is a skip. An error from the
    # script itself is a FAILURE: the first version of this check caught
    # both, and on the unfixed code reported the SyntaxError it exists to
    # find as "skipped, no browser".
    info, error = None, None
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as e:  # noqa: BLE001 — no browser binary here
                skip("the discovery script runs in a real page",
                     f"no browser: {type(e).__name__}")
                return
            try:
                page = browser.new_page()
                # Offline: every request is refused, so only the markup counts.
                page.route("**/*", lambda route: route.abort())
                page.set_content(_V2_EXPLICIT)
                info = page.evaluate(captcha_solver.CAPTCHA_DISCOVERY_JS)
            except Exception as e:  # noqa: BLE001 — reported as a failure below
                error = f"{type(e).__name__}: {str(e).splitlines()[0][:120]}"
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 — the driver itself is unavailable
        skip("the discovery script runs in a real page",
             f"no playwright driver: {type(e).__name__}")
        return
    if not check("the discovery script runs in a real page without an error"
                 + (f" ({error})" if error else ""), error is None):
        return
    runtime = captcha_solver.challenge_from_discovery(info)
    check("run in a page, it finds the widget", bool(info and info.get("found")))
    eq("and reads the explicit loader as v2", runtime and runtime.kind, "recaptcha_v2")


def check_recaptcha_version_from_markup():
    print("\n[recaptcha version]")
    found = captcha_solver.detect_in_html(_V2_EXPLICIT, "https://example.test/")
    eq("an explicitly rendered widget is v2, not v3 (a v2 task sent as v3 is "
       "ERROR_CAPTCHA_UNSOLVABLE)", found and found.kind, "recaptcha_v2")
    invisible = _V2_EXPLICIT.replace('data-sitekey=', 'data-size="invisible" data-sitekey=')
    found = captcha_solver.detect_in_html(invisible, "https://example.test/")
    eq("with data-size=invisible it is v2 invisible", found and found.kind,
       "recaptcha_v2_invisible")
    v3 = _V2_EXPLICIT.replace("render=explicit", "render=" + _V2_KEY)
    found = captcha_solver.detect_in_html(v3, "https://example.test/")
    eq("a loader rendering the sitekey itself is v3", found and found.kind,
       "recaptcha_v3")


def check_fingerprint_refusal_is_an_api_error():
    """A refused or unreachable Fingerprint API is exit 5 with a reason, not
    a traceback and exit 1. Offline: requests go to a proxy that does not
    exist, and every variable a developer's own .env could set is overridden
    with an empty one (which the loader treats as unset)."""
    print("\n[fingerprint refusal]")
    if ENGINES.get("playwright_scraper") is None:
        skip("fingerprint refusal", "playwright not installed")
        return
    # The run launches Chromium before it asks for a fingerprint, so without
    # a browser binary it dies at launch (exit 1) and this check would be
    # measuring the machine, not the code. CI installs the browser.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser_path = pw.chromium.executable_path
    except Exception as e:  # noqa: BLE001
        browser_path = None
        print(f"        (could not ask Playwright for its browser: {e})")
    if not browser_path or not os.path.exists(browser_path):
        skip("fingerprint refusal", "no Chromium binary for this Playwright "
             "(run `playwright install chromium`)")
        return
    import subprocess
    example = open(os.path.join(REPO, ".env.example"), encoding="utf-8").read()
    url = re.search(r"^[A-Z]+_URL=(https?://\S+)", example, re.M).group(1)
    fake_key = "f" + "a" * 31
    with tempfile.TemporaryDirectory() as tmp:
        # A private HOME: the fingerprint client caches on disk under
        # ~/.cache, and a developer's cached fingerprint would answer this run
        # instead of the (unreachable) API — the run then goes on to the
        # dead proxy and the check measures that. The browser path is kept.
        env = dict(os.environ, HTTPS_PROXY="http://127.0.0.1:9",
                   HTTP_PROXY="http://127.0.0.1:9", HOME=tmp,
                   PLAYWRIGHT_BROWSERS_PATH=os.environ.get(
                       "PLAYWRIGHT_BROWSERS_PATH",
                       os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")),
                   **{name: "" for name in env_config.ENV_KEYS})
        run = subprocess.run(
            [sys.executable, os.path.join(REPO, "playwright_scraper.py"),
             "--url", url, "--pages", "1",
             "--fingerprint", "--twocaptcha-key", fake_key,
             "--out", os.path.join(tmp, "run")],
            cwd=tmp, env=env, capture_output=True, text=True, timeout=120)
    text = run.stdout + run.stderr
    # 5 is the family contract's remote-API code, whichever module names it.
    eq("a failed fingerprint fetch exits 5, the remote-API code", run.returncode, 5)
    check("with the reason and no traceback", "Traceback" not in text
          and "Fingerprint API" in text)
    check("and the key, which rides in this endpoint's query string, is not "
          "printed", fake_key not in text)



def check_family_sync_2026_09_29():
    """Three defects found in this repo by a family-wide pass on 2026-09-29."""
    print("\n[family sync 2026-09-29]")
    import shutil
    import subprocess
    import tempfile
    # 1. The fingerprint init script was syntactically broken JavaScript
    #    ("( => {"), and a syntax error in an init script is SILENT: the
    #    page runs unpatched and --fingerprint did nothing for anyone.
    import fingerprint_client
    node = shutil.which("node")
    if node:
        path = os.path.join(tempfile.mkdtemp(), "fp.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fingerprint_client.playwright_init_script({}))
        r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        check("the fingerprint init script parses as JavaScript"
              + ("" if r.returncode == 0 else f" — {r.stderr.strip()[:160]}"), r.returncode == 0)
    else:
        skip("the fingerprint init script parses as JavaScript", "node not installed; CI has it")
    # 2. `env_config.py --help` read .env: ci_checks runs every CLI's --help.
    r = subprocess.run([sys.executable, "env_config.py", "--help"], cwd=REPO,
                       capture_output=True, text=True)
    check("`env_config.py --help` answers without reading .env",
          r.returncode == 0 and ".env file:" not in r.stdout)
    # 4. The Scraping Browser connect policy: retry a lock or
    #    an outage, never an expired login, and say which it was.
    eq("a 500 on the upgrade is retried", proxy_pool.cdp_retryable(
        "WebSocket error: ws://***:***@cb.2captcha.com:9222/ 500 Internal Server Error"), True)
    eq("a 401 is not", proxy_pool.cdp_retryable("<ws unexpected response> 401 Unauthorized"), False)
    eq("a number that merely CONTAINS 500 is not a 5xx", proxy_pool.cdp_retryable("port 5001 reset"), False)
    check("a 401 is explained as expired credentials",
          "expire" in proxy_pool.cdp_refusal_advice("401 Unauthorized"))
    engine = ENGINES.get("playwright_scraper")
    if engine is None:
        skip("the Playwright engine retries a locked profile", "playwright not installed")
    else:
        calls = []

        class _Flaky:
            def connect_over_cdp(self, endpoint, timeout=None):
                calls.append(1)
                if len(calls) < 3:
                    raise RuntimeError("WebSocket error: ws://***:***@cb.2captcha.com:9222/ 500 Internal Server Error")
                raise _Connected()

        class _Connected(Exception):
            pass

        class _PW:
            chromium = _Flaky()

        class _Args:
            cdp_endpoint = "ws://***:***@cb.2captcha.com:9222"

        real_sleep = engine.time.sleep
        engine.time.sleep = lambda s: None
        try:
            engine._connect_remote(_PW(), _Args())
        except _Connected:
            pass
        except RuntimeError:
            pass
        finally:
            engine.time.sleep = real_sleep
        eq("the Playwright engine tried a locked profile three times", len(calls), 3)
    # 5. A pipe in a workflow step hides the left side's failure unless
    #    pipefail is on, and GitHub runs an unspecified shell without it.
    import re as _re
    wf_dir = os.path.join(REPO, ".github", "workflows")
    piped = 0
    if os.path.isdir(wf_dir):
        for name in sorted(os.listdir(wf_dir)):
            text = open(os.path.join(wf_dir, name), encoding="utf-8").read()
            for step in _re.split(r"\n\s*- (?:name|uses):", text):
                code = "\n".join(ln for ln in step.splitlines()
                                 if ln.strip() and not ln.strip().startswith("#"))
                m = _re.search(r"run:\s*\|?(.*)", code, _re.S)
                if m and _re.search(r"python[^\n]*\|\s*tee", m.group(1)):
                    piped += 1
                    check(f"{name}: a piped python step runs with pipefail",
                          "shell: bash" in code or "pipefail" in code)
        check(f"the pipefail check found the piped steps it guards ({piped})", piped >= 1)
    else:
        skip("workflow pipefail", "no .github/workflows in this tree")
    # 6. The secret scan skips a virtualenv by its marker, not its name —
    #    and still reads an ordinary untracked directory. Planted both ways.
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("ci_checks_fs", os.path.join(REPO, ".github", "ci_checks.py"))
    if spec and os.path.isdir(os.path.join(REPO, ".github")):
        cc = _ilu.module_from_spec(spec)
        spec.loader.exec_module(cc)
        venv = os.path.join(REPO, ".venv-plantedcheck")
        plain = os.path.join(REPO, "plantedcheck-plain")
        key = "b" * 32
        try:
            os.makedirs(os.path.join(venv, "lib"), exist_ok=True)
            open(os.path.join(venv, "pyvenv.cfg"), "w").write("home = /usr\n")
            open(os.path.join(venv, "lib", "vendored.py"), "w").write(f"# {key}\n")
            os.makedirs(plain, exist_ok=True)
            open(os.path.join(plain, "leak.py"), "w").write(f"# {key}\n")
            cc._VENV_CACHE.clear()
            scanned = {str(p) for p in cc.scanned_files()}
            check("the scan skips a virtualenv whatever it is called (pyvenv.cfg)",
                  not any(".venv-plantedcheck" in p for p in scanned))
            check("...and still reads an ordinary untracked directory",
                  any("plantedcheck-plain" in p for p in scanned))
        finally:
            shutil.rmtree(venv, ignore_errors=True)
            shutil.rmtree(plain, ignore_errors=True)
    else:
        skip("secret scan virtualenv rule", "no .github in this tree")
    # 3. .gitignore by SHAPE: a renamed .env, a run directory, a paged dump.
    if shutil.which("git") and os.path.isdir(os.path.join(REPO, ".git")):
        for name, ignored in ((".env.bak", True), (".env.local", True), ("live/dump.html", True),
                              ("out.json.page3", True), ("captures/x.html", True),
                              (".env.example", False), ("sample_output.json", False)):
            r = subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO)
            check(f"{name} is {'ignored' if ignored else 'NOT ignored'}",
                  (r.returncode == 0) == ignored)
    else:
        skip(".gitignore shapes", "not a git checkout")



def check_supply_chain():
    print("\n[supply chain]")
    wf_dir = os.path.join(REPO, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        skip("workflow pins", "no .github/workflows in this tree")
        return
    for name in sorted(os.listdir(wf_dir)):
        text = open(os.path.join(wf_dir, name), encoding="utf-8").read()
        uses = re.findall(r"uses:\s*([^\s#]+)", text)
        loose = [u for u in uses if not re.search(r"@[0-9a-f]{40}$", u)]
        check(f"{name}: every action is pinned by commit SHA"
              + (f" (not: {loose})" if loose else ""), not loose)
        code = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
        installs = re.findall(r"pip install ([^\n]+)", code)
        bad = [i for i in installs if "--require-hashes" not in i or ".lock" not in i]
        check(f"{name}: pip installs only hash-checked locks" + (f" (not: {bad})" if bad else ""),
              not bad)
    for txt, lock in (("requirements.txt", "requirements.lock"),
                      ("requirements-playwright.txt", "requirements-playwright.lock"),
                      ("requirements-puppeteer.txt", "requirements-puppeteer.lock"),
                      ("requirements-selenium.txt", "requirements-selenium.lock")):
        wanted = {re.split(r"[<>=!~\[ ]", line.strip())[0].lower()
                  for line in open(os.path.join(REPO, txt), encoding="utf-8")
                  if line.strip() and not line.startswith("#")}
        if txt != "requirements.txt":
            wanted |= {"requests"}
        pinned = set(re.findall(r"^([a-z0-9_.-]+)==", open(os.path.join(REPO, lock),
                                                           encoding="utf-8").read(), re.M | re.I))
        pinned = {x.lower() for x in pinned}
        missing = wanted - pinned
        check(f"{lock} pins everything {txt} asks for" + (f" (missing {missing})" if missing else ""),
              not missing)
        check(f"{lock} carries hashes", "--hash=sha256:" in open(os.path.join(REPO, lock), encoding="utf-8").read())

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
                  check_banned_wording, check_no_private_citations,
                  check_removed_flags_stay_removed,
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
                  check_writes_are_atomic, check_zero_rows_exit_codes,
                  check_output_file_modes,
                  check_csv_escape_count_reaches_the_sidecar,
                  check_one_solve_allowance_per_page,
                  check_status_reaches_the_classifier,
                  check_scraper_api_payload_and_logging,
                  check_credential_scan_uses_relative_paths,
                  check_cli_refuses_impossible_values,
                  check_diff_refuses_a_window_comparison,
                  check_captcha_javascript_actually_runs,
                  check_recaptcha_version_from_markup,
                  check_fingerprint_refusal_is_an_api_error,
                  check_family_sync_2026_09_29, check_supply_chain):
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
