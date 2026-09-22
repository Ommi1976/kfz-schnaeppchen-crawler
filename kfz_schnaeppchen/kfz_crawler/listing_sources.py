"""Separate the fetched portal from an explicitly evidenced original source.

No redirects are followed and no mobile.de ID is guessed from AutoUncle IDs.
The stored AutoUncle URL remains the user's preferred opening link.
"""
import re
from urllib.parse import urlsplit


def is_autouncle_url(url):
    try:
        parsed = urlsplit(url or "")
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and parsed.hostname in {"autouncle.de", "www.autouncle.de"})
    except (ValueError, TypeError):
        return False


def autouncle_origin(url):
    if not is_autouncle_url(url):
        return None
    # These source slugs were verified against AutoUncle's source-label controls.
    match = re.fullmatch(r"/de/das_wiedersehen/(mobile|mobilebody|mobilepricerating)/\d+/\d+/?",
                         urlsplit(url).path)
    return "mobile.de" if match else None


def normalize_source_label(label):
    if not isinstance(label, str):
        return None
    return {"mobile.de": "mobile.de", "autoscout24": "AutoScout24",
            "autoscout24.de": "AutoScout24", "kleinanzeigen": "Kleinanzeigen",
            "kleinanzeigen.de": "Kleinanzeigen"}.get((label or "").strip().lower())


def listing_origin(portal, url, evidence=None):
    if portal != "AutoUncle":
        return portal
    if not is_autouncle_url(url):
        return None
    origin = autouncle_origin(url)
    if origin:
        return origin
    item = evidence.get("origin_portal", {}) if isinstance(evidence, dict) else {}
    if (isinstance(item, dict) and item.get("source") == "autouncle_source_label"
            and item.get("url") == url):
        return normalize_source_label(item.get("value"))
    return None
