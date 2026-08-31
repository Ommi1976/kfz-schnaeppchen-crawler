"""AutoUncle-Scraper.

AutoUncle ist selbst eine Fahrzeug-Meta-Suchmaschine und bewertet Preise
(u. a. mit eigenem "Preis-Rating"). Die Ergebnisliste wird serverseitig
gerendert und zusätzlich als State-JSON eingebettet. Wir bevorzugen das
JSON und fallen auf HTML-Karten zurück.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, urlencode

from bs4 import BeautifulSoup

from ..models import (
    Listing,
    SearchQuery,
    evaluate_query,
    extract_battery_soh,
    extract_battery_kwh,
    extract_ev_range_km,
)
from .base import BasePortal, PortalError, PortalPartialError

logger = logging.getLogger(__name__)

FUEL_MAP = {"benzin": "petrol", "diesel": "diesel", "elektro": "electric", "hybrid": "hybrid"}


class AutoUncle(BasePortal):
    name = "AutoUncle"
    BASE = "https://www.autouncle.de"
    PREFERS_BROWSER = True   # ohne Browser 403
    # Ergebnisseiten je Suche – GEMEINSAM über alle adaptiven Varianten.
    # Ohne gemeinsames Budget holt jede Fallback-Variante erneut die volle
    # Seitenzahl. Das ist der eigentliche Schutz hier.
    #
    # Die Seitenzahl selbst darf hoeher liegen als bei mobile.de: AutoUncle war
    # nie gesperrt und lieferte bei 20 Seiten stabil. Vor allem aber bietet es
    # keinen Akku- und keinen Marken-Filter, sodass grob vorgefiltert werden
    # muss – gemessen scheitern 237 von 299 Rohtreffern erst lokal an der
    # Akkukapazitaet. Ein enges Budget kappt hier die Ausbeute, ohne die
    # Verschwendung zu verringern (12 Seiten: 299 roh / 54 passend).
    PAGE_BUDGET = 20
    FULL_CRAWL_MAX_PAGES = 20  # Reißleine, auch wenn page_budget gesetzt wird

    @property
    def _use_browser(self) -> bool:
        """AutoUncle wird immer mit einem echten Browser abgerufen.

        Die öffentliche Seite antwortet auf reine HTTP-Anfragen regelmäßig mit
        403. Der Portal-Schalter bleibt trotzdem erhalten: nur wenn AutoUncle
        in der Konfiguration aktiviert ist, wird diese Klasse überhaupt
        instanziiert.
        """
        return True

    def _build_url(self, query: SearchQuery, page: int) -> str:
        # AutoUncle filtert Marke/Modell PFADbasiert:
        #   /de/gebrauchtwagen/<marke>[/<modell>]
        # Die weiteren Kriterien (Preis, Jahr, km, Kraftstoff) greifen über die
        # Query nicht zuverlässig und werden daher client-seitig nachgefiltert.
        # Für eine reine E-Auto-Suche gibt es eine eigene, deutlich ergiebigere
        # Landingpage. Die allgemeine Auto-Seite enthält viele Verbrenner- und
        # Neuwagen-Karten; dadurch kann eine korrekte Nachfilterung trotz vieler
        # Rohkarten fälschlich bei null passenden Treffern landen.
        path = "/de/gebrauchtwagen/f-elektro" if query.fuel == "elektro" and not query.make else "/de/gebrauchtwagen"
        if query.make:
            path += f"/{quote(query.make)}"
            if query.model:
                path += f"/{quote(query.model)}"
        if query.price_to is not None:
            path += f"/mp-unter-{int(query.price_to)}-euro"

        params = [("page", page), ("s[order_by]", "price_asc")]
        if query.make:
            make = "VW" if query.make in ("vw", "volkswagen") else query.make.title()
            params.append(("s[brands_models][][brand]", make))
        if query.model:
            params.append(("s[brands_models][][model]", query.model.title()))
        if query.mileage_to is not None:
            params.append(("s[max_km]", int(query.mileage_to)))
        if query.year_from is not None:
            params.append(("s[min_year]", int(query.year_from)))
        if query.ev_range_from is not None:
            params.append(("s[min_electric_drive_range]", int(query.ev_range_from)))
        if query.power_from is not None:
            params.append(("s[min_hp]", int(query.power_from)))
        if query.equipment:
            # Die beiden AutoUncle-Optionen existieren in der öffentlichen
            # Suche; alle übrigen Ausstattungen werden weiterhin lokal geprüft.
            if 133 in query.equipment or 38 in query.equipment:
                params.append(("s[has_distance_control]", "true"))
            if 34 in query.equipment:
                params.append(("s[has_seat_heat]", "true"))
        return f"{self.BASE}{path}?{urlencode(params, doseq=True)}"

    @staticmethod
    def _query_variants(query: SearchQuery) -> List[SearchQuery]:
        """Erzeugt zwei kontrollierte, portalverträgliche Suchhüllen.

        AutoUncle bietet nur grobe Filterstufen und kann bei einer zu engen
        Kombination eine leere Liste zurückgeben. Die zweite/ dritte Hülle
        erweitert daher obere Grenzen nach oben und Mindestwerte nach unten.
        Die eigentliche Entscheidung bleibt danach immer der ursprüngliche
        lokale Suchfilter.
        """
        variants = [query]
        for step in (1, 2):
            def lower(value, amount):
                return None if value is None else max(0, value - amount * step)

            def higher(value, amount):
                return None if value is None else value + amount * step

            variants.append(replace(
                query,
                price_to=higher(query.price_to, 5000),
                mileage_to=higher(query.mileage_to, 25000),
                year_from=lower(query.year_from, 1),
                ev_range_from=lower(query.ev_range_from, 50),
                power_from=lower(query.power_from, 25),
                battery_from_kwh=lower(query.battery_from_kwh, 5),
            ))
        return variants

    def search(self, query: SearchQuery) -> List[Listing]:
        # Eine konsistente Firefox-Session ist robuster als pro Seite ein neuer
        # Browser. Das Profil bleibt lokal unter /data und enthält keine von
        # uns abgefragten Zugangsdaten; eine bestehende AutoUncle-Anmeldung kann
        # nur durch eine explizit auf dem HA-Host eingerichtete Session genutzt
        # werden.
        if self._use_browser:
            try:
                from ..browser import rendered_session
                profile = os.environ.get("AUTO_UNCLE_PROFILE")
                if not profile:
                    profile = (
                        "/data/autouncle_profile"
                        if Path("/data").exists()
                        else str(Path(__file__).parent.parent / "autouncle_profile")
                    )
                with rendered_session(
                    proxy=self.proxy,
                    engine="firefox",
                    request_delay_range=(3.5, 6.5),
                    warmup_url=f"{self.BASE}/",
                    profile_dir=profile,
                ) as fetch:
                    return self._search_variants(query, fetch)
            except PortalPartialError:
                raise
            except Exception as exc:
                raise PortalError(f"AutoUncle: Browserabruf fehlgeschlagen – {exc}") from exc

        return self._search_variants(query, lambda url: self._get(url).text)

    def _search_variants(self, query: SearchQuery, fetcher) -> List[Listing]:
        results: List[Listing] = []
        seen_ids = set()
        # Das Seitenbudget gilt für die Suche als Ganzes: Fallback-Varianten
        # bekommen nur, was die vorherigen übrig gelassen haben.
        budget = int(getattr(self, "page_budget", 0) or self.PAGE_BUDGET)
        rest = max(1, min(budget, self.FULL_CRAWL_MAX_PAGES))
        for variant in self._query_variants(query):
            if rest <= 0:
                logger.info("AutoUncle: Seitenbudget aufgebraucht, weitere Varianten entfallen")
                break
            self._last_pages_fetched = 0
            items = self._crawl_pages(variant, fetcher, max_pages=rest)
            # Mindestens eine Seite je Variante abziehen, damit das Budget
            # auch dann endet, wenn ein Aufruf den Verbrauch nicht meldet.
            rest -= max(1, int(getattr(self, "_last_pages_fetched", 1) or 1))
            for item in items:
                key = item.raw_id or item.url
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                results.append(item)
            # Nur wenn die Originalkriterien wirklich keinen Treffer ergeben,
            # wird die nächste größere Suchhülle abgerufen.
            if any(evaluate_query(item, query).passed for item in results):
                break
        return results

    _last_pages_fetched = 0

    def _crawl_pages(self, query: SearchQuery, fetcher, max_pages: Optional[int] = None) -> List[Listing]:
        results: List[Listing] = []
        seen_ids = set()
        # max_pages ist hier das verbleibende Budget der Gesamtsuche, nicht die
        # allgemeine Stichprobengröße aus der Konfiguration.
        if max_pages is None:
            budget = int(getattr(self, "page_budget", 0) or self.PAGE_BUDGET)
            max_pages = min(budget, self.FULL_CRAWL_MAX_PAGES)
        max_limit = max(1, min(int(max_pages), self.FULL_CRAWL_MAX_PAGES))
        self._last_pages_fetched = 0
        for page in range(1, max_limit + 1):
            self._last_pages_fetched = page
            try:
                html = fetcher(
                    self._build_url(query, page),
                    wait_selector="article",
                    render_delay=1.2,
                    max_retries=0,
                )
            except Exception as exc:
                if results:
                    raise PortalPartialError(
                        f"AutoUncle: {len(results)} Treffer bis Seite {page - 1}; "
                        f"Seite {page} wurde nicht geladen – {exc}",
                        listings=results,
                        failed_page=page,
                    ) from exc
                raise
            items = self._parse(html)
            if not items:
                break
            new = 0
            for item in items:
                if item.raw_id and item.raw_id in seen_ids:
                    continue
                if item.raw_id:
                    seen_ids.add(item.raw_id)
                results.append(item)
                new += 1
            if not new:
                break
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
            listing = Listing(
                    portal=self.name,
                    title=self._title(it),
                    url=self._url(it),
                    price=self._to_int(it.get("price") or it.get("priceValue")),
                    year=self._to_int(it.get("year") or it.get("regDate")),
                    mileage=self._to_int(it.get("km") or it.get("mileage")),
                    fuel=it.get("fuel") or it.get("fuelType"),
                    location=it.get("city") or it.get("location"),
                    body=json.dumps(it, ensure_ascii=False)[:6000],
                    raw_id=str(it.get("id") or ""),
                )
            from ..models import infer_listing_details
            infer_listing_details(listing)
            listings.append(listing)
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

            # Der eigentliche Angebotslink ist stabiler und für den Nutzer
            # nützlicher als die interne AutoUncle-Detail-URL.
            offer = art.select_one("a[href*='/das_wiedersehen/']")
            offer_href = offer.get("href", "") if offer else ""
            offer_url = offer_href if offer_href.startswith("http") else (self.BASE + offer_href if offer_href else url)

            heading = art.select_one("h2, h3, [data-testid*='title']")
            subtitle = art.select_one("p")
            card_title = heading.get_text(" ", strip=True) if heading else ""
            if subtitle and subtitle is not heading:
                subtext = subtitle.get_text(" ", strip=True)
                if subtext and subtext.lower() not in card_title.lower():
                    card_title = f"{card_title} {subtext}".strip()

            m_price = re.search(r"([\d][\d\.]{2,})\s*€", text)
            m_year = re.search(r"\b((?:19|20)\d{2})\b", text)
            m_km = re.search(r"([\d][\d\.]{2,})\s*km", text)
            m_ps = re.search(r"(?:\(|\s)(\d{2,4})\s*(?:PS|hp)\b", text, re.I)
            km = self._to_int(m_km.group(1)) if m_km else None
            if km and km > 500000:      # unplausibel -> verwerfen
                km = None
            fuel = None
            low = text.lower()
            for f in ("elektro", "diesel", "benzin", "hybrid"):
                if f in low:
                    fuel = f
                    break

            listing = Listing(
                    portal=self.name,
                    title=card_title or self._title_from_href(href) or a.get_text(" ", strip=True)[:120]
                    or "AutoUncle-Inserat",
                    url=offer_url,
                    price=self._to_int(m_price.group(1)) if m_price else None,
                    year=int(m_year.group(1)) if m_year else None,
                    mileage=km,
                    fuel=fuel,
                    power_ps=int(m_ps.group(1)) if m_ps else None,
                    ev_range_km=extract_ev_range_km(text),
                    battery_kwh=extract_battery_kwh(text),
                    battery_soh=extract_battery_soh(text),
                    location=self._extract_location(text),
                    body=text,
                    raw_id=self._id_from_href(href),
                )
            from ..models import infer_listing_details
            infer_listing_details(listing)
            listings.append(listing)
        return listings

    @staticmethod
    def _extract_location(text: str) -> Optional[str]:
        match = re.search(r"\b(\d{5})\s+([A-ZÄÖÜ][\wÄÖÜäöüß.\- ]{2,45})(?:,\s*[^\d]{2,35})?", text)
        return f"{match.group(1)} {match.group(2).strip()}" if match else None

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
