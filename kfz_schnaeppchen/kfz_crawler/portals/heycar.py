"""heycar-Scraper.

heycar (hey.car) ist eine Single-Page-App und lädt Ergebnisse über eine
JSON-Schnittstelle nach. Wir versuchen zunächst das im HTML eingebettete
State-JSON zu lesen und fallen auf HTML-Karten zurück. heycar ändert seine
Struktur häufig – bei Ausfall laufen die übrigen Portale normal weiter.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery


from .base import BasePortal


class Heycar(BasePortal):
    name = "heycar"
    BASE = "https://hey.car"
    PREFERS_BROWSER = True   # SPA: Ergebnisse werden per JS nachgeladen

    def _build_url(self, query: SearchQuery, page: int) -> str:
        params = [f"page={page}", "sort=price.asc"]
        if query.make:
            params.append(f"makes={query.make}")
        if query.model:
            params.append(f"models={query.model}")
        if query.price_to:
            params.append(f"priceTo={query.price_to}")
        if query.price_from:
            params.append(f"priceFrom={query.price_from}")
        if query.mileage_to:
            params.append(f"mileageTo={query.mileage_to}")
        if query.year_from:
            params.append(f"firstRegistrationFrom={query.year_from}")
        return f"{self.BASE}/gebrauchtwagen?{'&'.join(params)}"

    def search(self, query: SearchQuery) -> List[Listing]:
        results: List[Listing] = []
        for page in range(1, self.max_pages + 1):
            url = self._build_url(query, page)
            resp = self._get(url)
            items = self._parse(resp.text)
            if not items:
                break
            results.extend(items)
        return results

    def _parse(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "lxml")
        node = soup.find("script", id="__NEXT_DATA__")
        if node and node.string:
            try:
                return self._parse_next(json.loads(node.string))
            except (json.JSONDecodeError, TypeError):
                pass
        return self._parse_html(soup)

    def _parse_next(self, data: dict) -> List[Listing]:
        listings: List[Listing] = []
        found = _find_listing_arrays(data)
        for it in found:
            price = it.get("price") or it.get("priceGross") or (it.get("prices") or {}).get("gross")
            listings.append(
                Listing(
                    portal=self.name,
                    title=self._title(it),
                    url=self._url(it),
                    price=self._to_int(price),
                    year=self._to_int(str(it.get("firstRegistration", ""))[:4]),
                    mileage=self._to_int(it.get("mileage")),
                    fuel=it.get("fuelType"),
                    raw_id=str(it.get("id") or it.get("vehicleId") or ""),
                )
            )
        return listings

    def _parse_html(self, soup: BeautifulSoup) -> List[Listing]:
        listings: List[Listing] = []
        for a in soup.select("a[href*='/gebrauchtwagen/'], a[data-testid*='vehicle']"):
            href = a.get("href", "")
            if not href or href.endswith("/gebrauchtwagen"):
                continue
            url = href if href.startswith("http") else self.BASE + href
            title = a.get_text(" ", strip=True)[:120] or "heycar-Inserat"
            price = self._to_int(self._text(a, "[class*='price'], [data-testid*='price']"))
            listings.append(Listing(portal=self.name, title=title, url=url, price=price))
        return listings

    # ---- Helfer -------------------------------------------------------
    @staticmethod
    def _title(it: dict) -> str:
        make = it.get("make") or ""
        model = it.get("model") or ""
        trim = it.get("trimline") or it.get("variant") or ""
        return f"{make} {model} {trim}".strip()[:120] or "heycar-Inserat"

    def _url(self, it: dict) -> str:
        slug = it.get("url") or it.get("slug") or ""
        vid = it.get("id") or it.get("vehicleId") or ""
        if slug:
            return slug if str(slug).startswith("http") else self.BASE + str(slug)
        return f"{self.BASE}/gebrauchtwagen/{vid}" if vid else self.BASE

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


def _find_listing_arrays(obj, _depth: int = 0) -> List[dict]:
    """Durchsucht das State-JSON rekursiv nach Listen von Fahrzeug-Objekten."""
    if _depth > 8:
        return []
    if isinstance(obj, list):
        cars = [x for x in obj if isinstance(x, dict) and ("mileage" in x or "firstRegistration" in x)]
        if len(cars) >= 3:
            return cars
        out: List[dict] = []
        for x in obj:
            out.extend(_find_listing_arrays(x, _depth + 1))
        return out
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out.extend(_find_listing_arrays(v, _depth + 1))
        return out
    return []
