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
from urllib.parse import parse_qs, urlparse

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


STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
delete Object.getPrototypeOf(navigator).webdriver;
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['de-DE', 'de', 'en-US', 'en'] });
"""

CHROMIUM_STEALTH_JS = STEALTH_JS + """
window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
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


def _page_number(url: str) -> Optional[str]:
    """Liest die Zielseite aus einer mobilen Such-URL."""
    return parse_qs(urlparse(url).query).get("pageNumber", [None])[0]


def _click_matching_page_link(page, target_page: Optional[str]) -> bool:
    """Folgt einer vorhandenen Seitennavigation wie im sichtbaren Browser.

    Die Suchseite entscheidet damit selbst über die Navigations-URL. Ein
    Fallback auf die übergebene URL bleibt nur für Layout-Änderungen erhalten.
    """
    if not target_page:
        return False
    selectors = [
        f'a[href*="pageNumber={target_page}"]',
        f'a[href*="pageNumber%3D{target_page}"]',
    ]
    if target_page.isdigit() and int(target_page) > 1:
        selectors.extend([
            "a[aria-label*='Nächste']",
            "a[aria-label*='Weiter']",
            "button[aria-label*='Nächste']",
            "button[aria-label*='Weiter']",
        ])
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            locator.click(timeout=8000)
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            return True
        except Exception:
            continue
    return False


def _matching_user_agent(browser) -> str:
    """Nutzt exakt die installierte Browser-Version, ohne Headless-Mismatch."""
    probe_context = browser.new_context()
    try:
        probe_page = probe_context.new_page()
        return probe_page.evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
    finally:
        probe_context.close()


def _inject_saved_mobile_cookies(context) -> int:
    """Übernimmt nur explizit im Add-on gespeicherte mobile.de-Cookies."""
    try:
        from .cookie_storage import get_mobile_cookies
        saved = get_mobile_cookies(max_age_seconds=12 * 3600)
        cookies = [
            {
                "name": str(name),
                "value": str(value),
                "domain": ".mobile.de",
                "path": "/",
                "secure": True,
            }
            for name, value in saved.items()
            if name and value
        ]
        if cookies:
            context.add_cookies(cookies)
        return len(cookies)
    except Exception as exc:
        logger.debug("Gespeicherte mobile.de-Cookies konnten nicht geladen werden: %s", exc)
        return 0


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
                launch_kwargs: dict = {"headless": not bool(os.environ.get("DISPLAY"))}
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
                    page.add_init_script(CHROMIUM_STEALTH_JS if engine == "chromium" else STEALTH_JS)
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
    engine: str = "firefox",
    timeout_ms: int = 30000,
    request_delay_range: tuple[float, float] = (1.7, 3.2),
) -> Iterator[Callable[..., str]]:
    """Öffnet eine wiederverwendbare Browser-Session für mehrere Seiten.

    Für mobile.de wird ein dauerhaftes Firefox-Profil verwendet. Das ergibt
    über Läufe hinweg eine konsistente, normale Browsersitzung ohne Cookie- oder
    Engine-Wechsel.
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
            launch_kwargs["headless"] = not bool(os.environ.get("DISPLAY"))
            context_kwargs = dict(
                locale="de-DE",
                timezone_id="Europe/Berlin",
                viewport={"width": 1440, "height": 900},
            )
            browser = None
            if engine == "firefox" and not effective_proxy:
                PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                context = engine_obj.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR),
                    **launch_kwargs,
                    **context_kwargs,
                )
            else:
                browser = engine_obj.launch(**launch_kwargs)
                context_kwargs["user_agent"] = _matching_user_agent(browser)
                context = browser.new_context(**context_kwargs)
                page_script = CHROMIUM_STEALTH_JS if engine == "chromium" else STEALTH_JS
                context.add_init_script(page_script)
            page = context.pages[0] if context.pages else context.new_page()
            last_request_at = 0.0

            try:
                try:
                    page.goto("https://www.mobile.de/", wait_until="domcontentloaded", timeout=15000)
                    last_request_at = time.monotonic()
                except Exception:
                    pass

                def fetch(
                    url: str,
                    wait_selector: Optional[str] = None,
                    render_delay: float = 0.8,
                    max_retries: int = 0,
                ) -> str:
                    for attempt in range(max_retries + 1):
                        elapsed = time.monotonic() - last_request_at
                        polite_delay = random.uniform(*request_delay_range)
                        if last_request_at and elapsed < polite_delay:
                            time.sleep(polite_delay - elapsed)
                        clicked = _click_matching_page_link(page, _page_number(url))
                        response = None if clicked else page.goto(
                            url, wait_until="domcontentloaded", timeout=timeout_ms
                        )
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
                            return html
                        if attempt < max_retries:
                            wait_seconds = 6.0 * (attempt + 1)
                            logger.warning("mobile.de antwortet mit Block/HTTP %s; %.0f s Backoff (%d/%d)", status or "HTML", wait_seconds, attempt + 1, max_retries)
                            # Ein abgewiesener Schutz-Cookie wird beim Retry nicht
                            # erneut gesendet; die Session darf sich frisch aufbauen.
                            context.clear_cookies()
                            time.sleep(wait_seconds)
                            continue
                        raise BrowserBlocked(f"Browserzugriff blockiert: {url}")
                    raise BrowserBlocked(f"Browserzugriff blockiert: {url}")

                yield fetch
            finally:
                page.close()
                context.close()
                if browser is not None:
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
