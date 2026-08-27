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
GEAR_MAP = {"schaltgetriebe": "MANUAL_GEAR", "automatik": "AUTOMATIC_GEAR"}
SELLER_MAP = {"haendler": "DEALER", "händler": "DEALER", "privat": "PRIVATE"}
PS_TO_KW = 1.35962


class MobileDe(BasePortal):
    name = "mobile.de"
    BASE = "https://suchen.mobile.de"
    PREFERS_BROWSER = True  # Autarker Playwright Firefox Abruf

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

    # ---- Abruf (primär autark via Playwright Firefox, Fallback auf curl_cffi) ---
    def _fetch(self, url: str) -> str:
        # 1. Primärer Weg: Autarker Playwright Firefox Abruf auf dem Server
        try:
            from ..browser import fetch_rendered
            return fetch_rendered(url, proxy=self.proxy, engine="firefox", wait_until="domcontentloaded", render_delay=1.0)
        except Exception as e:
            # 2. Sekundärer Weg: curl_cffi mit hinterlegten Session-Cookies (falls vorhanden)
            if self.cookies:
                try:
                    from curl_cffi import requests as creq
                    headers = {
                        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
                        "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                        "Cookie": self.cookies,
                        "Referer": "https://www.mobile.de/",
                    }
                    r = creq.get(url, headers=headers, impersonate="chrome124", timeout=25)
                    html = r.text or ""
                    low = html.lower()
                    if not ("behavioral-content" in low or "sec-if-cpt" in low or r.status_code in (403, 429)):
                        return html
                except Exception:
                    pass
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

        # Maximal 8 Detailseiten pro Durchlauf, um Rate-Limits zu vermeiden
        detail_urls = [l.url.split("&searchId")[0].split("&ref=")[0] for l in listings[:8]]
        srp_url = self._build_url(query, 1)

        try:
            _, detail_htmls = fetch_rendered_batch(
                srp_url=srp_url,
                detail_urls=detail_urls,
                proxy=self.proxy,
                engine="firefox",
            )
        except Exception as e:
            logger.warning("Batch-Detailabruf fehlgeschlagen: %s", e)
            return

        for listing in listings[:8]:
            clean_url = listing.url.split("&searchId")[0].split("&ref=")[0]
            html = detail_htmls.get(clean_url)
            if not html:
                continue

            soup = BeautifulSoup(html, "lxml")
            full_text = soup.get_text(" ", strip=True)

            # SoH aus Detailtext extrahieren
            soh = extract_battery_soh(full_text)
            if soh is not None:
                listing.battery_soh = soh
                logger.info("SoH=%.1f%% aus Detailseite: %s", soh, listing.title[:60])

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
                body=full_card_text,
                image_urls=image_urls,
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
