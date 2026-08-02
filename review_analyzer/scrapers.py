"""Store-agnostic review fetching for Google Play and the Apple App Store.

Both scrapers return a list of plain dicts with the same shape:
    {"review_text": str, "rating": int, "date": datetime, "thumbsup_count": int}

so everything downstream (classification, workbook export) never has to
know which store the data came from.
"""
import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("review_analyzer.scrapers")

APPLE_MAX_PAGES = 10  # Apple's public RSS review feed only exposes ~10 pages
APPLE_PAGE_SIZE = 50  # of ~50 reviews each (most-recent ~500 reviews, hard cap)


class ScraperError(RuntimeError):
    """Raised for unrecoverable scraping failures (bad app id, network, etc)."""


def _retry(fn, attempts=3, backoff=1.5, what="request"):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (requests.RequestException, ConnectionError) as exc:
            last_exc = exc
            wait = backoff ** attempt
            logger.warning("%s failed (attempt %d/%d): %s — retrying in %.1fs",
                            what, attempt, attempts, exc, wait)
            time.sleep(wait)
    raise ScraperError(f"{what} failed after {attempts} attempts: {last_exc}")


def fetch_google_reviews(app_id, country="us", lang="en", count=None,
                          date_from=None, date_to=None, sleep=0.2, page_size=200):
    """Fetch Google Play reviews, newest first, via google-play-scraper.

    Either `count` (int) or a `date_from`/`date_to` window (datetime, naive
    UTC) should be supplied by the caller; this function just keeps pulling
    pages until whichever stop condition is met.
    """
    try:
        from google_play_scraper import reviews, Sort
    except ImportError as exc:
        raise ScraperError(
            "google-play-scraper is not installed. Run: pip install google-play-scraper"
        ) from exc

    collected = []
    continuation_token = None
    date_mode = date_from is not None or date_to is not None

    while True:
        batch_size = page_size if date_mode else min(page_size, max(1, count - len(collected)))

        def _do_fetch(bt=batch_size, ct=continuation_token):
            return reviews(
                app_id, lang=lang, country=country, sort=Sort.NEWEST,
                count=bt, continuation_token=ct,
            )

        result, continuation_token = _retry(_do_fetch, what=f"Google Play fetch ({app_id})")

        if not result:
            break

        for r in result:
            collected.append({
                "review_text": r.get("content") or "",
                "rating": int(r.get("score") or 0),
                "date": r.get("at"),
                "thumbsup_count": int(r.get("thumbsUpCount") or 0),
            })

        oldest = collected[-1]["date"]
        logger.info("Google Play: fetched %d reviews so far (oldest so far: %s)",
                    len(collected), oldest)

        if date_mode:
            if date_from and oldest and oldest < date_from:
                break
        elif count is not None and len(collected) >= count:
            break

        if continuation_token is None:
            break

        time.sleep(sleep)

    if date_mode:
        collected = [
            r for r in collected
            if r["date"] is not None
            and (date_from is None or r["date"] >= date_from)
            and (date_to is None or r["date"] <= date_to)
        ]
    elif count is not None:
        collected = collected[:count]

    return collected


def _resolve_apple_app_id(app_identifier, country="us"):
    """Accept either a numeric App Store ID or a search term / bundle id and
    resolve it to a numeric trackId via the public iTunes lookup/search API."""
    app_identifier = str(app_identifier).lstrip("id")
    if app_identifier.isdigit():
        return int(app_identifier)

    def _do_lookup():
        return requests.get(
            "https://itunes.apple.com/lookup",
            params={"bundleId": app_identifier, "country": country},
            timeout=15,
        )

    resp = _retry(_do_lookup, what=f"Apple bundleId lookup ({app_identifier})")
    data = resp.json()
    if data.get("resultCount"):
        return data["results"][0]["trackId"]

    raise ScraperError(
        f"Could not resolve Apple app id '{app_identifier}'. "
        "Pass the numeric App Store ID (the digits after 'id' in the store URL)."
    )


def fetch_apple_reviews(app_id, country="us", count=None, date_from=None, date_to=None,
                         sleep=0.5):
    """Fetch App Store reviews via Apple's public customer-reviews RSS feed.

    This feed is unauthenticated and stable, but Apple caps it at roughly the
    500 most recent reviews (10 pages x 50). If more history is requested,
    we return everything available and the caller is warned.
    """
    numeric_id = _resolve_apple_app_id(app_id, country=country)
    collected = []
    date_mode = date_from is not None or date_to is not None

    for page in range(1, APPLE_MAX_PAGES + 1):
        url = (
            f"https://itunes.apple.com/{country}/rss/customerreviews/"
            f"page={page}/id={numeric_id}/sortby=mostrecent/json"
        )

        def _do_fetch(u=url):
            return requests.get(u, timeout=20, headers={"User-Agent": "Mozilla/5.0"})

        resp = _retry(_do_fetch, what=f"Apple RSS page {page} ({numeric_id})")
        if resp.status_code != 200:
            logger.warning("Apple RSS page %d returned HTTP %d — stopping", page, resp.status_code)
            break

        entries = resp.json().get("feed", {}).get("entry", [])
        reviews_on_page = [e for e in entries if "im:rating" in e]

        if not reviews_on_page:
            break

        for e in reviews_on_page:
            date_str = e.get("updated", {}).get("label")
            try:
                dt = datetime.fromisoformat(date_str).astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError, TypeError):
                dt = None
            collected.append({
                "review_text": e.get("content", {}).get("label", ""),
                "rating": int(e.get("im:rating", {}).get("label", 0)),
                "date": dt,
                "thumbsup_count": int(e.get("im:voteCount", {}).get("label", 0)),
            })

        logger.info("Apple App Store: fetched %d reviews so far (page %d)", len(collected), page)

        oldest = collected[-1]["date"]
        if date_mode and date_from and oldest and oldest < date_from:
            break
        if not date_mode and count is not None and len(collected) >= count:
            break

        time.sleep(sleep)
    else:
        logger.warning(
            "Reached Apple's ~%d review cap (page limit). Older reviews are not "
            "accessible through the public feed.", APPLE_MAX_PAGES * APPLE_PAGE_SIZE
        )

    if date_mode:
        collected = [
            r for r in collected
            if r["date"] is not None
            and (date_from is None or r["date"] >= date_from)
            and (date_to is None or r["date"] <= date_to)
        ]
    elif count is not None:
        collected = collected[:count]

    return collected


def fetch_reviews(app_id, store, country="us", lang="en", count=None,
                   date_from=None, date_to=None):
    if store == "google":
        return fetch_google_reviews(app_id, country=country, lang=lang, count=count,
                                     date_from=date_from, date_to=date_to)
    if store == "apple":
        return fetch_apple_reviews(app_id, country=country, count=count,
                                    date_from=date_from, date_to=date_to)
    raise ScraperError(f"Unknown store '{store}' (expected 'google' or 'apple')")
