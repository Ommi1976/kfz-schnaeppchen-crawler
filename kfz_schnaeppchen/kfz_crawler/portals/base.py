"""Basisklasse für alle Portal-Scraper."""

from __future__ import annotations

import os
import random
import time
from typing import List, Optional

import requests

from ..models import Listing, SearchQuery

# Konsistente, aktuelle Browser-Profile (UA + passende Client-Hints).
BROWSER_PROFILES = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "ch_ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "ch_ua": '"Chromium";v="123", "Google Chrome";v="123", "Not-A.Brand";v="99"',
        "platform": '"macOS"',
    },
]


class PortalError(Exception):
    """Wird geworfen, wenn ein Portal nicht abgefragt werden kann (z. B. Block)."""


class _Rendered:
    """Minimaler Response-Ersatz für vom Browser gerenderte Seiten."""
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200


class BasePortal:
    #: menschenlesbarer Name, wird in Ausgaben angezeigt
    name: str = "base"
    #: True, wenn dieses Portal (Bot-Schutz/SPA) den Browser-Renderer braucht
    PREFERS_BROWSER: bool = False

    def __init__(
        self,
        request_delay: float = 2.5,
        max_pages: int = 2,
        proxy: Optional[str] = None,
        render: bool = False,
    ):
        self.request_delay = request_delay
        self.max_pages = max_pages
        self.render = render
        # Proxy: Argument > Umgebungsvariable KFZ_PROXY. Beispiel für Tor:
        #   socks5h://127.0.0.1:9050   (h = DNS über den Proxy auflösen)
        self.proxy = proxy or os.environ.get("KFZ_PROXY") or None
        self.session = requests.Session()
        if self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})
        self._profile = random.choice(BROWSER_PROFILES)

    @property
    def _use_browser(self) -> bool:
        return self.render and self.PREFERS_BROWSER

    # ---- HTTP-Helfer -------------------------------------------------
    def _headers(self) -> dict:
        p = self._profile
        return {
            "User-Agent": p["ua"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "sec-ch-ua": p["ch_ua"],
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": p["platform"],
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "DNT": "1",
        }

    def _get(self, url: str, **kwargs):
        time.sleep(self.request_delay + random.uniform(0, 1.0))
        # Render-Pfad: JS-lastige/geschützte Portale über echten Browser laden.
        if self._use_browser:
            from .. import browser  # lazy: Playwright nur laden, wenn genutzt
            try:
                html = browser.fetch_rendered(url, proxy=self.proxy)
            except browser.BrowserUnavailable as e:
                raise PortalError(f"{self.name}: Browser-Backend nicht verfügbar – {e}")
            except browser.BrowserBlocked:
                raise PortalError(
                    f"{self.name}: Auch mit Browser geblockt (Anti-Bot-Challenge)."
                )
            return _Rendered(html)
        try:
            resp = self.session.get(url, headers=self._headers(), timeout=25, **kwargs)
        except requests.exceptions.ProxyError as e:
            raise PortalError(
                f"{self.name}: Proxy-Fehler ({self.proxy}). Läuft Tor auf dem Port? {e}"
            )
        if resp.status_code in (403, 429):
            raise PortalError(
                f"{self.name}: Zugriff blockiert (HTTP {resp.status_code}). "
                "Portal hat automatisierte Anfrage erkannt."
            )
        resp.raise_for_status()
        return resp

    # ---- von Subklassen zu implementieren ---------------------------
    def search(self, query: SearchQuery) -> List[Listing]:
        raise NotImplementedError
