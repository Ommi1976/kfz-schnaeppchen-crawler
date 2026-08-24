"""Kleinanzeigen-Scraper (Rubrik Autos).

Kleinanzeigen liefert serverseitig gerendertes HTML. Wir parsen die
Ergebnis-Artikel direkt. Preis-/km-Angaben stehen im Freitext, daher
werden sie heuristisch extrahiert.
"""

from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery
from .base import BasePortal, PortalError

# Wie viele Detailseiten pro Suche höchstens nachgeladen werden (Requests sparen).
DETAIL_LIMIT = 40

_FUEL_NORM = {
    "benzin": "benzin", "diesel": "diesel", "elektro": "elektro",
    "hybrid": "hybrid", "autogas": "lpg", "lpg": "lpg",
    "erdgas": "cng", "cng": "cng",
}
_GEAR_NORM = {"manuell": "schaltgetriebe", "schaltgetriebe": "schaltgetriebe",
             "automatik": "automatik"}


class Kleinanzeigen(BasePortal):
    name = "Kleinanzeigen"
    BASE = "https://www.kleinanzeigen.de"

    def _build_url(self, query: SearchQuery, page: int) -> str:
        # Rubrik "Autos" = c216. Suchbegriff aus Marke + Modell.
        term_parts = [p for p in (query.make, query.model) if p]
        term = "-".join(term_parts) if term_parts else "auto"
        price = ""
        if query.price_from or query.price_to:
            price = f"/preis:{query.price_from or ''}:{query.price_to or ''}"
        page_seg = f"/seite:{page}" if page > 1 else ""
        return f"{self.BASE}/s-autos{price}{page_seg}/{term}/k0c216"

    def search(self, query: SearchQuery) -> List[Listing]:
        results: List[Listing] = []
        for page in range(1, self.max_pages + 1):
            url = self._build_url(query, page)
            resp = self._get(url)
            items = self._parse(resp.text, query)
            if not items:
                break
            results.extend(items)
        return results

    def _parse(self, html: str, query: SearchQuery) -> List[Listing]:
        soup = BeautifulSoup(html, "lxml")
        listings: List[Listing] = []
        for art in soup.select("article.aditem"):
            a = art.select_one("a[href]")
            if not a:
                continue
            href = a["href"]
            url = href if href.startswith("http") else self.BASE + href
            title = self._text(art, ".text-module-begin, h2 a, .aditem-main--middle--title")
            price = self._to_int(self._text(art, ".aditem-main--middle--price-shipping--price"))
            location = self._text(art, ".aditem-main--top--left")
            desc = self._text(art, ".aditem-main--middle--description") or ""

            listing = Listing(
                portal=self.name,
                title=(title or "Kleinanzeigen-Inserat")[:120],
                url=url,
                price=price,
                mileage=self._extract_km(desc + " " + (title or "")),
                year=self._extract_year(desc + " " + (title or "")),
                location=location,
                raw_id=art.get("data-adid"),
            )
            if self._matches(listing, query):
                listings.append(listing)
        return listings

    def _matches(self, l: Listing, q: SearchQuery) -> bool:
        # Preis-Filter clientseitig nachziehen (Freitext-Suche ist unscharf).
        if q.price_to and l.price and l.price > q.price_to:
            return False
        if q.price_from and l.price and l.price < q.price_from:
            return False
        if q.mileage_to and l.mileage and l.mileage > q.mileage_to:
            return False
        return True

    # ---- #4: Detailseiten-Anreicherung --------------------------------
    def enrich(self, listings: List[Listing], query: SearchQuery) -> List[Listing]:
        """Lädt Detailseiten nach, um Kraftstoff/Getriebe/Leistung/EZ/km
        strukturiert zu ermitteln. Nur sinnvoll, wenn die Suche Kriterien
        nutzt, die aus der Trefferliste nicht hervorgehen."""
        needs = any([query.fuel, query.transmission, query.power_from,
                     query.power_to, query.doors])
        if not needs:
            return listings
        for l in listings[:DETAIL_LIMIT]:
            try:
                html = self._get(l.url).text
            except (PortalError, Exception):
                continue
            self._parse_detail(html, l)
        return listings

    def _parse_detail(self, html: str, l: Listing) -> None:
        soup = BeautifulSoup(html, "lxml")
        for li in soup.select("li.addetailslist--detail"):
            valel = li.select_one(".addetailslist--detail--value")
            if not valel:
                continue
            value = valel.get_text(strip=True)
            key = li.get_text(" ", strip=True)
            key = key.replace(value, "").strip().lower()
            v = value.strip().lower()
            if "kilometerstand" in key:
                l.mileage = self._to_int(value) or l.mileage
            elif "erstzulassung" in key:
                y = re.search(r"(19|20)\d{2}", value)
                if y:
                    l.year = int(y.group(0))
            elif "kraftstoff" in key:
                for token, norm in _FUEL_NORM.items():
                    if token in v:
                        l.fuel = norm
                        break
            elif "leistung" in key:
                l.power_ps = self._to_int(value) or l.power_ps
            elif "getriebe" in key:
                for token, norm in _GEAR_NORM.items():
                    if token in v:
                        l.transmission = norm
                        break
            elif "fahrzeugzustand" in key:
                # Zustand in body ablegen -> Betrugsfilter (#5) kann greifen.
                l.body = (l.body + " " if l.body else "") + value

    # ---- Heuristiken --------------------------------------------------
    @staticmethod
    def _extract_km(text: str) -> Optional[int]:
        m = re.search(r"([\d\.]{4,})\s*km", text, re.IGNORECASE)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)))
        return None

    @staticmethod
    def _extract_year(text: str) -> Optional[int]:
        m = re.search(r"\b(19[89]\d|20[0-2]\d)\b", text)
        return int(m.group(1)) if m else None

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
