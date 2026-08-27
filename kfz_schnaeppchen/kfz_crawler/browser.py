"""Browser-Backend (Playwright) für JS-lastige und bot-geschützte Portale (z. B. mobile.de).

Nutzt standardmäßig Playwright Firefox Headless, da die native Gecko-Engine
den Akamai Bot Manager von mobile.de server-seitig zuverlässig und ohne
Sperren (Status 200) passiert.
"""

from __future__ import annotations

import atexit
import random
import threading
import time
from typing import Optional

_lock = threading.Lock()
_pw = None
_browsers: dict = {}

_BLOCK_MARKERS = (
    "captcha-delivery.com",
    "datadome",
    "you have been blocked",
    "access to this page has been denied",
    "access denied",
    "zugriff verweigert",
    "unusual traffic",
)


class BrowserUnavailable(RuntimeError):
    """Playwright/Browser ist nicht installiert."""


class BrowserBlocked(RuntimeError):
    """Seite wurde trotz Browser durch Anti-Bot-Schutz geblockt."""


def _ensure_browser(engine_name: str = "firefox"):
    global _pw, _browsers
    if engine_name in _browsers and _browsers[engine_name]:
        return _browsers[engine_name]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserUnavailable(
            "Playwright fehlt. Installieren mit: "
            "pip install playwright && playwright install firefox"
        ) from e
    try:
        if _pw is None:
            _pw = sync_playwright().start()
        engine = getattr(_pw, engine_name, _pw.firefox)
        if engine_name == "firefox":
            _browsers[engine_name] = engine.launch(headless=True)
        else:
            _browsers[engine_name] = engine.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
    except Exception as e:
        raise BrowserUnavailable(f"{engine_name.capitalize()} konnte nicht gestartet werden: {e}") from e
    atexit.register(_shutdown)
    return _browsers[engine_name]


def _shutdown():
    global _pw, _browsers
    try:
        for b in _browsers.values():
            if b:
                b.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _browsers = {}
    _pw = None


def fetch_rendered(
    url: str,
    proxy: Optional[str] = None,
    engine: str = "firefox",
    wait_until: str = "domcontentloaded",
    timeout_ms: int = 30000,
    render_delay: float = 1.5,
) -> str:
    """Lädt eine URL in Playwright Firefox/Chromium und liefert das gerenderte HTML."""
    import sys
    with _lock:
        browser = _ensure_browser(engine)
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
            if render_delay > 0:
                time.sleep(render_delay)
            html = page.content()
        finally:
            context.close()

    low = html.lower()
    if any(m in low for m in _BLOCK_MARKERS):
        raise BrowserBlocked("Zugriff durch Bot-Schutz verweigert.")
    return html
