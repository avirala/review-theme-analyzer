"""Parses a pasted Play Store / App Store URL (or a raw app id, for backward
compatibility) into (app_id, store)."""
import re
from urllib.parse import urlparse, parse_qs

GOOGLE_PLAY_DOMAINS = ("play.google.com",)
APPLE_DOMAINS = ("apps.apple.com", "itunes.apple.com")


def parse_app_input(text):
    """Returns (app_id, store) where store is 'google', 'apple', or None if
    it couldn't be determined (caller should fall back to auto-detection)."""
    text = (text or "").strip()

    if any(d in text for d in GOOGLE_PLAY_DOMAINS):
        query = parse_qs(urlparse(text).query)
        app_id = query.get("id", [None])[0]
        if app_id:
            return app_id, "google"

    if any(d in text for d in APPLE_DOMAINS):
        match = re.search(r"id(\d+)", text)
        if match:
            return match.group(1), "apple"

    # Not a recognized store URL — treat as a raw app id/package name
    # (old behavior), letting the caller auto-detect the store.
    return text, None
