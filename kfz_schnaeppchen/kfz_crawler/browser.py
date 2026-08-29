"""Browser-Backend (Playwright) für JS-lastige und bot-geschützte Portale (z. B. mobile.de).

Nutzt standardmäßig Playwright Firefox Headless, da die native Gecko-Engine
den Akamai Bot Manager von mobile.de server-seitig zuverlässig und ohne
Sperren (Status 200) passiert.
"""

from __future__ import annotations

import logging
import os
import random
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

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
MOBILE_STATE_PATH = PROFILE_DIR.parent / "mobile_browser_state.json"


STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
delete Object.getPrototypeOf(navigator).webdriver;
window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
"""

CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--window-size=1920,1080",
]


_BLOCK_MARKERS = (
    "zugriff verweigert",
    "access denied",
    "temporarily blocked",
    "unusual traffic",
    "captcha",
)


def _is_block_page(html: str) -> bool:
    head = (html or "").lower()[:5000]
    return any(marker in head for marker in _BLOCK_MARKERS)


def _matching_user_agent(browser) -> str:
    """Nutzt exakt die installierte Browser-Version, ohne Headless-Mismatch."""
    probe_context = browser.new_context()
    try:
        probe_page = probe_context.new_page()
        return probe_page.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
    finally:
        probe_context.close()


def fetch_rendered(
    url: str,
    proxy: Optional[str] = None,
    engine: str = "chromium",
    wait_until: str = "domcontentloaded",
    timeout_ms: int = 30000,
    render_delay: float = 2.0,
    wait_selector: Optional[str] = None,
    wait_selector_timeout_ms: int = 15000,
    max_retries: int = 2,
) -> str:
    """Lädt eine URL in Playwright Chromium/Firefox mit Stealth-Injektion und dynamischer Tor-Rotation."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserUnavailable("Playwright nicht installiert.") from e

    from .tor_service import is_tor_available, renew_tor_identity

    # Tor-Exit-Nodes sind bei großen Portalen oft bereits reputationsbelastet.
    # Deshalb nur auf ausdrückliche Konfiguration verwenden, nie automatisch.
    effective_proxy = proxy
    if not effective_proxy and os.environ.get("KFZ_USE_TOR") == "1" and "mobile.de" in url and is_tor_available():
        effective_proxy = "socks5://127.0.0.1:9050"

    with _lock:
        for attempt in range(max_retries + 1):
            with sync_playwright() as p:
                engine_obj = getattr(p, engine, p.chromium)
                launch_kwargs: dict = {"headless": True}
                if engine == "chromium":
                    launch_kwargs["args"] = CHROMIUM_ARGS
                if effective_proxy:
                    launch_kwargs["proxy"] = {"server": effective_proxy}

                try:
                    browser = engine_obj.launch(**launch_kwargs)
                except Exception:
                    # Fallback auf Firefox
                    browser = p.firefox.launch(headless=True, proxy={"server": effective_proxy} if effective_proxy else None)

                ctx = browser.new_context(
                    locale="de-DE",
                    timezone_id="Europe/Berlin",
                    viewport={"width": 1440, "height": 900},
                    user_agent=_matching_user_agent(browser),
                )
                page = ctx.new_page()
                try:
                    page.add_init_script(STEALTH_JS)
                except Exception:
                    pass

                try:
                    # Akamai Session Warmup auf Startseite
                    if "mobile.de" in url:
                        try:
                            page.goto("https://www.mobile.de/", wait_until="domcontentloaded", timeout=15000)
                            time.sleep(1.5)
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

                    # Prüfe auf Blockseite
                    if _is_block_page(html):
                        if attempt < max_retries and effective_proxy and "9050" in effective_proxy:
                            logger.info("mobile.de blockiert Tor-Node. Fordere neue Tor-Identität (Circuit) an (Versuch %d/%d)...", attempt + 1, max_retries)
                            renew_tor_identity()
                            continue
                        raise BrowserBlocked(f"Browserzugriff blockiert: {url}")

                    return html
                finally:
                    page.close()
                    browser.close()

        return html


@contextmanager
def rendered_session(
    proxy: Optional[str] = None,
    engine: str = "chromium",
    timeout_ms: int = 30000,
) -> Iterator[Callable[..., str]]:
    """Öffnet eine wiederverwendbare Browser-Session für mehrere Seiten.

    Cookies, TLS-/Akamai-Session und Browser-Fingerprint bleiben über die
    komplette Pagination erhalten. Das spart Browserstarts und reduziert
    Blockaden erheblich.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailable("Playwright nicht installiert.") from exc
    effective_proxy = proxy

    with _lock:
        with sync_playwright() as playwright:
            engine_obj = getattr(playwright, engine, playwright.chromium)
            launch_kwargs: dict = {"headless": True}
            if engine == "chromium":
                launch_kwargs["args"] = CHROMIUM_ARGS
            if effective_proxy:
                launch_kwargs["proxy"] = {"server": effective_proxy}
            browser = engine_obj.launch(**launch_kwargs)
            context_kwargs = dict(
                locale="de-DE",
                timezone_id="Europe/Berlin",
                viewport={"width": 1440, "height": 900},
                user_agent=_matching_user_agent(browser),
            )
            if MOBILE_STATE_PATH.exists():
                context_kwargs["storage_state"] = str(MOBILE_STATE_PATH)
            try:
                context = browser.new_context(**context_kwargs)
            except Exception:
                context_kwargs.pop("storage_state", None)
                context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.add_init_script(STEALTH_JS)
            last_request_at = 0.0
            had_success = False
            blocked_seen = False
            try:
                try:
                    page.goto("https://www.mobile.de/", wait_until="domcontentloaded", timeout=15000)
                    time.sleep(1.0)
                except Exception:
                    pass

                def fetch(
                    url: str,
                    wait_selector: Optional[str] = None,
                    render_delay: float = 0.8,
                    max_retries: int = 2,
                ) -> str:
                    nonlocal last_request_at, had_success, blocked_seen
                    for attempt in range(max_retries + 1):
                        elapsed = time.monotonic() - last_request_at
                        polite_delay = random.uniform(1.7, 3.2)
                        if last_request_at and elapsed < polite_delay:
                            time.sleep(polite_delay - elapsed)
                        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        last_request_at = time.monotonic()
                        status = response.status if response else 0
                        if wait_selector:
                            try:
                                page.wait_for_selector(wait_selector, timeout=18000)
                            except Exception:
                                logger.debug("Session-Selektor nicht erschienen: %s", wait_selector)
                            _wait_stable(page, wait_selector)
                        if render_delay:
                            time.sleep(render_delay)
                        html = page.content()
                        if status not in (403, 429) and not _is_block_page(html):
                            had_success = True
                            return html
                        blocked_seen = True
                        if attempt < max_retries:
                            wait_seconds = 6.0 * (attempt + 1)
                            logger.warning("mobile.de antwortet mit Block/HTTP %s; %.0f s Backoff (%d/%d)", status or "HTML", wait_seconds, attempt + 1, max_retries)
                            context.clear_cookies()
                            time.sleep(wait_seconds)
                            continue
                        raise BrowserBlocked(f"Browserzugriff blockiert: {url}")
                    raise BrowserBlocked(f"Browserzugriff blockiert: {url}")

                yield fetch
            finally:
                if had_success and not blocked_seen:
                    try:
                        MOBILE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                        context.storage_state(path=str(MOBILE_STATE_PATH))
                    except Exception as exc:
                        logger.debug("Browser-Sessionzustand konnte nicht gespeichert werden: %s", exc)
                page.close()
                context.close()
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
