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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def _wait_stable(page, selector: str, max_wait: float = 9.0, poll: float = 0.7) -> int:
    """Wartet, bis die Trefferzahl für ``selector`` nicht mehr wächst (SPA lädt
    Cards nach). Wichtig auf langsamen Hosts: ein fester Delay schneidet sonst
    unvollständig gerenderte Listen ab. Gibt die finale Anzahl zurück.
    """
    last = -1
    stable = 0
    deadline = time.time() + max_wait
    n = 0
    while time.time() < deadline:
        try:
            n = page.locator(selector).count()
        except Exception:
            break
        if n > 0 and n == last:
            stable += 1
            if stable >= 2:  # ~2 gleiche Messungen in Folge => fertig geladen
                break
        else:
            stable = 0
            last = n
        time.sleep(poll)
    return n


class BrowserUnavailable(RuntimeError):
    """Playwright/Browser ist nicht installiert."""


class BrowserBlocked(RuntimeError):
    """Seite wurde trotz Browser durch Anti-Bot-Schutz geblockt."""


PROFILE_DIR = Path("/data/firefox_profile")
if not Path("/data").exists():
    PROFILE_DIR = Path(__file__).parent.parent / "firefox_profile"


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
    """Lädt eine URL in Playwright Firefox mit persistentem Profil und Cookie-Caching."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserUnavailable("Playwright nicht installiert.") from e

    with _lock:
        with sync_playwright() as p:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            ctx = p.firefox.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=True,
                locale="de-DE",
                timezone_id="Europe/Berlin",
                viewport={"width": 1440, "height": 900},
            )
            page = ctx.new_page() if not ctx.pages else ctx.pages[0]
            try:
                # Akamai Session Warmup auf Startseite
                if "mobile.de" in url:
                    try:
                        page.goto("https://www.mobile.de/", wait_until="domcontentloaded", timeout=15000)
                        time.sleep(1.5)
                        try:
                            from .cookie_storage import save_mobile_cookies
                            cookies = ctx.cookies()
                            if cookies:
                                save_mobile_cookies({c["name"]: c["value"] for c in cookies})
                        except Exception:
                            pass
                    except Exception:
                        pass

                page.goto(url, wait_until=wait_until, timeout=timeout_ms)

                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=wait_selector_timeout_ms)
                    except Exception:
                        logger.debug("wait_selector '%s' nicht erschienen", wait_selector)
                    count = _wait_stable(page, wait_selector)
                    logger.debug("wait_selector '%s' stabil bei %d", wait_selector, count)

                if render_delay > 0:
                    time.sleep(render_delay)

                html = page.content()
                return html
            finally:
                try:
                    from .cookie_storage import save_mobile_cookies
                    cookies = ctx.cookies()
                    if cookies:
                        save_mobile_cookies({c["name"]: c["value"] for c in cookies})
                except Exception:
                    pass
                ctx.close()


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
                    try:
                        page.wait_for_selector("article a[href*='details.html']", timeout=20000)
                    except Exception:
                        pass
                    _wait_stable(page, "article a[href*='details.html']")
                    time.sleep(srp_delay)
                    srp_html = page.content()

                    # 3. Detail-URLs in derselben Session abrufen
                    details: Dict[str, str] = {}
                    for url in detail_urls:
                        try:
                            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                            # Auf den Detail-Inhalt warten (Titel) statt nur fester
                            # Delay – auf langsamer Box sonst leere Seite -> kein SoH.
                            try:
                                page.wait_for_selector("h1", timeout=15000)
                            except Exception:
                                pass
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

