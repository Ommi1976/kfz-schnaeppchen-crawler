"""Browser-Backend (Playwright) für JS-lastige und bot-geschützte Portale (z. B. mobile.de).

Nutzt standardmäßig Playwright Firefox Headless, da die native Gecko-Engine
den Akamai Bot Manager von mobile.de server-seitig zuverlässig und ohne
Sperren (Status 200) passiert.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

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
) -> str:
    """Lädt eine URL in Playwright Firefox/Chromium und liefert das gerenderte HTML."""
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
                    if render_delay > 0:
                        time.sleep(render_delay)

                    html = page.content()
                    return html
                finally:
                    page.close()
            finally:
                browser.close()
