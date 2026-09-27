"""Holt zu AutoUncle-Treffern mit Herkunft mobile.de das echte mobile.de-Inserat.

mobile.de hat die bessere Datenlage (Beschreibung, Garantie, SoH), die direkte
mobile.de-Suche liest je Klick aber nur wenige Seiten. AutoUncles Weiterleitung
(/de/das_wiedersehen/mobile/...) enthält die mobile.de-Adresse – gemessen
27.09.2026 in 4 von 4 Fällen, z. B. .../auto-inserat/<slug>/45180573055520.html.

Das mobile.de-Inserat wird über den Chrome-Browser geladen (Detail-Budget) und
als eigene mobile.de-Zeile gespeichert. Preis, Kilometer und Baujahr stammen aus
dem AutoUncle-Treffer – es ist nachweislich dasselbe Inserat. Die Zuordnung zum
selben Fahrzeug wird direkt angelegt; die Trefferliste zeigt dann die
mobile.de-Zeile statt der AutoUncle-Zeile.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

from bs4 import BeautifulSoup

from .models import Listing

logger = logging.getLogger(__name__)

STATE_PREFIX = "autouncle.mobile.v1."
RETRY_UNRESOLVED = 7 * 24 * 3600   # Weiterleitung ohne mobile.de-Adresse
_MOBILE_ID = re.compile(r"suchen\.mobile\.de(?:\\?/)+auto-inserat(?:\\?/)+[^\"'\s<>]*?(?:\\?/)(\d{6,})\.html")


def mobile_id_from_redirect(html: str) -> Optional[str]:
    """mobile.de-Inseratsnummer aus AutoUncles Weiterleitungsseite."""
    ids = set(_MOBILE_ID.findall(html or ""))
    return ids.pop() if len(ids) == 1 else None  # mehrdeutig -> lieber nichts


def _http_text(proxy: Optional[str]) -> Callable[[str], str]:
    from curl_cffi import requests
    proxies = {"http": proxy, "https": proxy} if proxy else None

    def fetch(url: str) -> str:
        r = requests.get(url, impersonate="chrome", timeout=25, allow_redirects=False,
                         proxies=proxies, headers={"Accept-Language": "de-DE,de;q=0.9"})
        return r.text if r.status_code == 200 else ""

    return fetch


def _mobile_detail(store, proxy):
    from .mobile_runtime import mobile_browser

    def fetch(url: str) -> str:
        return mobile_browser().fetch(url, store=store, proxy=proxy, kind="detail")

    return fetch


def bridge(store, search_name: str, *, proxy: Optional[str] = None, redirect_fetch=None,
           detail_fetch=None, now: Optional[float] = None, limit: int = 20) -> dict:
    from .battery_analyzer import mobile_detail_snapshot, parse_mobile_de_detail_html
    from .browser import BrowserBlocked
    from .mobile_runtime import MobileDeferred, load_state, save_state
    from .portals.mobile_de import MobileDe

    now = time.time() if now is None else now
    counts = {"übernommen": 0, "schon vorhanden": 0, "ohne mobile.de-Adresse": 0, "vertagt": 0, "Fehler": 0}
    rows = store.autouncle_mobile_candidates(search_name)
    redirect_fetch = redirect_fetch or _http_text(proxy)
    detail_fetch = detail_fetch or _mobile_detail(store, proxy)
    attempts = 0
    for row in rows:
        key = STATE_PREFIX + row["fingerprint"]
        state = load_state(store, key)
        mobile_id = state.get("mobile_id")
        if not mobile_id:
            if "checked_at" in state and state["checked_at"] > now - RETRY_UNRESOLVED:
                continue
            try:
                mobile_id = mobile_id_from_redirect(redirect_fetch(row["url"]))
            except Exception:
                mobile_id = None
            save_state(store, key, {"mobile_id": mobile_id, "checked_at": now})
            if not mobile_id:
                counts["ohne mobile.de-Adresse"] += 1
                continue
        url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={mobile_id}"
        listing = Listing(portal="mobile.de", title=row["title"], url=url, price=row["price"],
                          year=row["year"], mileage=row["mileage"], fuel=row["fuel"],
                          power_ps=row["power_ps"], location=row["location"], raw_id=mobile_id)
        if store.known_urls([listing.fingerprint]):
            store.link_same_vehicle(row["fingerprint"], listing.fingerprint, "AutoUncle-Weiterleitung")
            counts["schon vorhanden"] += 1
            continue
        if attempts >= limit:
            counts["vertagt"] += 1
            continue
        attempts += 1
        cache_key = f"mobile.detail.v1.{mobile_id}"
        cached = load_state(store, cache_key)
        try:
            html = cached.get("html") if cached.get("until", 0) > now else None
            title = cached.get("title") if html else None
            if not html:
                raw = detail_fetch(url)
                # Die Kurzfassung für den Cache enthält keine Überschrift.
                h1 = BeautifulSoup(raw or "", "lxml").select_one("h1")
                title = h1.get_text(" ", strip=True) if h1 else None
                html = mobile_detail_snapshot(raw)
                save_state(store, cache_key, {"checked_at": now, "until": now + MobileDe.DETAIL_CACHE,
                                              "html": html, "title": title})
        except (MobileDeferred, BrowserBlocked):
            counts["vertagt"] += len(rows) - rows.index(row)
            break
        except Exception:
            logger.debug("mobile.de-Inserat %s nicht ladbar", mobile_id, exc_info=True)
            counts["Fehler"] += 1
            continue
        if title:
            listing.title = title
        parse_mobile_de_detail_html(html, listing)
        listing.field_evidence["acquired_via"] = {
            "value": "AutoUncle-Weiterleitung", "source": "autouncle_redirect",
            "confidence": 1.0, "url": row["url"]}
        store.record_listings(search_name, [listing])
        store.link_same_vehicle(row["fingerprint"], listing.fingerprint, "AutoUncle-Weiterleitung")
        counts["übernommen"] += 1
    if rows:
        logger.info("mobile.de über AutoUncle %s: %s", search_name,
                    ", ".join(f"{v} {k}" for k, v in counts.items()))
    return counts
