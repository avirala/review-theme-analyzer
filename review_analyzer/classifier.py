"""Theme/sub-theme discovery and classification.

Two discovery backends, same output shape — an ordered dict of:
    {theme_name: [(sub_theme_label, [keyword, ...]), ...]}

- discover_themes_by_mining: no external dependencies, works offline, always
  available. Mines frequent multi-word phrases out of the review text itself.
- discover_themes_by_llm: one Claude call over a stratified sample of reviews
  to produce human-quality theme/sub-theme/keyword definitions, the same way
  they were hand-built in the manual analysis this tool is based on. Used
  automatically when ANTHROPIC_API_KEY is set (and the `anthropic` package is
  installed), unless explicitly disabled.

Either way, classify_reviews() applies the resulting rules only to reviews
with at least MIN_WORDS_FOR_THEME_ANALYSIS words. Shorter reviews ("good",
"ok", a single emoji) carry too little signal to theme meaningfully, so they
skip theme matching entirely and go straight into their own dedicated
SHORT_REVIEWS theme — kept separate specifically so it's easy to see, at a
glance, how many reviews were long enough to analyze vs. not.

Every review with >= MIN_WORDS_FOR_THEME_ANALYSIS words is guaranteed to land
in either a discovered theme or General Positive/Negative Feedback (by
rating) — never in a vague leftover bucket.
"""
import json
import logging
import os

from . import text_utils

logger = logging.getLogger("review_analyzer.classifier")

GENERAL_POSITIVE = "General Positive Feedback"
GENERAL_NEGATIVE = "General Negative Feedback (No Specific Issue Cited)"
SHORT_REVIEWS = "Short Reviews (Under 5 Words)"

# Reviews with fewer words than this skip theme/sub-theme analysis entirely
# and go straight into the SHORT_REVIEWS theme. Reviews at or above it always
# get a real theme — a discovered one, or General Positive/Negative Feedback.
MIN_WORDS_FOR_THEME_ANALYSIS = 5

_POSITIVE_SUBTHEMES = [
    ("Unspecified generic praise (no reason given)", list(text_utils.POSITIVE_LEXICON)),
]
_NEGATIVE_SUBTHEMES = [
    ("Blanket dissatisfaction statement, no reason given", list(text_utils.NEGATIVE_LEXICON)),
]


def discover_themes_by_mining(rows, max_themes=10, min_doc_freq=None):
    """Offline fallback: mine frequent complaint/praise phrases directly out
    of the review text via document-frequency n-gram counting."""
    negative_texts = [r["review_text"] for r in rows if r["rating"] <= 3]
    positive_texts = [r["review_text"] for r in rows if r["rating"] >= 4]

    if min_doc_freq is None:
        min_doc_freq = max(5, int(0.006 * max(len(rows), 1)))

    neg_phrases = text_utils.mine_phrases(negative_texts, top_k=60, min_doc_freq=min_doc_freq)
    pos_phrases = text_utils.mine_phrases(positive_texts, top_k=20, min_doc_freq=min_doc_freq)

    themes = {}
    themes.update(text_utils.group_phrases_into_themes(neg_phrases, max_themes=max_themes))
    themes.update(text_utils.group_phrases_into_themes(pos_phrases, max_themes=3))

    logger.info("Mining discovered %d candidate themes from %d negative-review phrases "
                "and %d positive-review phrases", len(themes), len(neg_phrases), len(pos_phrases))
    return themes


_LLM_SYSTEM_PROMPT = """You analyze app store reviews for a mobile app and identify the \
real, specific complaint/praise themes present — not generic sentiment. For each theme, \
give 2-4 sub-themes, each with a short human-readable label and a list of literal \
lowercase keywords/phrases (English and any other language present, e.g. Hindi \
transliterations) that would appear verbatim in a review belonging to that sub-theme. \
Keywords must be substrings you'd expect to literally find in review text via simple \
substring matching — not abstract concepts. Prefer specific, actionable themes (e.g. \
"Soundbox device stops working" not "product quality"). Return 8-12 themes total, \
ordered by how frequently you'd expect them in the sample, each with 2-4 sub-themes."""

_LLM_TOOL_SCHEMA = {
    "name": "report_themes",
    "description": "Report the discovered review themes and sub-themes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "themes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "theme_name": {"type": "string"},
                        "sub_themes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "keywords": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["label", "keywords"],
                            },
                        },
                    },
                    "required": ["theme_name", "sub_themes"],
                },
            },
        },
        "required": ["themes"],
    },
}


