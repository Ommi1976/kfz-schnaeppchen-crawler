"""mobile.de-Scraper (browserlos via curl_cffi + importierte Session-Cookies).

mobile.de ist durch Akamai Bot Manager geschützt. Weder reine requests noch
ein (auch headless) Browser kommen an echte Daten – Akamai weist automatisierte
Browser ab. Funktionierender Weg OHNE Browser im Crawler: die Session-Cookies
aus einem echten, eingeloggten Browser importieren und mit curl_cffi (imitiert
den Chrome-TLS-Fingerprint) wiederverwenden. Laufen die Cookies ab, meldet der
Scraper CookiesExpired und die Oberfläche fordert zum Aktualisieren auf.
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery
from .base import BasePortal, CookiesExpired, PortalError

FUEL_MAP = {"benzin": "PETROL", "diesel": "DIESEL", "elektro": "ELECTRICITY", "hybrid": "HYBRID"}


class MobileDe(BasePortal):
    name = "mobile.de"
    BASE = "https://suchen.mobile.de"
    PREFERS_BROWSER = False  # kein Browser – curl_cffi + importierte Cookies

    def __init__(self, *args, cookies: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.cookies = (cookies or "").strip()

    # ---- URL ----------------------------------------------------------
    def _build_url(self, query: SearchQuery, page: int) -> str:
        params = ["isSearchRequest=true", "s=Car", "vc=Car", f"pageNumber={page}"]

        def span(name, low, high):
            if low is not None or high is not None:
                value = "{}:{}".format(low if low is not None else "",
                                       high if high is not None else "")
                params.append(f"{name}={quote_plus(value)}")

        span("p", query.price_from, query.price_to)
        span("fr", query.year_from, query.year_to)
        span("ml", query.mileage_from, query.mileage_to)
        span("pw", query.power_from, query.power_to)
        if query.ev_range_from:
            params.append(f"re={max(50, (query.ev_range_from // 100) * 100)}")
        if query.fuel and query.fuel in FUEL_MAP and query.fuel != "elektro":
            params.append(f"fu={FUEL_MAP[query.fuel]}")
        elif query.fuel == "elektro":
            params.append("fu=ELECTRICITY")
        term = " ".join(p for p in (query.make, query.model) if p)
        if term:
            params.append(f"q={quote_plus(term)}")
        return f"{self.BASE}/fahrzeuge/search.html?{'&'.join(params)}"

    # ---- Abruf --------------------------------------------------------
    def _fetch(self, url: str) -> str:
        if not self.cookies:
            raise CookiesExpired("mobile.de: keine Cookies hinterlegt.")
        try:
            from curl_cffi import requests as creq
        except ImportError:
            raise PortalError("mobile.de: curl_cffi nicht installiert.")
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            "Cookie": self.cookies,
            "Referer": "https://www.mobile.de/",
        }
        try:
            r = creq.get(url, headers=headers, impersonate="chrome124", timeout=25)
        except Exception as e:  # Netzwerk-/curl-Fehler
            raise PortalError(f"mobile.de: Abruf fehlgeschlagen ({e}).")
        html = r.text or ""
        low = html.lower()
        if ("behavioral-content" in low or "sec-if-cpt" in low
                or "zugriff verweigert" in low or "access denied" in low
                or r.status_code in (403, 429)):
            raise CookiesExpired(
                "mobile.de: Cookies abgelaufen oder ungültig – bitte im Browser "
                "neu einloggen und Cookies aktualisieren."
            )
        return html

    def search(self, query: SearchQuery) -> List[Listing]:
        results: List[Listing] = []
        seen_ids = set()
        for page in range(1, self.max_pages + 1):
            html = self._fetch(self._build_url(query, page))
            cards = self._parse_cards(html)
            new = 0
            for l in cards:
                if l.raw_id and l.raw_id in seen_ids:
                    continue
                if l.raw_id:
                    seen_ids.add(l.raw_id)
                results.append(l)
                new += 1
            if new == 0:
                break
        return results

    # ---- HTML-Karten-Parsing (server-gerendert, mit gültigen Cookies) --
    def _parse_cards(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "lxml")
        listings: List[Listing] = []
        for art in soup.select("article"):
            link = art.select_one("a[href*='details.html']")
            if not link:
                continue
            href = link.get("href", "")
            url = href if href.startswith("http") else "https://suchen.mobile.de" + href
            m = re.search(r"id=(\d+)", href)
            lid = m.group(1) if m else None
            tnode = (art.select_one("[data-testid$='-title']")
                     or art.select_one("[data-testid='listing-title-card-view']"))
            title = (re.sub(r"^Gesponsert\s*", "", tnode.get_text(" ", strip=True))
                     if tnode else "mobile.de-Inserat")
            pnode = (art.select_one("[data-testid='main-price-label']")
                     or art.select_one("[data-testid='price-label']"))
            price = self._to_int(pnode.get_text() if pnode else "")
            dnode = (art.select_one("[data-testid='listing-details-attributes']")
                     or art.select_one("[data-testid='listing-details']"))
            det = self._parse_details(dnode.get_text(" ", strip=True) if dnode else "")
            snode = art.select_one("[data-testid='seller-info']")
            listings.append(Listing(
                portal=self.name,
                title=title[:120],
                url=url,
                price=price,
                year=det["year"],
                mileage=det["mileage"],
                fuel=det["fuel"],
                power_ps=det["power_ps"],
                location=snode.get_text(" ", strip=True)[:60] if snode else None,
                body=("Unfallfahrzeug" if det["damaged"] else None),
                raw_id=lid,
            ))
        return listings

    @staticmethod
    def _parse_details(text: str) -> dict:
        """Parst z. B. 'Unfallfrei • EZ 01/2019 • 195.500 km • 85 kW (116 PS) • Diesel'."""
        out = {"year": None, "mileage": None, "power_ps": None, "fuel": None, "damaged": False}
        if not text:
            return out
        t = text.replace("\xa0", " ")
        m = re.search(r"EZ\s*\d{2}/(\d{4})", t)
        if m:
            out["year"] = int(m.group(1))
        m = re.search(r"([\d.]+)\s*km", t)
        if m:
            out["mileage"] = MobileDe._to_int(m.group(1))
        m = re.search(r"\((\d{2,4})\s*PS\)", t)
        if m:
            out["power_ps"] = int(m.group(1))
        low = t.lower()
        for f in ("elektro", "diesel", "benzin", "hybrid"):
            if f in low:
                out["fuel"] = f
                break
        if out["fuel"] is None and ("autogas" in low or "lpg" in low):
            out["fuel"] = "lpg"
        if "unfallfahrzeug" in low or ("unfall" in low and "unfallfrei" not in low):
            out["damaged"] = True
        return out

    # ---- Feld-Helfer --------------------------------------------------
    @staticmethod
    def _to_int(value) -> Optional[int]:
        if value is None:
            return None
        digits = re.sub(r"[^\d]", "", str(value))
        return int(digits) if digits else None

    @staticmethod
    def _norm_fuel(value) -> Optional[str]:
        if not value:
            return None
        v = str(value).lower()
        for token in ("elektro", "diesel", "benzin", "hybrid"):
            if token in v:
                return token
        if "lpg" in v or "autogas" in v:
            return "lpg"
        if "cng" in v or "erdgas" in v:
            return "cng"
        return None

    @staticmethod
    def _norm_gear(value) -> Optional[str]:
        if not value:
            return None
        v = str(value).lower()
        if "auto" in v:
            return "automatik"
        if "schalt" in v or "manuell" in v:
            return "schaltgetriebe"
        return None
