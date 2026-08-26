"""mobile.de-Scraper (browserlos via curl_cffi + importierte Session-Cookies).

mobile.de ist durch Akamai Bot Manager geschützt. Weder reine requests noch
ein (auch headless) Browser kommen an echte Daten – Akamai weist automatisierte
Browser ab. Funktionierender Weg OHNE Browser im Crawler: die Session-Cookies
aus einem echten, eingeloggten Browser importieren und mit curl_cffi (imitiert
den Chrome-TLS-Fingerprint) wiederverwenden. Laufen die Cookies ab, meldet der
Scraper CookiesExpired und die Oberfläche fordert zum Aktualisieren auf.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional
from urllib.parse import quote_plus

from ..models import Listing, SearchQuery
from .base import BasePortal, CookiesExpired, PortalError

FUEL_MAP = {"benzin": "PETROL", "diesel": "DIESEL", "elektro": "ELECTRICITY", "hybrid": "HYBRID"}


class MobileDe(BasePortal):
    name = "mobile.de"
    BASE = "https://suchen.mobile.de"
    PREFERS_BROWSER = False  # kein Browser – curl_cffi + importierte Cookies

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
        span("pw", query.power_from, query.power_to)
        if query.ev_range_from:
            params.append(f"re={max(50, (query.ev_range_from // 100) * 100)}")
        if query.fuel and query.fuel in FUEL_MAP and query.fuel != "elektro":
            params.append(f"fu={FUEL_MAP[query.fuel]}")
        elif query.fuel == "elektro":
            params.append("fu=ELECTRICITY")
        term = " ".join(p for p in (query.make, query.model) if p)
        if term:
            params.append(f"q={quote_plus(term)}")
        return f"{self.BASE}/fahrzeuge/search.html?{'&'.join(params)}"

    # ---- Abruf --------------------------------------------------------
    def _fetch(self, url: str) -> str:
        if not self.cookies:
            raise CookiesExpired("mobile.de: keine Cookies hinterlegt.")
        try:
            from curl_cffi import requests as creq
        except ImportError:
            raise PortalError("mobile.de: curl_cffi nicht installiert.")
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            "Cookie": self.cookies,
            "Referer": "https://www.mobile.de/",
        }
        try:
            r = creq.get(url, headers=headers, impersonate="chrome124", timeout=25)
        except Exception as e:  # Netzwerk-/curl-Fehler
            raise PortalError(f"mobile.de: Abruf fehlgeschlagen ({e}).")
        html = r.text or ""
        low = html.lower()
        if ("behavioral-content" in low or "sec-if-cpt" in low
                or "zugriff verweigert" in low or "access denied" in low
                or r.status_code in (403, 429)):
            raise CookiesExpired(
                "mobile.de: Cookies abgelaufen oder ungültig – bitte im Browser "
                "neu einloggen und Cookies aktualisieren."
            )
        return html

    def search(self, query: SearchQuery) -> List[Listing]:
        results: List[Listing] = []
        seen_ids = set()
        for page in range(1, self.max_pages + 1):
            html = self._fetch(self._build_url(query, page))
            items = self._collect_from_state(html)
            new = 0
            for it in items:
                lid = it.get("id")
                if lid in seen_ids:
                    continue
                seen_ids.add(lid)
                listing = self._to_listing(it)
                if listing:
                    results.append(listing)
                    new += 1
            if new == 0:
                break
        return results

    # ---- State-Parsing ------------------------------------------------
    @staticmethod
    def _extract_state(html: str) -> Optional[dict]:
        i = html.find("window.__INITIAL_STATE__")
        if i < 0:
            return None
        start = html.find("{", i)
        depth = 0
        instr = False
        esc = False
        for j in range(start, len(html)):
            c = html[j]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(html[start:j + 1])
                        except json.JSONDecodeError:
                            return None
        return None

    def _collect_from_state(self, html: str) -> List[dict]:
        state = self._extract_state(html)
        if not state:
            return []
        found: dict = {}

        def walk(o):
            if isinstance(o, list):
                for x in o:
                    if (isinstance(x, dict) and isinstance(x.get("price"), dict)
                            and x.get("make") and x.get("url") and x.get("id") is not None):
                        found[x["id"]] = x
                    else:
                        walk(x)
            elif isinstance(o, dict):
                for v in o.values():
                    walk(v)

        walk(state)
        return list(found.values())

    def _to_listing(self, it: dict) -> Optional[Listing]:
        attr = it.get("attr") or {}
        make = (it.get("make") or {}).get("localized") or ""
        model = (it.get("model") or {}).get("localized") or ""
        title = f"{make} {model}".strip() or "mobile.de-Inserat"
        price = (((it.get("price") or {}).get("grs") or {}).get("amount"))
        url = it.get("url") or ""
        return Listing(
            portal=self.name,
            title=title[:120],
            url=url,
            price=int(price) if isinstance(price, (int, float)) else self._to_int(price),
            year=self._year_from_attr(attr),
            mileage=self._to_int(attr.get("ml")),
            fuel=self._norm_fuel(attr.get("ft")),
            power_ps=self._to_int(attr.get("pw")),
            transmission=self._norm_gear(attr.get("tr")),
            battery_kwh=self._to_float(attr.get("bc")),
            location=attr.get("loc"),
            raw_id=str(it.get("id")),
        )

    # ---- Feld-Helfer --------------------------------------------------
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
        m = re.search(r"\d+(?:[.,]\d+)?", str(value))
        return float(m.group(0).replace(",", ".")) if m else None

    @staticmethod
    def _year_from_attr(attr: dict) -> Optional[int]:
        fr = attr.get("fr") or attr.get("ez") or ""
        m = re.search(r"(19|20)\d{2}", str(fr))
        if m:
            return int(m.group(0))
        # Neuwagen ohne EZ -> None
        return None

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
