"""mobile.de-Scraper.

ACHTUNG: mobile.de setzt starken Bot-Schutz (u. a. DataDome) ein. Direkte
automatisierte Zugriffe werden häufig geblockt (HTTP 403). Dieser Scraper
ist bewusst defensiv gebaut und wirft bei einem Block eine PortalError,
sodass die übrigen Portale weiterlaufen. Für zuverlässigen Betrieb wäre
die offizielle mobile.de-API oder ein Browser-basierter Ansatz nötig.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery
from .base import BasePortal

FUEL_MAP = {"benzin": "PETROL", "diesel": "DIESEL", "elektro": "ELECTRICITY", "hybrid": "HYBRID"}


class MobileDe(BasePortal):
    name = "mobile.de"
    BASE = "https://suchen.mobile.de"
    PREFERS_BROWSER = True   # ohne Browser praktisch immer 403 (DataDome)

    def _build_url(self, query: SearchQuery, page: int) -> str:
        params = ["isSearchRequest=true", "sfmr=false", f"pageNumber={page}"]
        if query.price_from:
            params.append(f"minPrice={query.price_from}")
        if query.price_to:
            params.append(f"maxPrice={query.price_to}")
        if query.year_from:
            params.append(f"minFirstRegistrationDate={query.year_from}")
        if query.year_to:
            params.append(f"maxFirstRegistrationDate={query.year_to}")
        if query.mileage_to:
            params.append(f"maxMileage={query.mileage_to}")
        if query.fuel and query.fuel in FUEL_MAP:
            params.append(f"fuels={FUEL_MAP[query.fuel]}")
        # Freitext-Suche über Marke/Modell (Keyword-Feld, portal-unabhängig).
        term = " ".join(p for p in (query.make, query.model) if p)
        if term:
            params.append(f"q={requests_quote(term)}")
        return f"{self.BASE}/fahrzeuge/search.html?{'&'.join(params)}"

    def search(self, query: SearchQuery) -> List[Listing]:
        results: List[Listing] = []
        for page in range(1, self.max_pages + 1):
            url = self._build_url(query, page)
            resp = self._get(url)  # kann PortalError werfen -> im runner gefangen
            items = self._parse(resp.text)
            if not items:
                break
            results.extend(items)
        return results

    def _parse(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "lxml")
        # mobile.de bettet Ergebnisdaten teils als JSON in <script> ein.
        for script in soup.find_all("script"):
            txt = script.string or ""
            if "resultListItems" in txt or '"@type":"Car"' in txt:
                data = self._extract_json(txt)
                if data:
                    parsed = self._parse_json(data)
                    if parsed:
                        return parsed
        return self._parse_html(soup)

    def _parse_html(self, soup: BeautifulSoup) -> List[Listing]:
        listings: List[Listing] = []
        for a in soup.select("a[data-testid^='result-listing'], a.vehicle-data"):
            href = a.get("href", "")
            if not href:
                continue
            url = href if href.startswith("http") else "https://www.mobile.de" + href
            title = a.get_text(" ", strip=True)[:120] or "mobile.de-Inserat"
            price = self._to_int(self._text(a, "[data-testid='price-label'], .price-block"))
            listings.append(
                Listing(portal=self.name, title=title, url=url, price=price)
            )
        return listings

    def _parse_json(self, data) -> List[Listing]:
        listings: List[Listing] = []
        items = data if isinstance(data, list) else data.get("resultListItems", [])
        for it in items or []:
            if not isinstance(it, dict):
                continue
            listings.append(
                Listing(
                    portal=self.name,
                    title=str(it.get("title") or it.get("name") or "mobile.de-Inserat")[:120],
                    url=self._abs(it.get("relativeUrl") or it.get("url") or ""),
                    price=self._to_int((it.get("price") or {}).get("gross") if isinstance(it.get("price"), dict) else it.get("price")),
                    raw_id=str(it.get("id") or ""),
                )
            )
        return listings

    # ---- Helfer -------------------------------------------------------
    def _abs(self, href: str) -> str:
        if not href:
            return "https://www.mobile.de"
        return href if href.startswith("http") else "https://www.mobile.de" + href

    @staticmethod
    def _extract_json(text: str):
        # Sucht das erste ausgewogene JSON-Objekt/-Array im Script-Text.
        for pattern in (r"\{.*\}", r"\[.*\]"):
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
        return None

    @staticmethod
    def _text(node, selector: str) -> Optional[str]:
        el = node.select_one(selector)
        return el.get_text(strip=True) if el else None

    @staticmethod
    def _to_int(value) -> Optional[int]:
        if value is None:
            return None
        digits = re.sub(r"[^\d]", "", str(value))
        return int(digits) if digits else None


def requests_quote(s: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(s)
