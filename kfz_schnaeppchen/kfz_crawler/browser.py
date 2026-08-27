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
    render_delay: float = 1.0,
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
                is_linux = sys.platform.startswith("linux")
                if engine == "firefox":
                    ua = ("Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0"
                          if is_linux else
                          "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0")
                else:
                    ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                          if is_linux else
                          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

                ctx_args = {
                    "locale": "de-DE",
                    "timezone_id": "Europe/Berlin",
                    "user_agent": ua,
                    "viewport": {"width": 1440, "height": 900},
                    "extra_http_headers": {
                        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                }
                if proxy:
                    server = proxy.replace("socks5h://", "socks5://")
                    ctx_args["proxy"] = {"server": server}

                context = browser.new_context(**ctx_args)
                page = context.new_page()
                try:
                    page.goto(url, wait_until=wait_until, timeout=timeout_ms)

                    # Warten auf Consent-Banner oder bereits gerenderte Inserate
                    btn_sel = "button:has-text('Einverstanden'), button:has-text('Alle akzeptieren'), button:has-text('Zustimmen'), button:has-text('Akzeptieren')"
                    try:
                        page.wait_for_selector(f"{btn_sel}, article, [data-testid='search-column']", timeout=8000)
                        btn = page.locator(btn_sel).first
                        if btn.is_visible():
                            btn.click(timeout=1500)
                            time.sleep(2.0)
                    except Exception:
                        pass

                    if render_delay > 0:
                        time.sleep(render_delay)

                    html = page.content()
                    if len(html) < 30000:
                        time.sleep(2.0)
                        html = page.content()
                    return html
                finally:
                    context.close()
            finally:
                browser.close()
