"""Web front-end for review_analyzer, deployable on Streamlit Community Cloud.

Password-gated (st.secrets["APP_PASSWORD"]). Uses a shared Anthropic API key
(st.secrets["ANTHROPIC_API_KEY"]) for LLM theme discovery, protected by a
soft daily quota (st.secrets.get("DAILY_LLM_QUOTA", 15)) so a shared link
can't run up an unbounded bill — requests past the quota automatically fall
back to the free offline mining discovery instead of failing.
"""
import io
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from review_analyzer import classifier, report, scrapers, workbook
from review_analyzer.urls import parse_app_input

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("review_analyzer.web")

QUOTA_FILE = Path(__file__).parent / ".llm_usage_quota.json"
MAX_REVIEWS = 1000

st.set_page_config(page_title="App Review Theme Analyzer", page_icon="📱", layout="wide")


# ---------------------------------------------------------------- auth ----
def check_password():
    if st.session_state.get("authenticated"):
        return True

    st.title("📱 App Review Theme Analyzer")
    pw = st.text_input("Access password", type="password")
    if st.button("Enter"):
        expected = st.secrets.get("APP_PASSWORD")
        if expected and pw == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# --------------------------------------------------------------- quota ----
def _load_quota():
    if not QUOTA_FILE.exists():
        return {"date": str(date.today()), "llm_runs": 0}
    try:
        data = json.loads(QUOTA_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"date": str(date.today()), "llm_runs": 0}
    if data.get("date") != str(date.today()):
        return {"date": str(date.today()), "llm_runs": 0}
    return data


def _bump_quota():
    data = _load_quota()
    data["llm_runs"] += 1
    QUOTA_FILE.write_text(json.dumps(data))
    return data["llm_runs"]


def llm_quota_available():
    limit = st.secrets.get("DAILY_LLM_QUOTA", 15)
    return _load_quota()["llm_runs"] < limit, limit


# ------------------------------------------------------------- pipeline ---
def run_analysis(app_id, store, country, count, date_from, date_to, allow_llm):
    progress = st.progress(0.0, text="Fetching reviews…")

    rows = scrapers.fetch_reviews(
        app_id, store, country=country, count=count,
        date_from=date_from, date_to=date_to,
    )
    if not rows:
        progress.empty()
        st.error("No reviews were fetched. Check the app ID, store, and date range.")
        return None

    progress.progress(0.4, text=f"Fetched {len(rows)} reviews. Discovering themes…")

    quota_ok, limit = llm_quota_available()
    use_llm = "auto" if (allow_llm and quota_ok) else False
    if allow_llm and not quota_ok:
        st.warning(
            f"Shared daily LLM quota ({limit} analyses) is used up for today — "
            "falling back to the free offline theme discovery for this run. Try again tomorrow "
            "for LLM-quality themes."
        )

    try:
        theme_rules, discovery_method = classifier.discover_themes(rows, use_llm=use_llm)
    except RuntimeError as exc:
        st.warning(f"LLM discovery failed ({exc}); using offline discovery instead.")
        theme_rules, discovery_method = classifier.discover_themes(rows, use_llm=False)

    if discovery_method == "llm":
        _bump_quota()

    progress.progress(0.7, text="Classifying reviews…")
    classifier.classify_reviews(rows, theme_rules)

    progress.progress(0.9, text="Building outputs…")
    xlsx_buffer = io.BytesIO()
    n_summary_rows, n_detail_rows = workbook.build_workbook(rows, xlsx_buffer)
    xlsx_buffer.seek(0)

    progress.progress(1.0, text="Done.")
    progress.empty()

    return {
        "rows": rows,
        "discovery_method": discovery_method,
        "xlsx_bytes": xlsx_buffer.getvalue(),
        "n_summary_rows": n_summary_rows,
    }


