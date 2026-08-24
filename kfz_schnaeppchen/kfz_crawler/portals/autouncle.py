"""AutoUncle-Scraper.

AutoUncle ist selbst eine Fahrzeug-Meta-Suchmaschine und bewertet Preise
(u. a. mit eigenem "Preis-Rating"). Die Ergebnisliste wird serverseitig
gerendert und zusätzlich als State-JSON eingebettet. Wir bevorzugen das
JSON und fallen auf HTML-Karten zurück.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery
from .base import BasePortal

FUEL_MAP = {"benzin": "petrol", "diesel": "diesel", "elektro": "electric", "hybrid": "hybrid"}


class AutoUncle(BasePortal):
    name = "AutoUncle"
    BASE = "https://www.autouncle.de"
    PREFERS_BROWSER = True   # ohne Browser 403

    def _build_url(self, query: SearchQuery, page: int) -> str:
        # AutoUncle filtert Marke/Modell PFADbasiert:
        #   /de/gebrauchtwagen/<marke>[/<modell>]
        # Die weiteren Kriterien (Preis, Jahr, km, Kraftstoff) greifen über die
        # Query nicht zuverlässig und werden daher client-seitig nachgefiltert.
        path = "/de/gebrauchtwagen"
        if query.make:
            path += f"/{quote(query.make)}"
            if query.model:
                path += f"/{quote(query.model)}"
        return f"{self.BASE}{path}?page={page}"

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
        # Gerenderte Seite (Browser-Modus): article-basierte Liste bevorzugen.
        html_listings = self._parse_html(soup)
        if html_listings:
            return html_listings
        # Fallback: State-JSON in einem <script> mit "cars"-Array.
        for script in soup.find_all("script"):
            txt = script.string or ""
            if '"cars"' in txt or '"carResults"' in txt:
                data = self._extract_json(txt)
                parsed = self._parse_json(data) if data else []
                if parsed:
                    return parsed
        return []

    def _parse_json(self, data) -> List[Listing]:
        cars = _find_cars(data)
        listings: List[Listing] = []
        for it in cars:
            listings.append(
                Listing(
                    portal=self.name,
                    title=self._title(it),
                    url=self._url(it),
                    price=self._to_int(it.get("price") or it.get("priceValue")),
                    year=self._to_int(it.get("year") or it.get("regDate")),
                    mileage=self._to_int(it.get("km") or it.get("mileage")),
                    fuel=it.get("fuel") or it.get("fuelType"),
                    location=it.get("city") or it.get("location"),
                    raw_id=str(it.get("id") or ""),
                )
            )
        return listings

    def _parse_html(self, soup: BeautifulSoup) -> List[Listing]:
        """Parst die (im Browser gerenderte) Ergebnisliste.

        AutoUncle rendert je Angebot ein <article> mit einem Link der Form
        /de/d/<id>-gebraucht-<jahr>-<marke>-<modell>-…  und im Text Preis,
        Jahr, km, Kraftstoff und Leistung.
        """
        listings: List[Listing] = []
        for art in soup.select("article"):
            a = art.select_one("a[href^='/de/d/'], a[href*='/de/d/']")
            if not a:
                continue
            href = a.get("href", "")
            url = href if href.startswith("http") else self.BASE + href
            text = re.sub(r"\s+", " ", art.get_text(" ", strip=True))

            m_price = re.search(r"([\d][\d\.]{2,})\s*€", text)
            m_year = re.search(r"\b((?:19|20)\d{2})\b", text)
            m_km = re.search(r"([\d][\d\.]{2,})\s*km", text)
            m_ps = re.search(r"(\d{2,4})\s*(?:PS|hp)\b", text)
            km = self._to_int(m_km.group(1)) if m_km else None
            if km and km > 500000:      # unplausibel -> verwerfen
                km = None
            fuel = None
            low = text.lower()
            for f in ("elektro", "diesel", "benzin", "hybrid"):
                if f in low:
                    fuel = f
                    break

            listings.append(
                Listing(
                    portal=self.name,
                    title=self._title_from_href(href) or a.get_text(" ", strip=True)[:120]
                    or "AutoUncle-Inserat",
                    url=url,
                    price=self._to_int(m_price.group(1)) if m_price else None,
                    year=int(m_year.group(1)) if m_year else None,
                    mileage=km,
                    fuel=fuel,
                    power_ps=int(m_ps.group(1)) if m_ps else None,
                    raw_id=self._id_from_href(href),
                )
            )
        return listings

    @staticmethod
    def _id_from_href(href: str) -> str:
        m = re.search(r"/de/d/(\d+)", href)
        return m.group(1) if m else href

    @staticmethod
    def _title_from_href(href: str) -> str:
        # /de/d/206415252-gebraucht-2023-toyota-proace-plus-144-ps
        m = re.search(r"/de/d/\d+-(.+)$", href)
        if not m:
            return ""
        slug = m.group(1)
        slug = re.sub(r"^gebraucht-\d{4}-", "", slug)
        slug = re.sub(r"^gebraucht-", "", slug)
        return slug.replace("-", " ").strip().title()[:120]

    # ---- Helfer -------------------------------------------------------
    @staticmethod
    def _title(it: dict) -> str:
        make = it.get("make") or it.get("brand") or ""
        model = it.get("model") or ""
        variant = it.get("variant") or it.get("headline") or ""
        return f"{make} {model} {variant}".strip()[:120] or "AutoUncle-Inserat"

    def _url(self, it: dict) -> str:
        url = it.get("url") or it.get("permalink") or ""
        if url:
            return url if str(url).startswith("http") else self.BASE + str(url)
        vid = it.get("id") or ""
        return f"{self.BASE}/de/cars/{vid}" if vid else self.BASE

    @staticmethod
    def _extract_json(text: str):
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


def _find_cars(obj, _depth: int = 0) -> List[dict]:
    """Sucht rekursiv nach einer Liste von Fahrzeug-Objekten im State-JSON."""
    if _depth > 8:
        return []
    if isinstance(obj, list):
        cars = [x for x in obj if isinstance(x, dict) and ("price" in x and ("km" in x or "mileage" in x or "year" in x))]
        if len(cars) >= 3:
            return cars
        out: List[dict] = []
        for x in obj:
            out.extend(_find_cars(x, _depth + 1))
        return out
    if isinstance(obj, dict):
        out: List[dict] = []
        for v in obj.values():
            out.extend(_find_cars(v, _depth + 1))
        return out
    return []
