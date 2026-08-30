"""mobile.de-Scraper (Playwright/Firefox-Rendering).

mobile.de ist durch Akamai Bot Manager geschützt. Reine requests werden
abgewiesen. Funktionierender Weg im Add-on: die Seite headless mit
Playwright/Firefox (Gecko-Engine) rendern – dieser Fingerprint kommt an die
server-gerenderten Ergebnis-Cards (data-testid) heran, ganz ohne Session-Cookies.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery
from .base import BasePortal, PortalError, PortalPartialError

logger = logging.getLogger(__name__)

FUEL_MAP = {"benzin": "PETROL", "diesel": "DIESEL", "elektro": "ELECTRICITY", "hybrid": "HYBRID"}
GEAR_MAP = {"schaltgetriebe": "MANUAL_GEAR", "automatik": "AUTOMATIC_GEAR"}
SELLER_MAP = {"haendler": "DEALER", "händler": "DEALER", "privat": "PRIVATE"}
PS_TO_KW = 1.35962


class MobileDe(BasePortal):
    name = "mobile.de"
    BASE = "https://suchen.mobile.de"
    PREFERS_BROWSER = True  # Autarker Playwright Firefox Abruf
    FULL_CRAWL_MAX_PAGES = 100  # harter Schutz, beendet regulär vorher bei leerer Seite

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
        pw_from = round(query.power_from / PS_TO_KW) if query.power_from else None
        pw_to = round(query.power_to / PS_TO_KW) if query.power_to else None
        span("pw", pw_from, pw_to)
        # Batteriekapazität (bat=<min>:)
        bat_from = int(query.battery_from_kwh) if getattr(query, "battery_from_kwh", None) else None
        span("bat", bat_from, None)
        if not query.include_damaged:
            params.append("dam=0")
        if query.fuel and query.fuel in FUEL_MAP:
            params.append(f"ft={FUEL_MAP[query.fuel]}")
        if query.transmission and query.transmission in GEAR_MAP:
            params.append(f"tr={GEAR_MAP[query.transmission]}")
        if query.seller and query.seller in SELLER_MAP:
            params.append(f"c={SELLER_MAP[query.seller]}")
        if query.zip_code:
            params.append(f"ambc={quote_plus(query.zip_code)}")
            if query.radius_km:
                params.append(f"rad={query.radius_km}")
        # Land / Region (cn)
        country = (query.country or "DE").strip().upper()
        if country == "ALL":
            pass  # Europaweit / alle Länder
        else:
            params.append(f"cn={country}")
        # Ausstattung (fe=<feature>)
        from .as24_taxonomy import EQUIPMENT_TO_MOBILE_DE
        for eq_id in (query.equipment or []):
            if eq_id in EQUIPMENT_TO_MOBILE_DE:
                params.append(f"fe={EQUIPMENT_TO_MOBILE_DE[eq_id]}")
        term = " ".join(p for p in (query.make, query.model) if p)
        if term:
            params.append(f"q={quote_plus(term)}")
        return f"{self.BASE}/fahrzeuge/search.html?{'&'.join(params)}"

    # ---- Abruf (autark via Playwright Chromium/Firefox mit Stealth) ----------
    def _fetch(self, url: str) -> str:
        try:
            from ..browser import fetch_rendered
            return fetch_rendered(
                url, proxy=self.proxy, engine="chromium",
                wait_until="domcontentloaded",
                wait_selector="article a[href*='details.html']",
                wait_selector_timeout_ms=20000,
                render_delay=1.0,
            )
        except Exception as e:
            raise PortalError(f"mobile.de: Abruf fehlgeschlagen – {e}")

    def search(self, query: SearchQuery) -> List[Listing]:
        # In Produktion wird genau eine Browser-Session für alle Ergebnisseiten
        # verwendet. Bei Tests/Overrides bleibt _fetch als kompatibler Hook.
        original_fetch = getattr(self._fetch, "__func__", None) is MobileDe._fetch
        if original_fetch:
            try:
                from ..browser import rendered_session
                with rendered_session(
                    proxy=self.proxy,
                    engine="firefox",
                    request_delay_range=(8.0, 14.0),
                ) as session_fetch:
                    return self._crawl_pages(
                        query,
                        lambda url: session_fetch(
                            url,
                            wait_selector="article a[href*='details.html']",
                            render_delay=0.8,
                            max_retries=0,
                        ),
                    )
            except PortalPartialError:
                raise
            except Exception as exc:
                raise PortalError(f"mobile.de: Abruf fehlgeschlagen – {exc}") from exc
        return self._crawl_pages(query, self._fetch)

    def _crawl_pages(self, query: SearchQuery, fetcher) -> List[Listing]:
        results: List[Listing] = []
        seen_ids = set()
        # Die Einstellung max_pages war ursprünglich eine Stichprobengröße.
        # Für mobile.de muss aber bis zur ersten leeren Seite gelesen werden,
        # sonst fehlen bei mehr als fünf Seiten (z. B. 159 statt 104 Treffer)
        # reguläre Inserate. FULL_CRAWL_MAX_PAGES ist nur eine Schutzgrenze.
        max_limit = max(self.max_pages or 0, self.FULL_CRAWL_MAX_PAGES)
        for page in range(1, max_limit + 1):
            try:
                html = fetcher(self._build_url(query, page))
            except Exception as e:
                if results:
                    raise PortalPartialError(
                        f"mobile.de: {len(results)} Treffer bis Seite {page - 1}; "
                        f"Seite {page} wurde blockiert – {e}",
                        listings=results,
                        failed_page=page,
                    ) from e
                raise PortalError(f"mobile.de: Seite {page} konnte nicht vollständig geladen werden – {e}") from e
            cards = self._parse_cards(html)
            if not cards:
                break
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

    # PLZ (optional "DE-") + Stadt (beginnt mit Großbuchstabe, keine Einheit wie km).
    _LOC_RE = re.compile(
        r"(?:DE-)?\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\-/ ]{2,38})"
    )

    @classmethod
    def _extract_location(cls, *texts: str) -> Optional[str]:
        """Sucht 'PLZ Stadt' in den gegebenen Texten (untrunkiert!) und liefert
        z. B. '68766 Hockenheim'. Fällt sonst auf den ersten Text (nur Stadt)
        zurück – so bleibt zumindest eine Ortsanzeige erhalten.
        """
        for t in texts:
            if not t:
                continue
            m = cls._LOC_RE.search(t)
            if m:
                city = m.group(2).strip().rstrip(",;·|").strip()
                return f"{m.group(1)} {city}"[:60]
        for t in texts:
            if t:
                return t[:60]
        return None

    # ---- HTML-Karten-Parsing (server-gerendert, mit gültigen Cookies) --
    def _parse_cards(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "lxml")
        listings: List[Listing] = []
        # mobile.de ändert gelegentlich die data-testid-Werte, die
        # Kartenstruktur und der Detail-Link bleiben dagegen stabil.
        cards = soup.select(
            "article, [data-testid*='listing-card'], [data-testid*='listing-result']"
        )
        for art in cards:
            link = art.select_one(
                "a[href*='details.html'], a[href*='/auto-inserat/'], "
                "a[href*='/fahrzeuge/']"
            )
            if not link:
                continue
            href = link.get("href", "")
            url = href if href.startswith("http") else "https://suchen.mobile.de" + href
            lid = self._listing_id(href)
            tnode = art.select_one(
                "[data-testid$='-title'], [data-testid*='title'], h2, h3, "
                "[class*='title']"
            )
            title = self._clean_title(
                tnode.get_text(" ", strip=True) if tnode else ""
            )
            full_card_text = art.get_text(" ", strip=True)
            if not title:
                title = self._clean_title(link.get("aria-label", ""))
            pnode = art.select_one(
                "[data-testid='main-price-label'], [data-testid='price-label'], "
                "[data-testid*='price'], [class*='price']"
            )
            price = self._to_int(pnode.get_text() if pnode else "")
            if price is None:
                price = self._extract_price(full_card_text)
            dnode = art.select_one(
                "[data-testid='listing-details-attributes'], "
                "[data-testid='listing-details'], [data-testid*='attributes'], "
                "[class*='details']"
            )
            details_text = dnode.get_text(" ", strip=True) if dnode else full_card_text
            det = self._parse_details(details_text)
            # Einzelne Werte liegen je nach mobile.de-Layout außerhalb des
            # Detail-Containers. Mit dem Kartentext werden diese nachgezogen,
            # ohne bereits sicher erkannte Werte zu überschreiben.
            if any(v is None for k, v in det.items() if k in ("year", "mileage", "power_ps", "fuel")):
                fallback = self._parse_details(full_card_text)
                for key in ("year", "mileage", "power_ps", "fuel"):
                    if det[key] is None:
                        det[key] = fallback[key]
            snode = art.select_one("[data-testid='seller-info']")
            imgs = [
                img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                for img in art.select("img[src], img[data-src], img[data-lazy-src]")
            ]
            image_urls = [u for u in imgs if u and u.startswith("http") and not u.endswith(".svg")]
            seller_txt = snode.get_text(" ", strip=True) if snode else ""
            # PLZ steht bei mobile.de oft HINTER dem langen Händlernamen – daher
            # untrunkiert aus seller-info und der ganzen Karte suchen.
            loc = self._extract_location(seller_txt, full_card_text)
            l = Listing(
                portal=self.name,
                title=(title or "mobile.de-Inserat")[:120],
                url=url,
                price=price,
                year=det["year"],
                mileage=det["mileage"],
                fuel=det["fuel"],
                power_ps=det["power_ps"],
                location=loc,
                body=full_card_text,
                image_urls=image_urls,
                raw_id=lid,
            )
            from ..models import infer_listing_details
            infer_listing_details(l)
            listings.append(l)
        return listings

    @staticmethod
    def _clean_title(value: str) -> str:
        return re.sub(r"^(?:Gesponsert|Anzeige)\s*[:|-]?\s*", "", value or "", flags=re.I).strip()

    @staticmethod
    def _listing_id(href: str) -> Optional[str]:
        query_id = parse_qs(urlparse(href).query).get("id", [None])[0]
        if query_id and str(query_id).isdigit():
            return str(query_id)
        match = re.search(r"(?:/|[-_])([0-9]{7,})(?:\D|$)", href or "")
        return match.group(1) if match else None

    @staticmethod
    def _extract_price(text: str) -> Optional[int]:
        # Nur Eurobeträge verwenden; Monatsraten ohne Eurobetrag werden so
        # nicht versehentlich als Kaufpreis gespeichert.
        match = re.search(r"\b(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*€", text or "")
        return MobileDe._to_int(match.group(1)) if match else None

    @staticmethod
    def _parse_details(text: str) -> dict:
        """Parst die wechselnden Kurzangaben in einer mobile.de-Karte."""
        out = {"year": None, "mileage": None, "power_ps": None, "fuel": None, "damaged": False}
        if not text:
            return out
        t = text.replace("\xa0", " ")
        m = re.search(
            r"(?:\bEZ\b|erstzulassung|baujahr)\s*[:.]?\s*(?:\d{1,2}[./])?(\d{4})",
            t,
            re.I,
        )
        if m:
            out["year"] = int(m.group(1))
        m = re.search(r"([\d.\s]+)\s*(?:km|kilometer)\b", t, re.I)
        if m:
            out["mileage"] = MobileDe._to_int(m.group(1))
        m = re.search(r"(\d{2,4})\s*kW\s*(?:\(\s*(\d{2,4})\s*PS\s*\))?", t, re.I)
        if m:
            out["power_ps"] = int(m.group(2) or round(int(m.group(1)) * PS_TO_KW))
        else:
            m = re.search(r"(?:\(|\b)(\d{2,4})\s*PS\b", t, re.I)
            if m:
                out["power_ps"] = int(m.group(1))
        low = t.lower()
        out["fuel"] = MobileDe._norm_fuel(low)
        if "unfallfahrzeug" in low or ("unfall" in low and "unfallfrei" not in low) or ("beschädigt" in low and "unbeschädigt" not in low):
            out["damaged"] = True
        return out

    # ---- Feld-Helfer --------------------------------------------------
    @staticmethod
    def _to_int(value) -> Optional[int]:
        if value is None:
            return None
        text = str(value)
        if "€" in text:
            text = text.split("€")[0]
        digits = re.sub(r"[^0-9]", "", text)
        return int(digits) if digits else None

    @staticmethod
    def _norm_fuel(value) -> Optional[str]:
        if not value:
            return None
        v = str(value).lower()
        if any(token in v for token in ("elektro", "elektrisch", "electric", "bev", "stromer")):
            return "elektro"
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
