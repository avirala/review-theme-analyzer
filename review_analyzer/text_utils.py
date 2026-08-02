"""Tokenization, a small built-in sentiment lexicon, and n-gram mining used
by the no-API-key fallback theme discovery in classifier.py.

The lexicon is intentionally biased towards English + common Hinglish/Hindi
transliterations, since this tool's primary use case (Indian fintech/merchant
apps) skews heavily bilingual. It's a reasonable generic default, not a
substitute for real multilingual NLP.
"""
import re
from collections import Counter

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "for", "with", "and", "or", "but", "if",
    "this", "that", "these", "those", "it", "its", "i", "you", "he", "she",
    "we", "they", "my", "your", "his", "her", "our", "their", "me", "him",
    "us", "them", "am", "do", "does", "did", "doing", "have", "has", "had",
    "having", "will", "would", "shall", "should", "can", "could", "may",
    "might", "must", "not", "no", "so", "as", "than", "then", "there",
    "here", "when", "where", "why", "how", "what", "who", "which", "all",
    "any", "some", "just", "very", "too", "also", "even", "only", "app",
    "apps", "application", "please", "one", "also", "get", "got", "getting",
    "use", "using", "used", "yeh", "ye", "hai", "hain", "ho", "hi", "ka",
    "ki", "ke", "ko", "se", "me", "mein", "par", "aur", "bhi", "nahi",
    "nahin", "kar", "karo", "raha", "rahe", "rha", "rhi", "h", "hi",
}

POSITIVE_LEXICON = {
    "nice", "good", "super", "best", "awesome", "badiya", "achha", "aacha",
    "acha", "superb", "excellent", "great", "love", "helpful", "secure",
    "fast", "osm", "thanks", "perfect", "fine", "easy", "amazing",
    "wonderful", "satisfied", "mast", "zabardast", "sahi", "top", "wow",
}

NEGATIVE_LEXICON = {
    "bad", "worst", "bekar", "bekaar", "ghatiya", "faltu", "useless",
    "pathetic", "poor", "disappoint", "disappointing", "horrible", "waste",
    "trash", "kharab", "bakwas", "terrible", "fraud", "scam", "cheat",
    "cheating", "fake", "third", "3rd", "class", "worest", "chutya",
}

_WORD_RE = re.compile(r"[A-Za-zऀ-ॿ]+", re.UNICODE)


def tokenize(text):
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def content_words(text):
    return [w for w in tokenize(text) if w not in STOPWORDS and len(w) > 2]


def is_generic_sentiment_only(phrase_tokens):
    """True if every token in the phrase is a known generic sentiment word —
    those are already covered by the General Positive/Negative catch-all
    buckets, so mining them again as their own "theme" would be redundant."""
    return all(
        t in POSITIVE_LEXICON or t in NEGATIVE_LEXICON for t in phrase_tokens
    )


def extract_ngrams(words, n):
    return [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]


def mine_phrases(texts, top_k=40, min_doc_freq=5, ngram_sizes=(2, 3)):
    """Return [(phrase_str, doc_freq)] sorted by doc_freq desc, for the most
    common multi-word phrases across `texts` (one vote per review, not per
    raw occurrence, so one ranty review can't dominate the ranking)."""
    doc_freq = Counter()
    for text in texts:
        words = content_words(text)
        seen_in_doc = set()
        for n in ngram_sizes:
            for gram in extract_ngrams(words, n):
                if is_generic_sentiment_only(gram):
                    continue
                seen_in_doc.add(gram)
        doc_freq.update(seen_in_doc)

    candidates = [(gram, freq) for gram, freq in doc_freq.items() if freq >= min_doc_freq]
    candidates.sort(key=lambda x: -x[1])

    # de-duplicate near-identical/subsumed phrases (e.g. "not working" vs
    # "app not working") by greedily keeping the higher-ranked one
    accepted = []
    for gram, freq in candidates:
        gram_str = " ".join(gram)
        gram_set = set(gram)
        if any(gram_set <= set(a[0]) or set(a[0]) <= gram_set for a in accepted):
            continue
        accepted.append((gram, freq))
        if len(accepted) >= top_k:
            break

    return [(" ".join(gram), freq) for gram, freq in accepted]


def group_phrases_into_themes(phrases, max_themes=10):
    """Lightweight grouping: phrases sharing a significant token become
    sub-themes of one theme (named after the most frequent phrase in the
    group); ungrouped phrases become single-subtheme themes of their own."""
    groups = []  # list of {"anchor_token": str, "phrases": [(phrase, freq)]}

    for phrase, freq in phrases:
        tokens = set(phrase.split())
        placed = False
        for g in groups:
            if g["tokens"] & tokens:
                g["phrases"].append((phrase, freq))
                g["tokens"] |= tokens
                placed = True
                break
        if not placed:
            groups.append({"tokens": tokens, "phrases": [(phrase, freq)]})

    groups.sort(key=lambda g: -sum(f for _, f in g["phrases"]))
    groups = groups[:max_themes]

    themes = {}
    for g in groups:
        subs = sorted(g["phrases"], key=lambda x: -x[1])
        theme_name = f"Reviews mentioning '{subs[0][0]}'"
        themes[theme_name] = [
            (f"Mentions '{phrase}'", [phrase]) for phrase, _ in subs
        ]
    return themes
