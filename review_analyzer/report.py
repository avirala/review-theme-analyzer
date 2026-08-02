"""Builds the headline console table and a markdown summary file."""
from collections import defaultdict


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


def top_themes(rows, num_themes=10):
    stats = _theme_stats(rows)
    stats.pop("Other / Uncategorized", None)
    ranked = sorted(stats.items(), key=lambda x: -x[1]["count"])
    return ranked[:num_themes]


def print_console_summary(rows, num_themes=10, discovery_method="mining"):
    total = len(rows)
    overall_avg = sum(r["rating"] for r in rows) / total if total else 0
    print(f"\n{total} reviews analyzed | overall avg rating: {overall_avg:.2f}★ "
          f"| theme discovery: {discovery_method}\n")

    print(f"{'Theme':60s} {'Count':>6s} {'AvgRating':>10s}")
    print("-" * 80)
    for theme, s in top_themes(rows, num_themes=num_themes):
        print(f"{theme:60s} {s['count']:6d} {s['avg']:10.2f}")

    other = _theme_stats(rows).get("Other / Uncategorized")
    if other:
        print(f"\n(+ {other['count']} reviews in 'Other / Uncategorized', "
              f"avg {other['avg']:.2f}★ — included in the workbook, not ranked above)")


def write_markdown_summary(rows, output_path, app_label, num_themes=10, discovery_method="mining"):
    total = len(rows)
    overall_avg = sum(r["rating"] for r in rows) / total if total else 0
    lines = [
        f"# Review Theme Summary — {app_label}",
        "",
        f"- **Reviews analyzed:** {total}",
        f"- **Overall average rating:** {overall_avg:.2f}★",
        f"- **Theme discovery method:** {discovery_method}",
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
