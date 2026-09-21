"""mobile.de: verified filters, resumable search and HA-owned browser session."""

from __future__ import annotations

import logging
import hashlib
import json
import re
import time
from dataclasses import replace
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
    PREFERS_BROWSER = True
    # Work budgets, NOT a promise that the provider will accept this volume.
    PAGE_BUDGET = 12
    FULL_CRAWL_MAX_PAGES = 40
    DELTA_PAGES = 2
    FULL_INTERVAL = 24 * 3600

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = None
        self.complete_sweep = False
        self.active_fingerprints = set()
        self.coverage = {}
        self.checkpoint_key = ""
        self.pending_checkpoint = None

    # ---- URL ----------------------------------------------------------
    def _build_url(self, query: SearchQuery, page: int) -> str:
        params = ["isSearchRequest=true", "s=Car", "vc=Car", f"pageNumber={page}",
                  "sb=doc", "od=down"]

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
        # Verified against the public search UI; 'bat' is silently ignored.
        if query.battery_from_kwh:
            params.append(f"bc={query.battery_from_kwh:g}")
        if query.ev_range_from:
            steps = (50, 100, 200, 300, 400, 500, 600)
            lower = max((v for v in steps if v <= query.ev_range_from), default=0)
            if lower:
                params.append(f"re={lower}")
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
                key = "spc" if eq_id in (38, 133) else "fe"
                # ACC includes cruise control; never send competing radio values.
                if eq_id == 38 and 133 in query.equipment:
                    continue
                params.append(f"{key}={EQUIPMENT_TO_MOBILE_DE[eq_id]}")
        term = " ".join(p for p in (query.make, query.model) if p)
        if term:
            params.append(f"q={quote_plus(term)}")
        return f"{self.BASE}/fahrzeuge/search.html?{'&'.join(params)}"

    # ---- One shared, persistent browser for search and detail pages ----------
    def _fetch(self, url: str) -> str:
        from ..mobile_runtime import mobile_browser
        return mobile_browser().fetch(url, store=self.store, proxy=self.proxy)

    def search(self, query: SearchQuery) -> List[Listing]:
        return self._crawl_pages(query, self._fetch)

    def _publish_progress(self):
        progress = getattr(self, "progress", None)
        if progress is not None:
            progress.coverage(self.coverage)

    def _crawl_pages(self, query: SearchQuery, fetcher) -> List[Listing]:
        from ..mobile_runtime import load_state, search_page_info, MobileDeferred
        from ..browser import BrowserBlocked
        results: List[Listing] = []
        seen_ids = set()
        self.complete_sweep = False
        self.active_fingerprints = set()
        self.pending_checkpoint = None
        # Including all local filters invalidates old cursors after a UI edit.
        digest = hashlib.sha256(json.dumps(query.to_dict(), sort_keys=True).encode()).hexdigest()[:24]
        key = self.checkpoint_key = f"mobile.search.v3.{digest}"
        state = load_state(self.store, key)
        variants = [query]
        if query.unknown_policy != "strict" and query.battery_from_kwh and query.ev_range_from:
            variants.append(replace(query, battery_from_kwh=None))
        now = time.time()
        delta = bool(state.get("completed_at") and now - state["completed_at"] < self.FULL_INTERVAL)
        if not delta and state.get("plan"):
            variants = [SearchQuery.from_dict(spec) for spec in state["plan"]]
        if delta:
            start_variant, start_page = 0, 1
            active = set()
        else:
            start_variant = int(state.get("variant", 0))
            start_page = int(state.get("page", 1))
            active = set(state.get("active", []))
        self.coverage = {"mode": "delta" if delta else "full", "pages": 0,
                         "complete": False, "reason": "budget", "unique_seen": len(active),
                         "run_unique_seen": 0, "provider_reported_counts": []}
        reports = {}
        self._publish_progress()
        budget = int(getattr(self, "page_budget", 0) or self.PAGE_BUDGET)
        max_limit = max(1, min(budget, self.FULL_CRAWL_MAX_PAGES))
        used = 0
        def checkpoint(variant, page):
            # The caller commits this ONLY AFTER storing the returned listings.
            # A crash during the run therefore replays its pages, not skips them.
            self.pending_checkpoint = {"variant": variant, "page": page,
                "active": sorted(active), "plan": [v.to_dict() for v in variants]}

        variant_index = start_variant
        while variant_index < len(variants):
            page = start_page if variant_index == start_variant else 1
            previous_ids = None
            variant_pages = 0
            while used < max_limit and (not delta or variant_pages < self.DELTA_PAGES):
                self.coverage.update(variant=variant_index + 1, next_page=page)
                self._publish_progress()
                if not delta:
                    checkpoint(variant_index, page)
                try:
                    html = fetcher(self._build_url(variants[variant_index], page))
                except Exception as exc:
                    self.coverage["reason"] = "blocked" if isinstance(exc, BrowserBlocked) else "deferred" if isinstance(exc, MobileDeferred) else "error"
                    self._publish_progress()
                    if results:
                        raise PortalPartialError(f"mobile.de: Teilabruf, Seite {page}: {exc}", results, page) from exc
                    if isinstance(exc, MobileDeferred):
                        raise
                    raise PortalError(f"mobile.de: Seite {page} nicht vollständig: {exc}") from exc
                used += 1
                variant_pages += 1
                self.coverage["pages"] = used
                self._publish_progress()  # A fetched page counts even if its parser fails.
                cards = self._parse_cards(html)
                info = search_page_info(html)
                variant = variants[variant_index]
                variant_id = hashlib.sha256(json.dumps(variant.to_dict(), sort_keys=True).encode()).hexdigest()[:24]
                if info["total"] is not None:
                    reports[variant_id] = {
                        "variant_id": variant_id, "variant": variant_index + 1,
                        "battery_from_kwh": variant.battery_from_kwh,
                        "price_from": variant.price_from, "price_to": variant.price_to,
                        "reported_total": info["total"], "page": page,
                        "refreshed_at": time.time(),
                    }
                    self.coverage["provider_reported_counts"] = list(reports.values())
                self._publish_progress()
                ids = {l.raw_id or l.fingerprint for l in cards}
                if not cards and not info["empty"]:
                    self.coverage["reason"] = "unverified_empty"
                    self._publish_progress()
                    raise PortalPartialError("mobile.de: Leere Seite nicht als Suchende bestätigt", results, page)
                if ids and ids == previous_ids:
                    self.coverage["reason"] = "repeated_page"
                    self._publish_progress()
                    raise PortalPartialError("mobile.de: Seitenwechsel lieferte dieselben Inserate", results, page)
                previous_ids = ids
                for listing in cards:
                    active.add(listing.fingerprint)
                    identity = listing.raw_id or listing.fingerprint
                    if identity not in seen_ids:
                        seen_ids.add(identity)
                        results.append(listing)
                self.coverage.update(unique_seen=len(active), run_unique_seen=len(seen_ids),
                                     reported_total=info["total"])
                self._publish_progress()
                if info["ids"] - ids:
                    self.coverage["reason"] = "parser_incomplete"
                    self._publish_progress()
                    raise PortalPartialError("mobile.de: Nicht alle Inseratkarten erkannt", results, page)
                # Public result navigation is capped. Narrow large result sets
                # into disjoint price intervals, under the SAME request budget.
                # Never increase traffic or change identity to get past a block.
                if not delta and page == 1 and (info["total"] or 0) > 900:
                    low, high = variant.price_from or 0, variant.price_to
                    if high is not None and high > low and len(variants) < 64:
                        mid = (low + high) // 2
                        variants[variant_index:variant_index + 1] = [
                            replace(variant, price_from=low, price_to=mid),
                            replace(variant, price_from=mid + 1, price_to=high)]
                        previous_ids = None
                        checkpoint(variant_index, 1)
                        continue
                if info["has_next"] is False or info["empty"]:
                    # mobile can cap the UI at 50 pages. That is not completeness.
                    if info["total"] and page >= 50 and info["total"] > page * max(1, len(ids)):
                        self.coverage["reason"] = "provider_limit"
                        self._publish_progress()
                        return results
                    if not delta:
                        checkpoint(variant_index + 1, 1)
                    break
                page += 1
            else:
                if not delta:
                    # One-page overlap protects against moving result positions.
                    # No progress after a split still resumes its first page.
                    resume = max(1, page - 1) if variant_pages > 1 else page
                    if page > start_page and variant_index == start_variant:
                        resume = max(start_page + 1, resume)
                    checkpoint(variant_index, resume)
                    return results
            if used >= max_limit and variant_index + 1 < len(variants):
                return results
            variant_index += 1
        if not delta:
            self.complete_sweep = True
            self.active_fingerprints = active
            self.coverage.update(complete=True, reason="end")
            self.pending_checkpoint = {"completed_at": now, "variant": 0, "page": 1}
        else:
            self.coverage["reason"] = "delta"
        self._publish_progress()
        return results

    def enrich(self, listings, query, force=False):
        """Priority details, shared browser and persistent positive/negative cache."""
        from ..mobile_runtime import mobile_browser, load_state, save_state, MobileDeferred
        from ..browser import BrowserBlocked
        from ..battery_analyzer import parse_mobile_de_detail_html, mobile_detail_snapshot
        attempted = 0
        candidates = list(listings)
        # A delta sees only new ads. Include the stored backlog so older cars
        # eventually receive detail verification too, without refreshing last_seen.
        backlog = {}
        if self.store is not None:
            from dataclasses import fields
            names = {f.name for f in fields(Listing)} - {"image_urls", "field_evidence", "unknown_fields", "suspicious_reasons"}
            with self.store._lock:
                rows = self.store.conn.execute(
                    "SELECT * FROM deals WHERE portal='mobile.de' AND search_name=? "
                    "AND COALESCE(is_stale,0)=0 ORDER BY first_seen LIMIT 2000", (query.name,)).fetchall()
            present = {l.raw_id for l in candidates}
            for row in rows:
                values = dict(row)
                raw_id = self._listing_id(values.get("url", ""))
                if not raw_id or raw_id in present:
                    continue
                item = Listing(**{k: v for k, v in values.items() if k in names and v is not None})
                item.raw_id = raw_id
                item.field_evidence = json.loads(values.get("evidence_json") or "{}")
                item.image_urls = json.loads(values.get("image_urls") or "[]")
                backlog[id(item)] = values["fingerprint"]
                candidates.append(item)
                present.add(raw_id)
        caches = {l.raw_id: load_state(self.store, f"mobile.detail.v1.{l.raw_id}") for l in candidates if l.raw_id}
        ordered = sorted(candidates, key=lambda l: (
            caches.get(l.raw_id, {}).get("checked_at", 0) > 0,
            l.battery_soh is not None,
            -sum(v is None for v in (l.battery_kwh, l.ev_range_km, l.year)),
            caches.get(l.raw_id, {}).get("checked_at", 0)))
        for listing in ordered:
            if not listing.raw_id:
                continue
            key = f"mobile.detail.v1.{listing.raw_id}"
            cached = caches[listing.raw_id]
            if cached.get("until", 0) > time.time():
                if cached.get("html"):
                    parse_mobile_de_detail_html(cached["html"], listing)
                    if id(listing) in backlog:
                        self.store.update_mobile_details(backlog[id(listing)], listing)
                continue
            if attempted >= 3:
                continue  # later entries may have cached detail data
            attempted += 1
            try:
                html = mobile_browser().fetch(
                    f"https://suchen.mobile.de/fahrzeuge/details.html?id={listing.raw_id}",
                    store=self.store, proxy=self.proxy, kind="detail")
            except (MobileDeferred, BrowserBlocked):
                attempted = 3  # No further network; still apply cached details.
                continue
            except Exception:
                save_state(self.store, key, {"checked_at": time.time(), "until": time.time() + 6 * 3600})
                continue
            # Persistence failures must not turn a successful fetch into a
            # negative cache entry. Keep the valid snapshot for replay instead.
            cached_html = mobile_detail_snapshot(html)
            save_state(self.store, key, {"checked_at": time.time(), "until": time.time() + 24 * 3600, "html": cached_html})
            parse_mobile_de_detail_html(cached_html, listing)
            if id(listing) in backlog:
                self.store.update_mobile_details(backlog[id(listing)], listing)
        return listings

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
            if link is None:
                link = art.find_parent("a", href=True)
            if not link:
                continue
            if link.select_one("[data-testid='main-price-label']"):
                # Modern articles can include other cars from this dealer;
                # only the linked vehicle's own card is attribute evidence.
                art = link
            href = link.get("href", "")
            url = href if href.startswith("http") else "https://suchen.mobile.de" + href
            lid = self._listing_id(href)
            if not lid:
                continue
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
        return re.sub(r"^(?:(?:Gesponsert|Anzeige|NEU)\b\s*[:|-]?\s*)+", "", value or "", flags=re.I).strip()

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
        if "hybrid" in v:
            return "hybrid"
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