# ------------------------------------------------------------------ UI ----
def render_results(result, app_label):
    rows = result["rows"]
    total = len(rows)
    overall_avg = sum(r["rating"] for r in rows) / total

    c1, c2, c3 = st.columns(3)
    c1.metric("Reviews analyzed", total)
    c2.metric("Overall avg rating", f"{overall_avg:.2f}★")
    c3.metric("Theme discovery", result["discovery_method"])

    st.subheader("Top themes")
    top = report.top_themes(rows, num_themes=10)
    df_top = pd.DataFrame(
        [{"Theme": t, "Count": s["count"], "Avg Rating": round(s["avg"], 2)} for t, s in top]
    )
    st.dataframe(df_top, width="stretch", hide_index=True)

    st.subheader("All themes & sub-themes")
    from collections import defaultdict
    sub_counts = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    theme_counts = defaultdict(lambda: [0, 0])
    for r in rows:
        acc = sub_counts[r["theme"]][r["sub_theme"]]
        acc[0] += 1
        acc[1] += r["rating"]
        tc = theme_counts[r["theme"]]
        tc[0] += 1
        tc[1] += r["rating"]

    for theme, (cnt, rsum) in sorted(theme_counts.items(), key=lambda x: -x[1][0]):
        with st.expander(f"{theme} — n={cnt}, avg={rsum / cnt:.2f}★"):
            subs = sorted(sub_counts[theme].items(), key=lambda x: -x[1][0])
            df_sub = pd.DataFrame(
                [{"Sub-theme": label, "Count": c, "Avg Rating": round(s / c, 2)}
                 for label, (c, s) in subs]
            )
            st.dataframe(df_sub, width="stretch", hide_index=True)

    st.subheader("Browse individual reviews")
    theme_options = ["All themes"] + sorted(theme_counts.keys(), key=lambda t: -theme_counts[t][0])
    f1, f2, f3 = st.columns([1, 1, 2])
    selected_theme = f1.selectbox("Theme", theme_options)

    if selected_theme == "All themes":
        sub_options = ["All sub-themes"]
    else:
        sub_options = ["All sub-themes"] + sorted(
            sub_counts[selected_theme].keys(),
            key=lambda s: -sub_counts[selected_theme][s][0],
        )
    selected_sub = f2.selectbox("Sub-theme", sub_options)
    search_text = f3.text_input("Search review text", placeholder="e.g. soundbox, refund…")

    filtered = rows
    if selected_theme != "All themes":
        filtered = [r for r in filtered if r["theme"] == selected_theme]
    if selected_sub != "All sub-themes":
        filtered = [r for r in filtered if r["sub_theme"] == selected_sub]
    if search_text.strip():
        needle = search_text.strip().lower()
        filtered = [r for r in filtered if needle in (r["review_text"] or "").lower()]

    filtered_sorted = sorted(filtered, key=lambda r: r["date"] or "", reverse=True)
    st.caption(f"Showing {len(filtered_sorted)} of {total} reviews")
    df_reviews = pd.DataFrame([
        {
            "Review": r["review_text"],
            "Rating": r["rating"],
            "Theme": r["theme"],
            "Sub-theme": r["sub_theme"],
            "Date": str(r["date"]) if r["date"] else "",
            "Thumbs up": r["thumbsup_count"],
        }
        for r in filtered_sorted
    ])
    st.dataframe(df_reviews, width="stretch", hide_index=True, height=400)

    st.subheader("Downloads")
    slug = app_label.lower().replace(" ", "_")
    raw_csv = _rows_to_csv(rows, categorized=False)
    cat_csv = _rows_to_csv(rows, categorized=True)

    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇ Raw reviews CSV", raw_csv, file_name=f"{slug}_reviews_raw.csv",
                        mime="text/csv")
    d2.download_button("⬇ Categorized CSV", cat_csv, file_name=f"{slug}_reviews_categorized.csv",
                        mime="text/csv")
    d3.download_button("⬇ Theme workbook (.xlsx)", result["xlsx_bytes"],
                        file_name=f"{slug}_review_themes.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _rows_to_csv(rows, categorized):
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    if categorized:
        w.writerow(["review text", "rating", "date", "thumbsup count", "theme", "sub_theme"])
        for r in rows:
            w.writerow([r["review_text"], r["rating"], r["date"], r["thumbsup_count"],
                        r["theme"], r["sub_theme"]])
    else:
        w.writerow(["review text", "rating", "date", "thumbsup count"])
        for r in rows:
            w.writerow([r["review_text"], r["rating"], r["date"], r["thumbsup_count"]])
    return buf.getvalue().encode("utf-8")


def main():
    if not check_password():
        return

    st.title("📱 App Review Theme Analyzer")
    st.caption("Scrape Google Play / Apple App Store reviews and auto-group them into "
               "themes and sub-themes.")

    with st.form("analyze_form"):
        col1, col2 = st.columns(2)
        with col1:
            app_url = st.text_input(
                "App Store URL",
                placeholder="https://play.google.com/store/apps/details?id=com.paytm.business",
                help="Paste the app's Google Play or Apple App Store listing URL. "
                     "A raw package name or numeric App Store ID also still works.",
            )
            store = st.selectbox("Store", ["Auto-detect", "Google Play", "Apple App Store"])
        with col2:
            count = st.number_input("Number of most-recent reviews", min_value=10,
                                     max_value=MAX_REVIEWS, value=1000, step=10)
            allow_llm = st.checkbox(
                "Use LLM-based theme discovery (shared quota)", value=True,
                help="Falls back automatically to free offline discovery if today's shared "
                     "quota is used up.",
            )

        submitted = st.form_submit_button("Analyze", type="primary")

    # Widgets inside render_results() (the theme/sub-theme filters, search box)
    # live outside this form, so selecting them triggers a full script rerun
    # with `submitted` back to False. Without persisting the result in
    # session_state, that rerun would fall straight through and show the
    # blank form again instead of the (still valid) results.
    if submitted:
        if not app_url.strip():
            st.error("Enter an App Store URL (or app id) first.")
        else:
            app_id, url_store = parse_app_input(app_url)

            store_map = {"Auto-detect": None, "Google Play": "google", "Apple App Store": "apple"}
            resolved_store = store_map[store]
            if resolved_store is None:
                resolved_store = url_store or (
                    "apple" if app_id.lstrip("id").isdigit() else "google"
                )

            with st.spinner("Running analysis…"):
                result = run_analysis(app_id, resolved_store, "us",
                                       count, None, None, allow_llm)

            if result:
                st.session_state["result"] = result
                st.session_state["app_label"] = app_id

    if "result" in st.session_state:
        render_results(st.session_state["result"], st.session_state["app_label"])


if __name__ == "__main__":
    main()
