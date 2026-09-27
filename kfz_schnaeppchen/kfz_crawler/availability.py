"""Erkennt Inserate, die auf dem Portal nicht mehr existieren.

Ein Suchlauf liest je Portal nur einen Ausschnitt der Ergebnisse (Seitenbudget).
"Im Lauf nicht gesehen" heißt deshalb nicht "gelöscht". Nach jedem Suchlauf
(ab 1.7.0 nur noch per Knopf) wird deshalb jedes nicht gesehene Inserat einzeln
aufgerufen. Gemessen an Produktionsdaten:

- AutoScout24: gelöscht -> HTTP 410, vorhanden -> 200            (26.09.2026)
- AutoUncle:   gelöscht -> HTTP 410, vorhanden -> 200            (26.09.2026)
- Kleinanzeigen: gelöscht -> 200, aber Umleitung von /s-anzeige/
  auf eine Suchseite (/s-autos/...)                              (26.09.2026)
- mobile.de:   gelöscht -> HTTP 404, vorhanden -> 200 mit Titel  (27.09.2026)

Seitentexte wie "verkauft" oder "gelöscht" taugen nicht: Sie stehen auch in
lebenden Inseraten. mobile.de wird über den eigenen Chrome-Browser geprüft –
mit Startseite, Referer und Pausen, begrenzt durch das Stundenbudget "check".
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Tuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

HTTP_PORTALS = ("AutoScout24", "AutoUncle", "Kleinanzeigen")
BROWSER_PORTALS = ("mobile.de",)
CHECKABLE_PORTALS = HTTP_PORTALS + BROWSER_PORTALS
GONE_STATUS = {404, 410}
CHECKS_PER_PORTAL = 500         # nach einem Klick alle Kandidaten
RECHECK_AFTER = 30 * 60         # Doppelklick prüft nicht alles erneut
SAFETY_NET_AGE = 72 * 3600      # nicht gesehen und nicht als vorhanden bestätigt
ALIVE_VALID = 24 * 3600         # so lange schützt eine Bestätigung vor dem Netz
HTTP_PAUSE = (1.5, 3.0)         # zwischen zwei Abrufen desselben Portals

Fetch = Callable[[str], Tuple[int, str]]


def classify(portal: str, requested_url: str, status: int, final_url: str) -> str:
    """'gone', 'alive' oder 'unknown' – im Zweifel nie 'gone'."""
    if status in GONE_STATUS:
        return "gone"
    if status != 200:
        return "unknown"  # Sperre, Serverfehler: sagt nichts über das Inserat
    requested, final = urlparse(requested_url), urlparse(final_url)
    if portal == "Kleinanzeigen" and requested.path.startswith("/s-anzeige/"):
        return "alive" if final.path.startswith("/s-anzeige/") else "gone"
    if portal == "mobile.de":
        wanted = parse_qs(requested.query).get("id")
        return "alive" if wanted and parse_qs(final.query).get("id") == wanted else "unknown"
    # Eine ungemessene Umleitung ist kein Beleg in die eine oder andere Richtung.
    return "alive" if final.path.rstrip("/") == requested.path.rstrip("/") else "unknown"


def http_fetch(proxy: Optional[str] = None) -> Fetch:
    from curl_cffi import requests

    proxies = {"http": proxy, "https": proxy} if proxy else None

    def fetch(url: str) -> Tuple[int, str]:
        response = requests.get(url, impersonate="chrome", timeout=25, allow_redirects=True,
                                proxies=proxies, headers={"Accept-Language": "de-DE,de;q=0.9"})
        return response.status_code, str(response.url)

    return fetch


def mobile_fetch(store, proxy: Optional[str] = None) -> Fetch:
    from .mobile_runtime import mobile_browser

    def fetch(url: str) -> Tuple[int, str]:
        return mobile_browser().check_listing(url, store=store, proxy=proxy)

    return fetch


def check_unseen(store, search_name: str, seen_before: float, *, proxy: Optional[str] = None,
                 fetch: Optional[Fetch] = None, browser_fetch: Optional[Fetch] = None,
                 sleep=time.sleep, now: Optional[float] = None) -> dict:
    """Prüft alle im Lauf nicht gesehenen Inserate und wendet danach das Sicherheitsnetz an."""
    from .browser import BrowserBlocked
    from .mobile_runtime import MobileDeferred

    now = time.time() if now is None else now
    counts = {"gone": 0, "alive": 0, "unknown": 0, "stale": 0, "deferred": 0}
    lock = threading.Lock()
    candidates = store.unseen_candidates(search_name, seen_before, now - RECHECK_AFTER,
                                         CHECKABLE_PORTALS, CHECKS_PER_PORTAL)
    groups = defaultdict(list)
    for row in candidates:
        groups[row["portal"]].append(row)
    if groups.keys() & set(HTTP_PORTALS) and fetch is None:
        fetch = http_fetch(proxy)
    if groups.keys() & set(BROWSER_PORTALS) and browser_fetch is None:
        browser_fetch = mobile_fetch(store, proxy)

    def run(portal, rows):
        getter = browser_fetch if portal in BROWSER_PORTALS else fetch
        for index, row in enumerate(rows):
            if index and portal in HTTP_PORTALS:
                sleep(random.uniform(*HTTP_PAUSE))  # der Browser drosselt selbst
            try:
                status, final_url = getter(row["url"])
                verdict = classify(portal, row["url"], status, final_url)
            except (BrowserBlocked, MobileDeferred):
                # Sperre oder Budget: Rest beim nächsten Klick, nichts als geprüft markieren.
                with lock:
                    counts["deferred"] += len(rows) - index
                return
            except Exception as exc:
                logger.debug("Verfügbarkeit von %s nicht prüfbar: %s", row["url"], type(exc).__name__)
                verdict = "unknown"
            store.record_availability(row["fingerprint"], verdict, now)
            with lock:
                counts[verdict] += 1

    if groups:
        with ThreadPoolExecutor(max_workers=len(groups)) as pool:
            for future in [pool.submit(run, portal, rows) for portal, rows in groups.items()]:
                future.result()
    counts["stale"] = store.apply_unseen_safety_net(search_name, now - SAFETY_NET_AGE,
                                                    now - ALIVE_VALID, SAFETY_NET_AGE, now,
                                                    CHECKABLE_PORTALS)
    if any(counts.values()):
        logger.info("Verfügbarkeit %s: %d entfernt, %d bestätigt, %d unklar, %d vertagt, "
                    "%d per Sicherheitsnetz ausgeblendet", search_name, counts["gone"], counts["alive"],
                    counts["unknown"], counts["deferred"], counts["stale"])
    return counts
