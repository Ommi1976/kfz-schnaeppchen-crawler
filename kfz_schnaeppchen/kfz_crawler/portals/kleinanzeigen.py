"""Kleinanzeigen-Scraper (Rubrik Autos).

Kleinanzeigen liefert serverseitig gerendertes HTML. Wir parsen die
Ergebnis-Artikel direkt. Preis-/km-Angaben stehen im Freitext, daher
werden sie heuristisch extrahiert.
"""

from __future__ import annotations

import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from ..models import Listing, SearchQuery, extract_battery_kwh, extract_ev_range_km
from .base import BasePortal, PortalError

# Wie viele Detailseiten pro Suche höchstens nachgeladen werden (Requests sparen).
# Die Detailabrufe gelten jetzt den gefilterten Treffern, nicht mehr den
# ersten Rohtreffern. Das Budget muss deshalb die uebliche Trefferzahl decken.
DETAIL_LIMIT = 150
# Parallele Detailabrufe (begrenzt, um Kleinanzeigen nicht zu überlasten).
ENRICH_WORKERS = 5


def _fetch_html(url: str, headers: dict, proxy: Optional[str]) -> Optional[str]:
    """Thread-sicherer Einzelabruf mit eigener Session + kleiner Jitter-Pause."""
    time.sleep(random.uniform(0.2, 0.8))
    try:
        with requests.Session() as s:
            if proxy:
                s.proxies.update({"http": proxy, "https": proxy})
            r = s.get(url, headers=headers, timeout=20)
            if r.status_code in (403, 429):
                return None
            return r.text if r.ok else None
    except requests.RequestException:
        return None

_FUEL_NORM = {
    "benzin": "benzin", "diesel": "diesel", "elektro": "elektro",
    "hybrid": "hybrid", "autogas": "lpg", "lpg": "lpg",
    "erdgas": "cng", "cng": "cng",
}
_GEAR_NORM = {"manuell": "schaltgetriebe", "schaltgetriebe": "schaltgetriebe",
             "automatik": "automatik"}


_EXPANSION_EV_MODELS = [
    "vw id 3", "vw id 4", "vw id 5", "cupra born", "skoda enyaq",
    "tesla model 3", "tesla model y", "hyundai ioniq 5", "hyundai ioniq 6",
    "kia ev6", "renault megane e tech", "hyundai kona elektro", "kia niro ev",
    "smart 1", "audi q4 e tron", "mercedes eqa", "mercedes eqb", "bmw i4", "bmw ix3"
]


def _clean_slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", s).strip("-")


