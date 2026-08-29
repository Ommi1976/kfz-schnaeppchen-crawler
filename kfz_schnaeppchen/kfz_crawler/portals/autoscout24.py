"""AutoScout24-Scraper.

AutoScout24 rendert die Ergebnisliste serverseitig und bettet die Daten
zusätzlich als JSON im <script id="__NEXT_DATA__"> ein. Wir bevorzugen das
JSON (stabiler als HTML-Selektoren) und fallen bei Bedarf auf HTML zurück.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery, extract_ev_range_km
from .base import BasePortal, PortalError

FUEL_MAP = {
    "benzin": "B", "diesel": "D", "elektro": "E", "hybrid": "2",
    "lpg": "L", "cng": "C",
}
GEAR_MAP = {"schaltgetriebe": "M", "automatik": "A"}
SELLER_MAP = {"haendler": "D", "händler": "D", "privat": "P"}
PS_TO_KW = 1.35962

from .as24_taxonomy import BODY_TYPE_TO_AS24, DOORS_TO_AS24, VALID_EQUIPMENT_IDS

# Schadstoffklasse-Slug -> AutoScout24 emclass-ID
EMCLASS_MAP = {"euro4": 4, "euro5": 5, "euro6": 6, "euro6d": 8, "euro6e": 10}
# Antrieb-Slug -> AutoScout24 drivetrain-Wert
DRIVETRAIN_MAP = {"allrad": "4", "front": "F", "heck": "R"}


class AutoScout24(BasePortal):
    name = "AutoScout24"
    BASE = "https://www.autoscout24.de"

    def _build_url(self, query: SearchQuery, page: int) -> str:
        # Pfad nur mit vorhandenen Segmenten bauen: ohne Marke -> /lst,
        # sonst /lst/<marke>[/<modell>]. "/lst/-/-" liefert 404.
        if query.make:
            path = f"/lst/{query.make}" + (f"/{query.model}" if query.model else "")
        else:
            path = "/lst"
        # Neueste zuerst (repräsentative Stichprobe). Eine Preis-Sortierung würde
        # den Marktpreis aus den billigsten Autos schätzen und fast alle
        # Schnäppchen verschlucken.
        params = ["sort=age", "desc=1", f"page={page}", "size=20"]
        if query.price_from:
            params.append(f"pricefrom={query.price_from}")
        if query.price_to:
            params.append(f"priceto={query.price_to}")
        if query.year_from:
            params.append(f"fregfrom={query.year_from}")
        if query.year_to:
            params.append(f"fregto={query.year_to}")
        if query.mileage_from:
            params.append(f"kmfrom={query.mileage_from}")
        if query.mileage_to:
            params.append(f"kmto={query.mileage_to}")
        if query.fuel and query.fuel in FUEL_MAP:
            params.append(f"fuel={FUEL_MAP[query.fuel]}")
        if query.transmission and query.transmission in GEAR_MAP:
            params.append(f"gear={GEAR_MAP[query.transmission]}")
        if query.seller and query.seller in SELLER_MAP:
            params.append(f"customertype={SELLER_MAP[query.seller]}")
        # Leistung: AutoScout24 filtert in kW -> aus PS umrechnen.
        if query.power_from:
            params.append(f"powertype=kw&powerfrom={round(query.power_from / PS_TO_KW)}")
        if query.power_to:
            params.append(f"powertype=kw&powerto={round(query.power_to / PS_TO_KW)}")
        # Karosserie (body=<id>)
        body_id = BODY_TYPE_TO_AS24.get(query.body_type)
        if body_id:
            params.append(f"body={body_id}")
        # Türen (doorfrom/doorto)
        doors = DOORS_TO_AS24.get(query.doors)
        if doors:
            params.append(f"doorfrom={doors[0]}&doorto={doors[1]}")
        # E-Reichweite (erange) – server-seitig statt nur Nachfilter
        if query.ev_range_from:
            params.append(f"erange={query.ev_range_from}")
        # Schadstoffklasse (emclass=<id>)
        emc = EMCLASS_MAP.get(query.emission_class)
        if emc:
            params.append(f"emclass={emc}")
        # Antrieb (drivetrain: Allrad=4, Front=F, Heck=R)
        dt = DRIVETRAIN_MAP.get(query.drivetrain)
        if dt:
            params.append(f"drivetrain={dt}")
        # Unfallwagen: AutoScout24 schließt beschädigte standardmäßig aus.
        # Nur wenn ausdrücklich gewünscht, wieder einschließen.
        if query.include_damaged:
            params.append("damaged_listing=include")
        # Standort & Umkreis (zip / zipradius)
        if query.zip_code:
            params.append(f"zip={query.zip_code}")
            if query.radius_km:
                params.append(f"zipradius={query.radius_km}")
        # Land / Region (cy)
        country = (query.country or "DE").strip().upper()
        if country == "ALL":
            pass  # Europaweit / alle Länder
        elif country in ("DE", "D"):
            params.append("cy=D")
        elif country in ("AT", "A"):
            params.append("cy=A")
        elif country in ("CH"):
            params.append("cy=CH")
        elif country in ("FR", "F"):
            params.append("cy=F")
        elif country in ("IT", "I"):
            params.append("cy=I")
        elif country in ("NL"):
            params.append("cy=NL")
        elif country in ("BE", "B"):
            params.append("cy=B")
        elif country in ("ES", "E"):
            params.append("cy=E")
        elif country in ("PL"):
            params.append("cy=PL")
        elif country in ("LU", "L"):
            params.append("cy=L")
        else:
            params.append(f"cy={country}")
        # Ausstattung (eq=<id>,<id>,…)
        eq = [str(i) for i in (query.equipment or []) if i in VALID_EQUIPMENT_IDS]
        if eq:
            params.append("eq=" + ",".join(eq))
        qs = "&".join(params)
        return f"{self.BASE}{path}?{qs}"

    def search(self, query: SearchQuery) -> List[Listing]:
        results: List[Listing] = []
        max_limit = min(max(self.max_pages, 10), 20)  # Standard: 10 Seiten (~200 Neueste Inserate), max. 20
        for page in range(1, max_limit + 1):
            url = self._build_url(query, page)
            try:
                resp = self._get(url)
            except Exception as e:
                logger.warning("AutoScout24: Fehler beim Abruf von Seite %d: %s", page, e)
                break
            page_items = self._parse(resp.text)
            if not page_items:
                break
            results.extend(page_items)
        return results

    def _parse(self, html: str) -> List[Listing]:
        soup = BeautifulSoup(html, "lxml")
        node = soup.find("script", id="__NEXT_DATA__")
        if node and node.string:
            try:
                return self._parse_next_data(json.loads(node.string))
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        return self._parse_html(soup)

    def _parse_next_data(self, data: dict) -> List[Listing]:
        listings: List[Listing] = []
        # Der Pfad kann sich ändern – wir suchen robust nach der Ergebnisliste.
        props = data.get("props", {}).get("pageProps", {})
        items = (
            props.get("listings")
            or props.get("numberOfResults") and props.get("searchResults")
            or []
        )
        if isinstance(items, dict):
            items = items.get("listings", [])
        for it in items or []:
            try:
                v = it.get("vehicle") or {}
                tr = it.get("tracking") or {}
                price = self._to_int((it.get("price") or {}).get("priceRaw") or tr.get("price"))
                # Leistung: bevorzugt kW aus Tracking, in PS umrechnen.
                kw = self._to_int(tr.get("powerInKw") or v.get("rawPowerInKw"))
                power_ps = round(kw * PS_TO_KW) if kw else self._to_int(tr.get("powerInHp"))
                detail_text = " ".join(
                    str(d.get("data", "")) for d in (it.get("vehicleDetails") or [])
                    if isinstance(d, dict)
                )
                first_registration = (
                    tr.get("firstRegistration")
                    or v.get("firstRegistrationDate")
                    or v.get("firstRegistration")
                    or ""
                )
                loc = it.get("location") or {}
                loc_str = f"{loc.get('zip', '')} {loc.get('city', '')}".strip() or loc.get("city")
                l = Listing(
                    portal=self.name,
                    title=self._title(it),
                    url=self._url(it),
                    price=price,
                    year=self._extract_year(first_registration),
                    mileage=self._to_int(tr.get("mileage")),
                    fuel=v.get("fuelType"),
                    location=loc_str,
                    transmission=self._norm_gear(v.get("transmissionType") or tr.get("transmission")),
                    power_ps=power_ps,
                    body=f"{v.get('bodyType') or ''} {detail_text}".strip() or None,
                    ev_range_km=extract_ev_range_km(detail_text),
                    image_urls=[img for img in it.get("images", []) if isinstance(img, str)],
                    raw_id=str(it.get("id") or ""),
                )
                from ..models import infer_listing_details
                infer_listing_details(l)
                listings.append(l)
            except Exception:
                continue
        return listings

    @staticmethod
    def _norm_gear(value) -> Optional[str]:
        if not value:
            return None
        v = str(value).lower()
        if "auto" in v or v == "a":
            return "automatik"
        if "man" in v or "schalt" in v or v == "m":
            return "schaltgetriebe"
        return None

    def _parse_html(self, soup: BeautifulSoup) -> List[Listing]:
        listings: List[Listing] = []
        for art in soup.select("article[data-guid], article.cldt-summary-full-item"):
            a = art.select_one("a[href*='/angebote/']") or art.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            url = href if href.startswith("http") else self.BASE + href
            title = a.get_text(strip=True) or "AutoScout24-Inserat"
            price = self._to_int(self._text(art, "[data-testid='regular-price'], .cldt-price"))
            listings.append(
                Listing(
                    portal=self.name,
                    title=title[:120],
                    url=url,
                    price=price,
                    raw_id=art.get("data-guid"),
                )
            )
        return listings

    # ---- kleine Helfer ------------------------------------------------
    @staticmethod
    def _title(it: dict) -> str:
        v = it.get("vehicle") or {}
        make = v.get("make") or ""
        model = v.get("model") or ""
        sub = v.get("modelVersionInput") or v.get("subtitle") or ""
        return f"{make} {model} {sub}".strip()[:120] or "AutoScout24-Inserat"

    def _url(self, it: dict) -> str:
        url = it.get("url") or ""
        if url and not url.startswith("http"):
            url = self.BASE + url
        return url or self.BASE

    @staticmethod
    def _extract_year(value) -> Optional[int]:
        """Erstzulassungsjahr aus ISO-Datum oder MM-JJJJ lesen."""
        if not value:
            return None
        match = re.search(r"\b(19\d{2}|20\d{2})\b", str(value))
        return int(match.group(1)) if match else None

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