def _stratified_sample(rows, sample_size=180, seed_rows=None):
    by_rating = {r: [] for r in range(1, 6)}
    for row in rows:
        rating = max(1, min(5, row["rating"]))
        by_rating[rating].append(row)

    weights = {1: 0.35, 2: 0.1, 3: 0.15, 4: 0.15, 5: 0.25}
    sample = []
    for rating, weight in weights.items():
        bucket = by_rating[rating]
        take = min(len(bucket), max(1, int(sample_size * weight))) if bucket else 0
        step = max(1, len(bucket) // take) if take else 1
        sample.extend(bucket[::step][:take])
    return sample


def discover_themes_by_llm(rows, api_key, model="claude-sonnet-4-5-20250929", sample_size=180):
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' package is not installed. Run: pip install anthropic"
        ) from exc

    sample = _stratified_sample(rows, sample_size=sample_size)
    lines = [f"[{r['rating']}★] {r['review_text'][:280]}" for r in sample if r["review_text"].strip()]
    user_content = (
        f"Here are {len(lines)} sampled app store reviews (rating shown as N★):\n\n"
        + "\n".join(lines)
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=_LLM_SYSTEM_PROMPT,
        tools=[_LLM_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "report_themes"},
        messages=[{"role": "user", "content": user_content}],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("LLM response did not include the expected tool call")

    payload = tool_use.input
    themes = {}
    for t in payload.get("themes", []):
        name = t["theme_name"].strip()
        subs = [
            (s["label"].strip(), [kw.lower() for kw in s.get("keywords", [])])
            for s in t.get("sub_themes", [])
            if s.get("keywords")
        ]
        if subs:
            themes[name] = subs

    logger.info("LLM discovered %d themes from a %d-review sample", len(themes), len(sample))
    return themes


def discover_themes(rows, use_llm="auto", api_key=None, model="claude-sonnet-4-5-20250929",
                     max_themes=10):
    """use_llm: "auto" (use LLM iff a key is available), True (require LLM,
    raise on failure), False (never use LLM)."""
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    if use_llm is False:
        return discover_themes_by_mining(rows, max_themes=max_themes), "mining"

    if use_llm is True and not api_key:
        raise RuntimeError("LLM theme discovery requested but no ANTHROPIC_API_KEY is set")

    if api_key:
        try:
            return discover_themes_by_llm(rows, api_key=api_key, model=model), "llm"
        except Exception as exc:  # noqa: BLE001 — any LLM failure falls back
            if use_llm is True:
                raise
            logger.warning("LLM theme discovery failed (%s) — falling back to mining", exc)

    return discover_themes_by_mining(rows, max_themes=max_themes), "mining"


def _classify_short(rating):
    return ("Short positive mention (rating 4-5)" if rating >= 4
            else "Short negative/neutral mention (rating 1-3)")


def _first_matching_subtheme(text_lower, subthemes):
    for label, keywords in subthemes:
        for kw in keywords:
            if kw and kw in text_lower:
                return label
    return None


def classify_reviews(rows, theme_rules):
    """Mutates each row in `rows`, adding "theme" and "sub_theme" keys.

    Reviews with fewer than MIN_WORDS_FOR_THEME_ANALYSIS words skip theme
    matching entirely and go straight into SHORT_REVIEWS. Everything else
    is guaranteed a real theme: discovered theme_rules first (priority
    order, as given), falling back to General Positive/Negative Feedback by
    rating — never a vague leftover bucket.
    """
    ordered_themes = list(theme_rules.items())

    for row in rows:
        text = row["review_text"] or ""
        text_lower = text.lower()
        word_count = len(text.split())

        if word_count < MIN_WORDS_FOR_THEME_ANALYSIS:
            row["theme"] = SHORT_REVIEWS
            row["sub_theme"] = _classify_short(row["rating"])
            continue

        matched_theme, matched_sub = None, None

        for theme_name, subthemes in ordered_themes:
            sub = _first_matching_subtheme(text_lower, subthemes)
            if sub:
                matched_theme, matched_sub = theme_name, sub
                break

        if matched_theme is None:
            if row["rating"] >= 4:
                sub = _first_matching_subtheme(text_lower, _POSITIVE_SUBTHEMES)
                matched_theme = GENERAL_POSITIVE
                matched_sub = sub or "Unspecified generic praise (no reason given)"
            else:
                sub = _first_matching_subtheme(text_lower, _NEGATIVE_SUBTHEMES)
                matched_theme = GENERAL_NEGATIVE
                matched_sub = sub or "Blanket dissatisfaction statement, no reason given"

        row["theme"] = matched_theme
        row["sub_theme"] = matched_sub

    return rows