class Kleinanzeigen(BasePortal):
    name = "Kleinanzeigen"
    BASE = "https://www.kleinanzeigen.de"

    def _build_url(self, query: SearchQuery, page: int, custom_term: Optional[str] = None) -> str:
        # Native Kleinanzeigen Attribute im k0-Segment
        attr_parts = ["c216"]
        if query.year_from:
            attr_parts.append(f"autos.ez_i:{query.year_from},")
        if query.mileage_to:
            attr_parts.append(f"autos.km_i:,{query.mileage_to}")
        if not custom_term and not (query.make or query.model):
            if query.fuel == "diesel":
                attr_parts.append("autos.kraftstoff_s:diesel")
            elif query.fuel == "benzin":
                attr_parts.append("autos.kraftstoff_s:benzin")
            elif query.fuel == "hybrid":
                attr_parts.append("autos.kraftstoff_s:hybrid")

        if query.transmission == "automatik":
            attr_parts.append("autos.getriebe_s:automatik")
        elif query.transmission == "schaltgetriebe":
            attr_parts.append("autos.getriebe_s:manuell")

        attr_seg = "+".join(attr_parts)

        # Suchbegriff
        if custom_term:
            term = _clean_slug(custom_term)
        else:
            term_parts = [p for p in (query.make, query.model) if p]
            if not term_parts and getattr(query, "keywords", None):
                term_parts = list(query.keywords)
            term = _clean_slug("-".join(term_parts)) if term_parts else "auto"

        loc_seg = f"/{query.zip_code}" if query.zip_code else ""
        price_seg = ""
        if query.price_from or query.price_to:
            price_seg = f"/preis:{query.price_from or ''}:{query.price_to or ''}"
        page_seg = f"/seite:{page}" if page > 1 else ""
        qs = f"?radius={query.radius_km}" if (query.zip_code and query.radius_km) else ""

        return f"{self.BASE}/s-autos{loc_seg}{price_seg}{page_seg}/{term}/k0{attr_seg}{qs}"

    def search(self, query: SearchQuery) -> List[Listing]:
        results: List[Listing] = []
        seen_urls = set()

        # Bestimme Suchbegriffe (entweder explizit oder via Modell-Expansion für allgemeine E-Auto-Suchen)
        has_specific_car = bool(query.make or query.model or getattr(query, "keywords", None))

        if not has_specific_car and query.fuel == "elektro" and (query.battery_from_kwh or query.ev_range_from or query.power_from):
            terms: List[Optional[str]] = list(_EXPANSION_EV_MODELS)
            pages_per_term = 3
        else:
            terms = [None]
            pages_per_term = max(self.max_pages, 20)

        for term in terms:
            for page in range(1, pages_per_term + 1):
                url = self._build_url(query, page, custom_term=term)
                resp = self._get(url)
                if not resp or not resp.text:
                    break
                items = self._parse(resp.text, query)
                if not items:
                    break
                for it in items:
                    if it.url not in seen_urls:
                        seen_urls.add(it.url)
                        results.append(it)

        return results

    # Preis am Euro-Zeichen erkennen; "5.389 km" darf nicht als Preis gelten.
    # Kein \b hinter dem Euro-Zeichen: € ist selbst kein Wortzeichen, eine
    # Wortgrenze kann dort nie entstehen ("999 € VB" waere nie erkannt worden).
    _PREIS_RE = re.compile(r"([\d][\d.]{1,9})\s*(?:€|EUR\b)", re.I)
    # Standort: PLZ mit nachfolgendem Ortsnamen.
    _ORT_RE = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\- ]{2,38})")

    def _artikel(self, soup) -> List:
        """Anzeigenblöcke finden – klassenunabhängig.

        Kleinanzeigen ist auf Utility-Klassen (Tailwind) umgestellt; die alten
        Selektoren ``article.aditem`` und ``.aditem-main--*`` existieren nicht
        mehr. Solche Klassennamen ändern sich beim nächsten Umbau wieder,
        deshalb wird über die Struktur gegangen: ein Anzeigenblock ist ein
        ``article`` mit einem Link auf ``/s-anzeige/``.
        """
        alt_stil = soup.select("article.aditem")
        if alt_stil:
            return alt_stil
        return [a for a in soup.select("article") if a.select_one('a[href*="/s-anzeige/"]')]

    def _parse(self, html: str, query: SearchQuery) -> List[Listing]:
        soup = BeautifulSoup(html, "lxml")
        listings: List[Listing] = []
        for art in self._artikel(soup):
            a = art.select_one('a[href*="/s-anzeige/"]') or art.select_one("a[href]")
            if not a or not a.get("href"):
                continue
            href = a["href"]
            url = href if href.startswith("http") else self.BASE + href

            ueberschrift = art.find(["h2", "h3"])
            title = (self._text(art, ".text-module-begin, h2 a, .aditem-main--middle--title")
                     or (ueberschrift.get_text(" ", strip=True) if ueberschrift else None)
                     or a.get_text(" ", strip=True))

            volltext = art.get_text(" ", strip=True)
            price = self._to_int(self._text(art, ".aditem-main--middle--price-shipping--price"))
            if price is None:
                treffer = self._PREIS_RE.search(volltext)
                price = self._to_int(treffer.group(1)) if treffer else None
            # Ohne Preis ist es meist keine Fahrzeuganzeige.
            if price is None:
                continue
            # Kleinanzeigen mischt Gesuche unter die Angebote – Händler, die
            # Autos ankaufen wollen. Der Preis dort ist keine Kaufgelegenheit.
            if "gesuch" in volltext.lower():
                continue

            location = self._text(art, ".aditem-main--top--left")
            if not location:
                # Der Block lautet "<n> TOP <PLZ> <Ort> <Titel> …". Der Ort
                # endet dort, wo der (separat bekannte) Titel beginnt – sonst
                # zieht das Muster die ersten Titelwoerter mit hinein.
                kopf = volltext
                if title and title[:18] in volltext:
                    kopf = volltext[:volltext.index(title[:18])]
                ort = self._ORT_RE.search(kopf) or self._ORT_RE.search(volltext)
                location = f"{ort.group(1)} {self._ort_saeubern(ort.group(2))}" if ort else None

            desc = self._text(art, ".aditem-main--middle--description") or ""
            listing_text = (desc + " " + volltext).strip()
            imgs = [img.get("src") or img.get("data-src") or img.get("data-imgsrc")
                    for img in art.select("img")]
            image_urls = [u for u in imgs if u and u.startswith("http") and not u.endswith(".svg")]

            listing = Listing(
                portal=self.name,
                title=(title or "Kleinanzeigen-Inserat")[:120],
                url=url,
                price=price,
                fuel=self._extract_fuel(listing_text),
                mileage=self._extract_km(listing_text),
                year=self._extract_year(listing_text),
                ev_range_km=extract_ev_range_km(listing_text),
                location=location,
                body=listing_text,
                image_urls=image_urls,
                raw_id=art.get("data-adid") or self._anzeigen_id(url),
            )
            from ..models import infer_listing_battery, infer_listing_details, infer_listing_range
            infer_listing_battery(listing, check_images=False)
            infer_listing_range(listing)
            infer_listing_details(listing, getattr(query, "zip_code", None))
            if self._matches(listing, query):
                listings.append(listing)
        return listings

    # Hinter dem Ort steht das Einstelldatum ("Heute", "Gestern", "12.03.2026").
    _DATUM_SUFFIX = re.compile(
        r"\s+(heute|gestern|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|\d{1,2}\.\d{1,2}\.).*$",
        re.I,
    )

    @classmethod
    def _ort_saeubern(cls, ort: str) -> str:
        return cls._DATUM_SUFFIX.sub("", (ort or "").strip()).strip()

    @staticmethod
    def _anzeigen_id(url: str) -> Optional[str]:
        """Anzeigen-ID aus der URL (…/3499617433-216-364)."""
        m = re.search(r"/(\d{8,})-\d+-\d+", url or "")
        return m.group(1) if m else None

    def _matches(self, l: Listing, q: SearchQuery) -> bool:
        # Preis-Filter clientseitig nachziehen (Freitext-Suche ist unscharf).
        if q.price_to and l.price and l.price > q.price_to:
            return False
        if q.price_from and l.price and l.price < q.price_from:
            return False
        if q.mileage_to and l.mileage and l.mileage > q.mileage_to:
            return False
        return True

    # ---- Detailseiten-Anreicherung (Homogenisierung) ------------------
    def enrich(self, listings: List[Listing], query: SearchQuery,
               force: bool = False) -> List[Listing]:
        """Lädt Detailseiten nach, um Kraftstoff/Getriebe/Leistung/EZ/km/Türen/Akku
        strukturiert zu ermitteln – damit der gemeinsame Filtersatz auch bei
        Kleinanzeigen greift (die Trefferliste liefert diese Felder nicht).
        """
        needs = force or any([query.fuel, query.transmission, query.power_from,
                              query.power_to, query.doors, query.battery_from_kwh, query.ev_range_from])
        if not needs:
            return listings
        targets = listings[:DETAIL_LIMIT]

        def work(l: Listing) -> None:
            html = _fetch_html(l.url, self._headers(), self.proxy)
            if html:
                self._parse_detail(html, l)

        # Parallele Detailabrufe mit begrenzter Nebenläufigkeit (eigene Session
        # pro Thread; höflich gedrosselt, damit Kleinanzeigen nicht überlastet wird).
        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as ex:
            list(ex.map(work, targets))
        return listings

    def _parse_detail(self, html: str, l: Listing) -> None:
        soup = BeautifulSoup(html, "lxml")
        # Nur die Anzeige selbst lesen, nicht die ganze Seite. Der Gesamttext
        # umfasst rund 13.500 Zeichen, die Anzeige davon nur 2.300 - der Rest
        # sind fremde Inserate ("Ähnliche Anzeigen"). Daraus wurden zuvor
        # "Motorschaden" oder "suche Tesla, auch beschädigt" als Zustand
        # DIESES Fahrzeugs gelesen: sieben Fehlalarme in zwölf Stichproben.
        #
        # Findet sich keiner der Bereiche, wird lieber nichts uebernommen als
        # fremder Text.
        teile = []
        for auswahl in ("#viewad-description-text", "#viewad-details"):
            bereich = soup.select_one(auswahl)
            if bereich:
                teile.append(bereich.get_text(" ", strip=True))
        if teile:
            l.body = " ".join(part for part in ([l.body] + teile) if part)
        # Bezugstext fuer Akku und Reichweite: die Anzeige selbst, sonst faellt
        # ein "58 kWh" aus einem fremden Inserat auf dieses Fahrzeug zurueck.
        full_text = " ".join(teile) if teile else (l.body or "")
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
                m_ps = re.search(r"(\d+)\s*(?:ps|hp)", value, re.I)
                m_kw = re.search(r"(\d+)\s*kw", value, re.I)
                if m_ps:
                    l.power_ps = int(m_ps.group(1))
                elif m_kw:
                    l.power_ps = round(int(m_kw.group(1)) * 1.35962)
                else:
                    l.power_ps = self._to_int(value) or l.power_ps
            elif "getriebe" in key:
                for token, norm in _GEAR_NORM.items():
                    if token in v:
                        l.transmission = norm
                        break
            elif "fahrzeugzustand" in key:
                # Zustand in body ablegen -> Betrugsfilter (#5) kann greifen.
                l.body = (l.body + " " if l.body else "") + value

        # Akku und Reichweite
        kwh = extract_battery_kwh(full_text)
        if kwh is not None:
            l.battery_kwh = kwh
        rng = extract_ev_range_km(full_text)
        if rng is not None:
            l.ev_range_km = rng

        from ..models import infer_listing_battery, infer_listing_range
        infer_listing_battery(l, check_images=False)
        infer_listing_range(l)

    # ---- Heuristiken --------------------------------------------------
    @staticmethod
    def _extract_km(text: str) -> Optional[int]:
        m = re.search(r"([\d\.]{4,})\s*km", text, re.IGNORECASE)
        if m:
            return int(re.sub(r"[^\d]", "", m.group(1)))
        return None

    # Motorkennungen des deutschen Marktes. Wichtig ist die Stellung des "i":
    # "740i" ist ein Benziner, "i4" ein Elektroauto von BMW.
    _VERBRENNER_RE = re.compile(
        r"\b(?:diesel|benzin(?:er)?|ottomotor|hubraum|zylinder"
        r"|tdi|tsi|tfsi|gti|gtd|fsi|hdi|dci|cdi|crdi|jtd|multijet|bluetec|ecoboost|cdti|tdci|bluehdi|d-?4-?d|dtec|blueefficiency"
        r"|\d{3}\s?[id]\b|\d\.\d\s?(?:l|liter|tdi|tsi))\b",
        re.I,
    )
    _ELEKTRO_RE = re.compile(
        r"\b(?:elektro|vollelektrisch|e-?auto|\bbev\b|kwh|elektromotor"
        r"|stromer|akkukapazit[äa]t|ladeleistung)\b",
        re.I,
    )
    _HYBRID_RE = re.compile(r"\b(?:hybrid|phev|plug-?in)\b", re.I)
    # Modellkennungen, die den Antrieb nicht ausschreiben. Der Lookbehind
    # verhindert Treffer mitten in einer Motorkennung: bei "320i" steht vor
    # dem "i" eine Ziffer, bei "BMW i4" ein Leerzeichen.
    _EV_MODELL_RE = re.compile(
        r"(?<![\w-])(?:"
        r"id\.?\s?[34578]"
        r"|i[3457]\b|ix[13]?\b"
        r"|eq[abcesv]?\b(?!\s*boost)"
        r"|e-?tron|edrive"
        r"|ioniq\s?[56]\b|ev6\b|niro\s?ev\b|kona\s?elektro"
        r"|zoe\b|leaf\b|born\b|enyaq|e-?golf|e-?up\b|e-?c4\b"
        r"|model\s?[3ysx]\b"
        r")",
        re.I,
    )

    @classmethod
    def _extract_fuel(cls, text: str) -> Optional[str]:
        """Kraftstoffart aus dem Inseratstext lesen.

        Vorher wurde hier die *gesuchte* Art eingetragen – jedes Inserat galt
        damit als passend, und der Kraftstofffilter war wirkungslos. So kam ein
        BMW 740i (Benziner) in eine Elektro-Suche.

        Bei Widerspruch gewinnt der Verbrenner-Hinweis: "Hybrid" und "Elektro"
        stehen oft nur als Suchbegriff im Text, eine Motorkennung wie 740i oder
        TDI ist dagegen eindeutig.
        """
        if not text:
            return None
        if cls._HYBRID_RE.search(text):
            return "hybrid"
        if cls._VERBRENNER_RE.search(text):
            return "diesel" if re.search(r"\b(?:diesel|tdi|hdi|dci|cdi|crdi|cdti|tdci|bluehdi|d-?4-?d|dtec|\d{3}\s?d)\b",
                                         text, re.I) else "benzin"
        if cls._ELEKTRO_RE.search(text) or cls._EV_MODELL_RE.search(text):
            return "elektro"
        return None

    @staticmethod
    def _extract_year(text: str) -> Optional[int]:
        """Baujahr nur aus einer ausdrücklichen Zulassungsangabe.

        Die erste Jahreszahl im Text zu nehmen ging schief: Der Kartentext
        enthält auch das Einstelldatum, weshalb reihenweise Inserate als
        Baujahr das laufende Jahr trugen – selbst Zubehörangebote. Ein
        falsches Baujahr ist schlechter als keines: es verfälscht den Filter
        und lässt Teileangebote als Fahrzeuge durchgehen.
        """
        from ..models import extract_first_registration

        jahr, _monat, art = extract_first_registration(text)
        return jahr if art in ("ez", "modelljahr") else None

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
