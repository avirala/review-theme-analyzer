import argparse
import logging
import os
import re
import sys
from datetime import datetime

from . import classifier, report, scrapers, workbook
from .urls import parse_app_input

logger = logging.getLogger("review_analyzer")


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "app"


def _detect_store(app_id, url_store, explicit_store):
    if explicit_store:
        return explicit_store
    if url_store:
        return url_store
    return "apple" if str(app_id).lstrip("id").isdigit() else "google"


def _fetch_app_label(app_id, store, country):
    try:
        if store == "google":
            from google_play_scraper import app as gp_app
            return gp_app(app_id, lang="en", country=country)["title"]
        import requests
        numeric_id = str(app_id).lstrip("id")
        if not numeric_id.isdigit():
            return app_id
        resp = requests.get("https://itunes.apple.com/lookup",
                             params={"id": numeric_id, "country": country}, timeout=15)
        results = resp.json().get("results")
        return results[0]["trackName"] if results else app_id
    except Exception:  # noqa: BLE001 — cosmetic only, never fatal
        return app_id


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{s}', expected YYYY-MM-DD") from exc


def build_parser():
    p = argparse.ArgumentParser(
        prog="analyze_reviews",
        description="Scrape app store reviews (Google Play or Apple App Store), "
                    "auto-discover themes/sub-themes, and export a summary + workbook.",
    )
    p.add_argument("--app-id", required=True,
                    help="Play Store / App Store listing URL, a Google Play package name "
                         "(e.g. com.paytm.business), or an Apple App Store numeric ID "
                         "(e.g. 1234567890)")
    p.add_argument("--store", choices=["google", "apple"], default=None,
                    help="Defaults to auto-detect: all-digits -> apple, else google")
    p.add_argument("--country", default="us", help="Store country code (default: us)")
    p.add_argument("--lang", default="en", help="Review language, Google Play only (default: en)")

    volume = p.add_mutually_exclusive_group()
    volume.add_argument("--count", type=int, default=None,
                         help="Number of most-recent reviews to fetch (default 1000 if "
                              "neither --count nor --from/--to given)")
    volume.add_argument("--from", dest="date_from", type=_parse_date, default=None,
                         help="Start date YYYY-MM-DD (use with --to)")
    p.add_argument("--to", dest="date_to", type=_parse_date, default=None,
                   help="End date YYYY-MM-DD (requires --from)")

    p.add_argument("--num-themes", type=int, default=10,
                    help="How many top themes to headline in the console/markdown summary "
                         "(the workbook always includes every theme found)")
    p.add_argument("--output-dir", default="./review_analysis_output")

    llm_group = p.add_mutually_exclusive_group()
    llm_group.add_argument("--use-llm", action="store_true",
                            help="Require LLM-based theme discovery (fails if no API key)")
    llm_group.add_argument("--no-llm", action="store_true",
                            help="Force the offline phrase-mining discovery even if an "
                                 "API key is available")
    p.add_argument("--llm-model", default="claude-sonnet-4-5-20250929")
    p.add_argument("--api-key", default=None,
                    help="Anthropic API key (defaults to $ANTHROPIC_API_KEY)")

    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    if args.date_to and not args.date_from:
        logger.error("--to requires --from")
        return 2
    if args.count is None and args.date_from is None:
        args.count = 1000
        logger.info("Neither --count nor --from/--to given; defaulting to --count 1000")

    app_id, url_store = parse_app_input(args.app_id)
    store = _detect_store(app_id, url_store, args.store)
    logger.info("Resolved store: %s", store)

    try:
        rows = scrapers.fetch_reviews(
            app_id, store, country=args.country, lang=args.lang,
            count=args.count, date_from=args.date_from, date_to=args.date_to,
        )
    except scrapers.ScraperError as exc:
        logger.error("Scraping failed: %s", exc)
        return 1

    if not rows:
        logger.error("No reviews were fetched — check the app id/store/date range.")
        return 1

    logger.info("Fetched %d reviews total", len(rows))

    app_label = _fetch_app_label(app_id, store, args.country)
    slug = _slugify(app_label if app_label != app_id else str(app_id))

    os.makedirs(args.output_dir, exist_ok=True)
    raw_csv_path = os.path.join(args.output_dir, f"{slug}_reviews_raw.csv")
    categorized_csv_path = os.path.join(args.output_dir, f"{slug}_reviews_categorized.csv")
    workbook_path = os.path.join(args.output_dir, f"{slug}_review_themes.xlsx")
    summary_md_path = os.path.join(args.output_dir, f"{slug}_summary.md")

    _write_raw_csv(rows, raw_csv_path)
    logger.info("Saved raw reviews: %s", raw_csv_path)

    use_llm = True if args.use_llm else (False if args.no_llm else "auto")
    try:
        theme_rules, discovery_method = classifier.discover_themes(
            rows, use_llm=use_llm, api_key=args.api_key, model=args.llm_model,
            max_themes=args.num_themes,
        )
    except RuntimeError as exc:
        logger.error("Theme discovery failed: %s", exc)
        return 1

    classifier.classify_reviews(rows, theme_rules)

    _write_categorized_csv(rows, categorized_csv_path)
    logger.info("Saved categorized reviews: %s", categorized_csv_path)

    n_summary_rows, n_detail_rows = workbook.build_workbook(rows, workbook_path)
    logger.info("Saved workbook (%d summary rows, %d detail rows): %s",
                n_summary_rows, n_detail_rows, workbook_path)

    report.write_markdown_summary(rows, summary_md_path, app_label,
                                   num_themes=args.num_themes, discovery_method=discovery_method)
    logger.info("Saved markdown summary: %s", summary_md_path)

    report.print_console_summary(rows, num_themes=args.num_themes, discovery_method=discovery_method)

    return 0


def _write_raw_csv(rows, path):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["review text", "rating", "date", "thumbsup count"])
        for r in rows:
            w.writerow([r["review_text"], r["rating"], r["date"], r["thumbsup_count"]])


def _write_categorized_csv(rows, path):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["review text", "rating", "date", "thumbsup count", "theme", "sub_theme"])
        for r in rows:
            w.writerow([r["review_text"], r["rating"], r["date"], r["thumbsup_count"],
                        r["theme"], r["sub_theme"]])


if __name__ == "__main__":
    sys.exit(main())
