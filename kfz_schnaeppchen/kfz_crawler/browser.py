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
    render_delay: float = 0.5,
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
                ctx_args = {
                    "locale": "de-DE",
                    "timezone_id": "Europe/Berlin",
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

                    # Consent-Banner Klick (mobile.de Einverstanden / Zustimmen)
                    t0 = time.time()
                    clicked = False
                    while time.time() - t0 < 8.0:
                        try:
                            buttons = page.locator("button").all()
                        except Exception:
                            buttons = []
                        for b in buttons:
                            try:
                                txt = (b.text_content() or "").strip().lower()
                            except Exception:
                                continue
                            if any(w in txt for w in ["einverstanden", "alle akzeptieren", "zustimmen", "akzeptieren"]):
                                try:
                                    b.click(timeout=2000)
                                    clicked = True
                                    break
                                except Exception:
                                    pass
                        if clicked:
                            break
                        time.sleep(0.5)

                    # Warten bis Inserate fertig gerendert sind
                    try:
                        page.wait_for_selector("article a[href*='details.html']", timeout=12000)
                    except Exception:
                        pass

                    if render_delay > 0:
                        time.sleep(render_delay)

                    html = page.content()
                    return html
                finally:
                    context.close()
            finally:
                browser.close()
