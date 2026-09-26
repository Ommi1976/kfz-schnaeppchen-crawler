"""Erkennt Inserate, die auf dem Portal nicht mehr existieren.

Ein Suchlauf liest je Portal nur einen Ausschnitt der Ergebnisse (Seitenbudget).
"Im Lauf nicht gesehen" heißt deshalb nicht "gelöscht". Solche Inserate werden
einzeln aufgerufen; gemessen am 26.09.2026 an Produktionsdaten:

- AutoScout24: gelöscht -> HTTP 410, vorhanden -> 200
- AutoUncle:   gelöscht -> HTTP 410, vorhanden -> 200 (Weiterleitungsseite)
- Kleinanzeigen: gelöscht -> 200, aber Umleitung von /s-anzeige/ auf eine
  Suchseite (/s-autos/...)

Seitentexte wie "verkauft" oder "gelöscht" taugen nicht: Sie stehen auch in
lebenden Inseraten. mobile.de wird nicht einzeln geprüft (Akamai); dort
erkennt der vollständige Durchgang Verschwundenes, dazu das Sicherheitsnetz.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CHECKABLE_PORTALS = ("AutoScout24", "AutoUncle", "Kleinanzeigen")
GONE_STATUS = {404, 410}
CHECKS_PER_PORTAL = 10          # je Suchlauf und Portal
RECHECK_AFTER = 12 * 3600       # dasselbe Inserat frühestens erneut nach
SAFETY_NET_AGE = 72 * 3600      # nicht gesehen und nicht als vorhanden bestätigt
ALIVE_VALID = 24 * 3600         # so lange schützt eine Bestätigung vor dem Netz

Fetch = Callable[[str], Tuple[int, str]]


def classify(portal: str, requested_url: str, status: int, final_url: str) -> str:
    """'gone', 'alive' oder 'unknown' – im Zweifel nie 'gone'."""
    if status in GONE_STATUS:
        return "gone"
    if status != 200:
        return "unknown"  # Sperre, Serverfehler: sagt nichts über das Inserat
    requested = urlparse(requested_url).path.rstrip("/")
    final = urlparse(final_url).path.rstrip("/")
    if portal == "Kleinanzeigen" and requested.startswith("/s-anzeige/"):
        return "alive" if final.startswith("/s-anzeige/") else "gone"
    # Eine ungemessene Umleitung ist kein Beleg in die eine oder andere Richtung.
    return "alive" if final == requested else "unknown"


def http_fetch(proxy: Optional[str] = None) -> Fetch:
    from curl_cffi import requests

    proxies = {"http": proxy, "https": proxy} if proxy else None

    def fetch(url: str) -> Tuple[int, str]:
        response = requests.get(url, impersonate="chrome", timeout=25, allow_redirects=True,
                                proxies=proxies, headers={"Accept-Language": "de-DE,de;q=0.9"})
        return response.status_code, str(response.url)

    return fetch


def check_unseen(store, search_name: str, seen_before: float, *, proxy: Optional[str] = None,
                 fetch: Optional[Fetch] = None, sleep=time.sleep, now: Optional[float] = None) -> dict:
    """Prüft im Lauf nicht gesehene Inserate und wendet danach das Sicherheitsnetz an."""
    now = time.time() if now is None else now
    counts = {"gone": 0, "alive": 0, "unknown": 0, "stale": 0}
    candidates = store.unseen_candidates(search_name, seen_before, now - RECHECK_AFTER,
                                         CHECKABLE_PORTALS, CHECKS_PER_PORTAL)
    if candidates and fetch is None:
        fetch = http_fetch(proxy)
    for index, row in enumerate(candidates):
        if index:
            sleep(random.uniform(3, 6))
        try:
            status, final_url = fetch(row["url"])
            verdict = classify(row["portal"], row["url"], status, final_url)
        except Exception as exc:
            logger.debug("Verfügbarkeit von %s nicht prüfbar: %s", row["url"], type(exc).__name__)
            verdict = "unknown"
        store.record_availability(row["fingerprint"], verdict, now)
        counts[verdict] += 1
    counts["stale"] = store.apply_unseen_safety_net(search_name, now - SAFETY_NET_AGE,
                                                    now - ALIVE_VALID, SAFETY_NET_AGE, now,
                                                    CHECKABLE_PORTALS)
    if any(counts.values()):
        logger.info("Verfügbarkeit %s: %d entfernt, %d bestätigt, %d unklar, %d per Sicherheitsnetz ausgeblendet",
                    search_name, counts["gone"], counts["alive"], counts["unknown"], counts["stale"])
    return counts
