"""Builds the headline console table and a markdown summary file."""
from collections import defaultdict

from .classifier import SHORT_REVIEWS, MIN_WORDS_FOR_THEME_ANALYSIS


def _theme_stats(rows):
    counts = defaultdict(int)
    rating_sum = defaultdict(int)
    for r in rows:
        counts[r["theme"]] += 1
        rating_sum[r["theme"]] += r["rating"]
    return {
        theme: {"count": n, "avg": rating_sum[theme] / n}
        for theme, n in counts.items()
    }


def word_length_split(rows):
    """(short_count, analyzed_count) — short = fewer than
    MIN_WORDS_FOR_THEME_ANALYSIS words (skipped theme matching), analyzed =
    everything else (went through the full theme/sub-theme analysis)."""
    short = sum(1 for r in rows if r["theme"] == SHORT_REVIEWS)
    return short, len(rows) - short


def top_themes(rows, num_themes=10):
    stats = _theme_stats(rows)
    stats.pop(SHORT_REVIEWS, None)
    ranked = sorted(stats.items(), key=lambda x: -x[1]["count"])
    return ranked[:num_themes]


def print_console_summary(rows, num_themes=10, discovery_method="mining"):
    total = len(rows)
    overall_avg = sum(r["rating"] for r in rows) / total if total else 0
    short_count, analyzed_count = word_length_split(rows)
    print(f"\n{total} reviews analyzed | overall avg rating: {overall_avg:.2f}★ "
          f"| theme discovery: {discovery_method}\n")
    print(f"{analyzed_count} reviews had >= {MIN_WORDS_FOR_THEME_ANALYSIS} words and went through "
          f"full theme/sub-theme analysis | {short_count} were shorter and were not themed\n")

    print(f"{'Theme':60s} {'Count':>6s} {'AvgRating':>10s}")
    print("-" * 80)
    for theme, s in top_themes(rows, num_themes=num_themes):
        print(f"{theme:60s} {s['count']:6d} {s['avg']:10.2f}")

    short = _theme_stats(rows).get(SHORT_REVIEWS)
    if short:
        print(f"\n(+ {short['count']} reviews in '{SHORT_REVIEWS}', "
              f"avg {short['avg']:.2f}★ — included in the workbook, not ranked above)")


def write_markdown_summary(rows, output_path, app_label, num_themes=10, discovery_method="mining"):
    total = len(rows)
    overall_avg = sum(r["rating"] for r in rows) / total if total else 0
    short_count, analyzed_count = word_length_split(rows)
    lines = [
        f"# Review Theme Summary — {app_label}",
        "",
        f"- **Reviews analyzed:** {total}",
        f"- **Overall average rating:** {overall_avg:.2f}★",
        f"- **Theme discovery method:** {discovery_method}",
        f"- **Reviews with >= {MIN_WORDS_FOR_THEME_ANALYSIS} words (themed):** {analyzed_count}",
        f"- **Reviews under {MIN_WORDS_FOR_THEME_ANALYSIS} words (not themed, see '{SHORT_REVIEWS}'):** {short_count}",
        "",
        f"## Top {num_themes} Themes",
        "",
        "| Theme | Count | Avg Rating |",
        "|---|---|---|",
    ]
    for theme, s in top_themes(rows, num_themes=num_themes):
        lines.append(f"| {theme} | {s['count']} | {s['avg']:.2f} |")

    lines += ["", "## All Themes & Sub-themes", ""]
    stats = _theme_stats(rows)
    sub_counts = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        acc = sub_counts[r["theme"]][r["sub_theme"]]
        acc[0] += 1
        acc[1] += r["rating"]

    for theme, s in sorted(stats.items(), key=lambda x: -x[1]["count"]):
        lines.append(f"### {theme} (n={s['count']}, avg={s['avg']:.2f})")
        lines.append("")
        lines.append("| Sub-theme | Count | Avg Rating |")
        lines.append("|---|---|---|")
        subs = sorted(sub_counts[theme].items(), key=lambda x: -x[1][0])
        for label, (cnt, rsum) in subs:
            lines.append(f"| {label} | {cnt} | {rsum / cnt:.2f} |")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
