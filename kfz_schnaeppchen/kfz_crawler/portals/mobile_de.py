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

from ..models import (
    Listing,
    SearchQuery,
    extract_battery_kwh,
    extract_ev_range_km,
)
from .base import BasePortal

FUEL_MAP = {"benzin": "PETROL", "diesel": "DIESEL", "elektro": "ELECTRICITY", "hybrid": "HYBRID"}


class MobileDe(BasePortal):
    name = "mobile.de"
    BASE = "https://suchen.mobile.de"
    PREFERS_BROWSER = True   # ohne Browser praktisch immer 403 (DataDome)

    def _build_url(self, query: SearchQuery, page: int) -> str:
        # Die öffentliche Suchseite verwendet aktuell die Kurzparameter der
        # Ergebnis-URL. Die alten minPrice/maxPrice-Parameter werden zwar
        # akzeptiert, aber nicht zuverlässig in der Suche angewendet.
        params = ["isSearchRequest=true", "s=Car", "vc=Car", f"pageNumber={page}"]

        def span(name: str, low, high) -> None:
            if low is not None or high is not None:
                params.append(f"{name}={requests_quote(f'{low or ""}:{high or ""}')}")

        span("p", query.price_from, query.price_to)
        span("fr", query.year_from, query.year_to)
        span("ml", query.mileage_from, query.mileage_to)

        # mobile.de bietet nur grobe Stufen. Wir wählen die nächstkleinere
        # Stufe und ziehen den exakten Wert anschließend zentral nach.
        if query.ev_range_from:
            params.append(f"re={max(50, (query.ev_range_from // 100) * 100)}")
        if query.battery_from_kwh:
            params.append(f"bc={max(10, int(query.battery_from_kwh // 10) * 10)}")
        if query.fuel and query.fuel in FUEL_MAP and query.fuel != "elektro":
            params.append(f"fu={FUEL_MAP[query.fuel]}")
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
        for a in soup.select(
            "a[data-testid^='result-listing'], a.vehicle-data, "
            "a[href*='/fahrzeuge/details.html']"
        ):
            href = a.get("href", "")
            if not href:
                continue
            url = href if href.startswith("http") else "https://www.mobile.de" + href
            text = a.get_text(" ", strip=True)
            title_node = a.find(["h2", "h3"])
            title = (
                title_node.get_text(" ", strip=True)
                if title_node else text[:120]
            ) or "mobile.de-Inserat"
            price = self._to_int(
                self._text(a, "[data-testid='price-label'], .price-block")
            ) or self._extract_price(text)
            listings.append(self._listing_from_text(title, url, price, text))
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
                    year=self._extract_year(it.get("firstRegistrationDate") or it.get("registrationDate")),
                    mileage=self._to_int(it.get("mileage") or it.get("mileageInKm")),
                    fuel=str(it.get("fuel") or it.get("fuelType") or "").lower() or None,
                    power_ps=self._extract_power(str(it.get("power") or it.get("powerPs") or "")),
                    ev_range_km=self._to_int(it.get("range") or it.get("electricRange")),
                    battery_kwh=self._to_float(it.get("batteryCapacity") or it.get("batteryKwh")),
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

    @staticmethod
    def _to_float(value) -> Optional[float]:
        if value is None:
            return None
        match = re.search(r"\d+(?:[.,]\d+)?", str(value))
        return float(match.group(0).replace(",", ".")) if match else None

    @staticmethod
    def _extract_year(text: str | None) -> Optional[int]:
        if not text:
            return None
        match = re.search(r"\b(19\d{2}|20\d{2})\b", str(text))
        return int(match.group(1)) if match else None

    @staticmethod
    def _extract_price(text: str | None) -> Optional[int]:
        if not text:
            return None
        for value in re.findall(r"\b(\d{1,3}(?:\.\d{3})+|\d{4,6})\s*€", text):
            price = int(value.replace(".", ""))
            if 500 <= price <= 500000:
                return price
        return None

    @staticmethod
    def _extract_power(text: str | None) -> Optional[int]:
        if not text:
            return None
        match = re.search(r"(\d{2,4})\s*kW\s*\((\d{2,4})\s*PS\)", text, re.IGNORECASE)
        return int(match.group(2)) if match else None

    def _listing_from_text(self, title: str, url: str, price: Optional[int], text: str) -> Listing:
        return Listing(
            portal=self.name,
            title=title[:120],
            url=url,
            price=price,
            year=self._extract_year(text),
            mileage=self._extract_mileage(text),
            fuel=self._extract_fuel(text),
            power_ps=self._extract_power(text),
            ev_range_km=extract_ev_range_km(text),
            battery_kwh=extract_battery_kwh(text),
        )

    @staticmethod
    def _extract_mileage(text: str | None) -> Optional[int]:
        if not text:
            return None
        match = re.search(r"\b(\d{1,3}(?:\.\d{3})+|\d{4,6})\s*km\b", text, re.IGNORECASE)
        return int(match.group(1).replace(".", "")) if match else None

    @staticmethod
    def _extract_fuel(text: str | None) -> Optional[str]:
        if not text:
            return None
        value = text.lower()
        if "elektro" in value:
            return "elektro"
        if "diesel" in value:
            return "diesel"
        if "benzin" in value:
            return "benzin"
        if "hybrid" in value:
            return "hybrid"
        return None


def requests_quote(s: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(s)
