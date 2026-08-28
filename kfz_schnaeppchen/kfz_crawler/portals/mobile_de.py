"""mobile.de-Scraper (Playwright/Firefox-Rendering).

mobile.de ist durch Akamai Bot Manager geschützt. Reine requests werden
abgewiesen. Funktionierender Weg im Add-on: die Seite headless mit
Playwright/Firefox (Gecko-Engine) rendern – dieser Fingerprint kommt an die
server-gerenderten Ergebnis-Cards (data-testid) heran, ganz ohne Session-Cookies.
"""

from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery
from .base import BasePortal, PortalError

FUEL_MAP = {"benzin": "PETROL", "diesel": "DIESEL", "elektro": "ELECTRICITY", "hybrid": "HYBRID"}
GEAR_MAP = {"schaltgetriebe": "MANUAL_GEAR", "automatik": "AUTOMATIC_GEAR"}
SELLER_MAP = {"haendler": "DEALER", "händler": "DEALER", "privat": "PRIVATE"}
PS_TO_KW = 1.35962


class MobileDe(BasePortal):
    name = "mobile.de"
    BASE = "https://suchen.mobile.de"
    PREFERS_BROWSER = True  # Autarker Playwright Firefox Abruf

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
        term = " ".join(p for p in (query.make, query.model) if p)
        if term:
            params.append(f"q={quote_plus(term)}")
        return f"{self.BASE}/fahrzeuge/search.html?{'&'.join(params)}"

    # ---- Abruf (autark via Playwright Firefox auf dem Server) ----------
    def _fetch(self, url: str) -> str:
        try:
            from ..browser import fetch_rendered
            # Explizit warten, bis die Ergebnis-Cards im DOM sind – auf einer
            # ausgelasteten HAOS-Box reicht ein fester Delay nicht, die SPA ist
            # dann noch leer (0 Treffer). Danach kleiner Settle-Delay.
            return fetch_rendered(
                url, proxy=self.proxy, engine="firefox",
                wait_until="domcontentloaded",
                wait_selector="article a[href*='details.html']",
                wait_selector_timeout_ms=20000,
                render_delay=0.8,
            )
        except Exception as e:
            raise PortalError(f"mobile.de: Abruf fehlgeschlagen – {e}")

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

        # Detail-Abruf für E-Autos: Batterie-Status aus der Detailseite extrahieren
        ev_listings = [l for l in results if l.fuel == "elektro" and l.battery_soh is None and l.url]
        if ev_listings:
            self._enrich_battery_from_details(ev_listings, query)

        return results

    def _enrich_battery_from_details(self, listings: List[Listing], query: SearchQuery) -> None:
        """Ruft Detailseiten für E-Autos in einer Batch-Session ab und extrahiert Batterie-Status."""
        try:
            from ..browser import fetch_rendered_batch
        except ImportError:
            return

        from ..models import extract_battery_soh, extract_ev_range_km, extract_battery_kwh
        import logging
        logger = logging.getLogger(__name__)

        # Maximal 10 Detailseiten pro Durchlauf für E-Autos abrufen.
        # WICHTIG: die kanonische Karten-URL (…/fahrzeuge/details.html?id=…)
        # nutzen – die SEO-Form /auto-inserat/car/{id}.html wird von Akamai
        # geblockt ("Zugriff verweigert") und liefert damit nie einen SoH.
        detail_targets = {}
        for l in listings[:10]:
            detail_url = l.url or ""
            if not detail_url or "details.html" not in detail_url:
                lid = l.raw_id
                if not lid and l.url:
                    m = re.search(r"id=(\d+)", l.url)
                    if m:
                        lid = m.group(1)
                if lid:
                    detail_url = f"https://suchen.mobile.de/fahrzeuge/details.html?id={lid}"
            if detail_url:
                detail_targets[detail_url] = l

        if not detail_targets:
            return

        srp_url = self._build_url(query, 1)

        try:
            _, detail_htmls = fetch_rendered_batch(
                srp_url=srp_url,
                detail_urls=list(detail_targets.keys()),
                proxy=self.proxy,
                engine="firefox",
            )
        except Exception as e:
            logger.warning("Batch-Detailabruf fehlgeschlagen: %s", e)
            return

        for d_url, listing in detail_targets.items():
            html = detail_htmls.get(d_url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")
            full_text = soup.get_text(" ", strip=True)

            # SoH aus Detailtext extrahieren
            soh = extract_battery_soh(full_text)
            if soh is not None:
                listing.battery_soh = soh
                logger.info("SoH=%.1f%% aus Detailtext: %s", soh, listing.title[:60])

            # Reichweite aus Detailtext (z.B. "Reichweite (WLTP) 546 km")
            if listing.ev_range_km is None:
                rng = extract_ev_range_km(full_text)
                if rng is not None:
                    listing.ev_range_km = rng

            # kWh aus Detailtext
            if listing.battery_kwh is None:
                kwh = extract_battery_kwh(full_text)
                if kwh is not None:
                    listing.battery_kwh = kwh

            # Detailtext als body speichern
            listing.body = f"{listing.body or ''} {full_text}".strip()[:2000]

            # Zusätzliche Bilder aus Detailseite
            imgs = [img.get("src") or img.get("data-src") for img in soup.select("img[src], img[data-src]")]
            valid_imgs = [u for u in imgs if u and u.startswith("http") and not u.endswith(".svg")]
            existing = listing.image_urls or []
            for img_url in valid_imgs:
                if img_url not in existing:
                    existing.append(img_url)
            listing.image_urls = existing

            # Standort: hat die Karte keine PLZ geliefert, aus der Detailseite
            # (volle Adresse) nachziehen, damit die Entfernung berechnet werden kann.
            if not (listing.location and re.search(r"\b\d{5}\b", listing.location)):
                loc2 = self._extract_location(full_text)
                if loc2 and re.search(r"\b\d{5}\b", loc2):
                    listing.location = loc2

            # Garantie & Standort anreichern
            from ..models import infer_listing_details
            infer_listing_details(listing, getattr(query, "zip_code", None))

            # OCR-Fallback: Zertifikatsbilder (AVILOO, DEKRA etc.) scannen
            if listing.battery_soh is None and listing.image_urls:
                try:
                    from ..battery_analyzer import extract_soh_from_image_urls
                    ocr_soh = extract_soh_from_image_urls(listing.image_urls, max_images=8)
                    if ocr_soh is not None:
                        listing.battery_soh = ocr_soh
                        logger.info("SoH=%.1f%% per Bild-OCR (AVILOO/DEKRA): %s", ocr_soh, listing.title[:60])
                except Exception as e:
                    logger.debug("OCR-Fallback fehlgeschlagen für %s: %s", listing.title[:40], e)

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
            full_card_text = art.get_text(" ", strip=True)
            imgs = [img.get("src") or img.get("data-src") for img in art.select("img[src], img[data-src]")]
            image_urls = [u for u in imgs if u and u.startswith("http") and not u.endswith(".svg")]
            seller_txt = snode.get_text(" ", strip=True) if snode else ""
            # PLZ steht bei mobile.de oft HINTER dem langen Händlernamen – daher
            # untrunkiert aus seller-info und der ganzen Karte suchen.
            loc = self._extract_location(seller_txt, full_card_text)
            l = Listing(
                portal=self.name,
                title=title[:120],
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
