"""Browser-Backend (Playwright) für JS-lastige und bot-geschützte Portale (z. B. mobile.de).

Nutzt standardmäßig Playwright Firefox Headless, da die native Gecko-Engine
den Akamai Bot Manager von mobile.de server-seitig zuverlässig und ohne
Sperren (Status 200) passiert.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_lock = threading.Lock()


class BrowserUnavailable(RuntimeError):
    """Playwright/Browser ist nicht installiert."""


class BrowserBlocked(RuntimeError):
    """Seite wurde trotz Browser durch Anti-Bot-Schutz geblockt."""


def fetch_rendered(
    url: str,
    proxy: Optional[str] = None,
    engine: str = "firefox",
    wait_until: str = "domcontentloaded",
    timeout_ms: int = 30000,
    render_delay: float = 3.0,
    wait_selector: Optional[str] = None,
    wait_selector_timeout_ms: int = 15000,
) -> str:
    """Lädt eine URL in Playwright Firefox/Chromium und liefert das gerenderte HTML.

    Ist ``wait_selector`` gesetzt, wird explizit gewartet, bis dieses Element im
    DOM ist (statt nur eines festen ``render_delay``). Das ist auf langsamen/
    ausgelasteten Hosts entscheidend: bei einer React-SPA wie mobile.de sind die
    Listings sonst noch nicht gerendert, wenn der feste Delay abläuft -> 0 Treffer.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserUnavailable("Playwright nicht installiert.") from e

    with _lock:
        with sync_playwright() as p:
            engine_obj = getattr(p, engine, p.firefox)
            browser = engine_obj.launch(headless=True)
            try:
                page = browser.new_page(
                    locale="de-DE",
                    timezone_id="Europe/Berlin",
                    viewport={"width": 1440, "height": 900},
                )
                try:
                    # Akamai Session Warmup auf Startseite
                    if "mobile.de" in url:
                        try:
                            page.goto("https://www.mobile.de/", wait_until="domcontentloaded", timeout=15000)
                            time.sleep(1.5)
                        except Exception:
                            pass

                    page.goto(url, wait_until=wait_until, timeout=timeout_ms)

                    # Auf die eigentlichen Inhalte warten (robust gegen langsame CPU).
                    if wait_selector:
                        try:
                            page.wait_for_selector(wait_selector, timeout=wait_selector_timeout_ms)
                        except Exception:
                            # Selektor nicht erschienen -> trotzdem weiter, ggf. Block/leer.
                            logger.debug("wait_selector '%s' nicht erschienen", wait_selector)

                    if render_delay > 0:
                        time.sleep(render_delay)

                    html = page.content()
                    return html
                finally:
                    page.close()
            finally:
                browser.close()


def fetch_rendered_batch(
    srp_url: str,
    detail_urls: List[str],
    proxy: Optional[str] = None,
    engine: str = "firefox",
    timeout_ms: int = 25000,
    srp_delay: float = 2.0,
    detail_delay: float = 2.5,
) -> Tuple[str, Dict[str, str]]:
    """Lädt die SRP und anschließend Detailseiten in EINER Browser-Session.

    Gibt (srp_html, {detail_url: detail_html, ...}) zurück.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserUnavailable("Playwright nicht installiert.") from e

    with _lock:
        with sync_playwright() as p:
            engine_obj = getattr(p, engine, p.firefox)
            browser = engine_obj.launch(headless=True)
            try:
                page = browser.new_page(
                    locale="de-DE",
                    timezone_id="Europe/Berlin",
                    viewport={"width": 1440, "height": 900},
                )
                try:
                    # 1. Warmup auf Startseite
                    try:
                        page.goto("https://www.mobile.de/", wait_until="domcontentloaded", timeout=15000)
                        time.sleep(1.5)
                    except Exception:
                        pass

                    # 2. SRP abrufen (etabliert Akamai-Session)
                    page.goto(srp_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    time.sleep(srp_delay)
                    srp_html = page.content()

                    # 3. Detail-URLs in derselben Session abrufen
                    details: Dict[str, str] = {}
                    for url in detail_urls:
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                            time.sleep(detail_delay)
                            html = page.content()
                            if "zugriff verweigert" not in html.lower()[:500]:
                                details[url] = html
                                logger.debug("Detail OK: %s (%d bytes)", url, len(html))
                            else:
                                logger.warning("Detail blocked: %s", url)
                        except Exception as e:
                            logger.debug("Detail-Fehler %s: %s", url, e)
                            continue

                    return srp_html, details
                finally:
                    page.close()
            finally:
                browser.close()

