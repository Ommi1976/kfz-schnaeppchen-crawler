"""Optionales Browser-Backend (Playwright) für JS-lastige / bot-geschützte Portale.

Wird nur genutzt, wenn `settings.use_browser` aktiv ist und ein Portal
`PREFERS_BROWSER = True` setzt (mobile.de, AutoUncle, heycar). Playwright wird
bewusst LAZY importiert, damit die Standardinstallation ohne Browser auskommt.

Aktivierung (Standalone):
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import atexit
import random
import threading
from typing import Optional

_lock = threading.Lock()
_pw = None          # Playwright-Handle
_browser = None     # Browser-Instanz

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['de-DE', 'de']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = { runtime: {} };
"""

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
    """Playwright/Chromium ist nicht installiert."""


class BrowserBlocked(RuntimeError):
    """Seite wurde trotz Browser durch Anti-Bot-Schutz geblockt."""


def _ensure_browser():
    global _pw, _browser
    if _browser is not None:
        return _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserUnavailable(
            "Playwright fehlt. Installieren mit: "
            "pip install playwright && playwright install chromium"
        ) from e
    try:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
    except Exception as e:  # z. B. Chromium-Binary fehlt
        raise BrowserUnavailable(f"Chromium konnte nicht gestartet werden: {e}") from e
    atexit.register(_shutdown)
    return _browser


def _shutdown():
    global _pw, _browser
    try:
        if _browser:
            _browser.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _browser = None
    _pw = None


def fetch_rendered(
    url: str,
    proxy: Optional[str] = None,
    wait_until: str = "networkidle",
    timeout_ms: int = 30000,
) -> str:
    """Lädt eine URL in echtem Chromium und gibt das gerenderte HTML zurück."""
    with _lock:  # Playwright-Sync-API ist nicht threadsicher
        browser = _ensure_browser()
        ctx_args = {
            "locale": "de-DE",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1366, "height": 900},
            "extra_http_headers": {"Accept-Language": "de-DE,de;q=0.9,en;q=0.7"},
        }
        if proxy:
            # Chromium versteht socks5, nicht socks5h -> normalisieren.
            server = proxy.replace("socks5h://", "socks5://")
            ctx_args["proxy"] = {"server": server}

        context = browser.new_context(**ctx_args)
        context.add_init_script(_STEALTH_JS)
        page = context.new_page()
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            # kleine, zufällige „menschliche" Pause
            page.wait_for_timeout(random.randint(600, 1500))
            html = page.content()
        finally:
            context.close()

    low = html.lower()
    if any(m in low for m in _BLOCK_MARKERS):
        raise BrowserBlocked()
    return html
