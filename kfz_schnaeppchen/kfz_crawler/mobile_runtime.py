"""HA-owned mobile.de browser, shared request budget and persistent circuit breaker.

All Playwright objects stay on one dedicated thread. Cookies are maintained by
the browser in /data, not copied from a desktop. A challenge stops *all* mobile
requests, including detail and certificate requests; it never triggers identity
rotation, cookie deletion or an immediate retry.
"""
from __future__ import annotations

import atexit
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .browser import BrowserBlocked, BrowserUnavailable, PROFILE_DIR, _is_block_page

STATE_KEY = "mobile.runtime.v1"
logger = logging.getLogger(__name__)
CARD_LINKS = "a[href*='details.html?id='], a[href*='/auto-inserat/']"


class MobileDeferred(RuntimeError):
    """Request postponed by shared budget/backoff (not an empty result)."""


class MobilePageError(RuntimeError):
    """Page could not be verified; keep existing listings and retry later."""


def load_state(store, key: str) -> dict:
    if store is None:
        return {}
    try:
        value = json.loads(store.get_setting(key, "{}"))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def save_state(store, key: str, value: dict) -> None:
    if store is not None:
        store.set_setting(key, json.dumps(value, ensure_ascii=False))


def is_mobile_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return not parsed.username and not parsed.password and parsed.port in (None, 443) and parsed.scheme == "https" and parsed.hostname in {
            "www.mobile.de", "suchen.mobile.de", "m.mobile.de",
        }
    except ValueError:
        return False


def search_page_info(html: str) -> dict:
    """Read displayed count, selected page and next-button, not script payloads."""
    soup = BeautifulSoup(html, "lxml")
    ids = set()
    effective = []
    for a in soup.select(CARD_LINKS):
        href = a.get("href", "")
        lid = parse_qs(urlparse(href).query).get("id", [None])[0]
        if not lid:
            match = re.search(r"/(\d{7,})\.html", href)
            lid = match.group(1) if match else None
        if lid:
            ids.add(str(lid))
            effective.append(parse_qs(urlparse(href).query))
    total = None
    for heading in soup.select("h1, h2"):
        match = re.search(r"([\d.\s]+)\s+Angebote\b", heading.get_text(" ", strip=True))
        if match:
            total = int(re.sub(r"\D", "", match.group(1)))
            break
    current = None
    pages = []
    for el in soup.select("button[aria-label], a[aria-label], [aria-current='page']"):
        label = el.get("aria-label", "")
        match = re.fullmatch(r"Seite\s+(\d+)", label)
        if match:
            number = int(match.group(1))
            pages.append(number)
            if el.has_attr("disabled") or el.get("aria-current") == "page":
                current = number
    # Selector lists are document-ordered, not priority-ordered. A dealer
    # carousel's Weiter often precedes the actual pagination.
    next_el = soup.select_one("[data-testid='pagination:next']")
    if next_el is None:
        next_el = soup.select_one("[aria-label='Weitere Angebote'] [aria-label='Weiter'], nav [aria-label='Weiter']")
    has_next = None
    if next_el:
        has_next = not (next_el.has_attr("disabled") or next_el.get("aria-disabled") == "true")
    elif pages and current is not None:
        has_next = current < max(pages)
    elif total is not None and total <= len(ids):
        has_next = False
    for node in soup.select("script, style, noscript"):
        node.decompose()
    text = soup.get_text(" ", strip=True)
    empty = total == 0 or (total is None and not ids and bool(re.search(
        r"\b(?:keine|0)\s+(?:passenden\s+)?(?:Angebote|Fahrzeuge|Treffer)\b", text, re.I)))
    if empty and not ids:
        has_next = False
    return {"ids": ids, "total": total, "page": current,
            "has_next": has_next, "empty": empty, "text": text, "effective": effective}


def verify_filters(info: dict, url: str) -> None:
    """Fail closed if the critical EV filters are silently ignored by the site."""
    params = parse_qs(urlparse(url).query)
    text = re.sub(r"\s+", " ", info["text"])
    checks = []
    for key, label in (("bc", "Batteriekapazität"), ("re", "Reichweite")):
        if key in params:
            value = params[key][0].split(":")[0]
            value_pattern = re.escape(value).replace(r"\.", "[.,]")
            checks.append((label, rf"{label}\s*:\s*(?:ab|von)\s*{value_pattern}(?![\d.,])"))
    if params.get("spc") == ["ADAPTIVE_CRUISE_CONTROL"]:
        # In the SRP this is a selected-filter chip, not the details-search form.
        checks.append(("Abstandstempomat", r"Abstandstempomat"))
    for label, pattern in checks:
        if not re.search(pattern, text, re.I):
            raise MobilePageError(f"Suchfilter '{label}' auf mobile.de nicht bestätigt")
    # Result links generated by the site carry its canonical, applied filters.
    # Unlike a title containing 'ACC', these detect silently dropped parameters.
    canonical = next((p for p in info.get("effective", []) if "vc" in p and "s" in p), None)
    if canonical is not None:
        for key in ("bc", "re", "spc"):
            if key in params and canonical.get(key) != params[key]:
                raise MobilePageError(f"mobile.de hat den Suchparameter '{key}' nicht übernommen")


def retry_after_seconds(value: str, now=None) -> float:
    now = time.time() if now is None else now
    try:
        delay = float(value) if str(value).isdigit() else parsedate_to_datetime(value).timestamp() - now
        return min(7 * 86400, max(0, delay))
    except (ValueError, TypeError, OverflowError):
        return 0


class RequestControl:
    """One persistent portal-wide gate; defaults are budgets, not safe-limit claims."""
    LIMITS = {"search": 30, "detail": 10, "image": 20}
    PAUSES = (2 * 3600, 6 * 3600, 24 * 3600)

    def __init__(self, store=None, *, clock=time.time, interval=12.0):
        self.store = store
        self.clock = clock
        self.interval = interval
        self.state = load_state(store, STATE_KEY)
        # One-time migration: an add-on update must not erase an active pause.
        if not self.state and store is not None and hasattr(store, "list_portal_health"):
            for health in store.list_portal_health():
                if health.get("portal") != "mobile.de" or health.get("status") != "blocked":
                    continue
                count = max(1, int(health.get("block_count") or 1))
                until = (health.get("last_run") or 0) + self.PAUSES[min(count, len(self.PAUSES)) - 1]
                if until > max(self.clock(), self.state.get("blocked_until", 0)):
                    self.state.update(block_count=count, blocked_until=until, status="blocked",
                                      last_error="Bestehende Schutzpause übernommen")
            if self.state:
                self.persist()

    def persist(self):
        save_state(self.store, STATE_KEY, self.state)

    def reserve(self, kind: str) -> float:
        now = self.clock()
        until = float(self.state.get("blocked_until", 0))
        if now < until:
            raise MobileDeferred(f"mobile.de: Schutzpause bis {time.strftime('%H:%M', time.localtime(until))}")
        if "window_start" not in self.state or now - self.state["window_start"] >= 3600:
            self.state.update(window_start=now, counts={})
        counts = self.state.setdefault("counts", {})
        if counts.get(kind, 0) >= self.LIMITS[kind]:
            raise MobileDeferred(f"mobile.de: Stundenbudget für {kind} erreicht; Fortsetzung automatisch")
        counts[kind] = counts.get(kind, 0) + 1
        due = max(now, self.state.get("next_request", 0))
        self.state["next_request"] = due + self.interval
        self.persist()  # attempts count even on crashes/timeouts
        return max(0, due - now)

    def blocked(self, retry_after=0):
        count = int(self.state.get("block_count", 0)) + 1
        pause = max(self.PAUSES[min(count, len(self.PAUSES)) - 1], retry_after)
        self.state.update(block_count=count, blocked_until=self.clock() + pause,
                          status="blocked", last_error="Zugriff abgewiesen / Verifikation erforderlich")
        self.persist()

    def success(self):
        self.state.update(last_success=self.clock(), status="ok", block_count=0, blocked_until=0, last_error="")
        self.persist()

    def failed(self, message):
        self.state.update(status="error", last_error=str(message)[:250])
        self.persist()


class MobileBrowser:
    def __init__(self, profile_dir=None):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mobile-browser")
        self._playwright = self._context = self._page = None
        self._proxy = None
        self._control = RequestControl()
        self._lock = threading.Lock()
        self._closed = False
        self._profile_dir = Path(profile_dir) if profile_dir else PROFILE_DIR
        self._account_session = None

    def _defer_during_login(self):
        session = self._account_session
        if session and session["expires_at"] > time.time():
            raise MobileDeferred("Anmeldung im Add-on geöffnet; Suche wird anschließend fortgesetzt")
        if session:
            self._close()
        self._account_session = None

    def fetch(self, url: str, *, store=None, proxy=None, kind="search") -> str:
        if not is_mobile_url(url):
            raise ValueError("Nur explizite HTTPS-mobile.de-Adressen erlaubt")
        return self._executor.submit(self._fetch, url, store, proxy, kind).result()

    def fetch_image(self, url: str, *, store=None, proxy=None) -> bytes:
        # Never follow an arbitrary listing URL to LAN addresses or another host.
        parsed = urlparse(url)
        if (parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.scheme != "https" or parsed.hostname not in {"img.classistatic.de", "i.classistatic.de"}):
            raise ValueError("Unbekannte mobile.de-Bildquelle")
        return self._executor.submit(self._fetch_image, url, store, proxy).result()

    def _fetch_image(self, url, store, proxy):
        self._defer_during_login()
        if store is not None:
            self._control = RequestControl(store)
        control = self._control
        delay = control.reserve("image")
        if delay:
            time.sleep(delay)
        self._open(proxy)
        response = self._context.request.get(url, timeout=10000, max_redirects=0)
        try:
            if response.status in (403, 429):
                control.blocked(retry_after_seconds(response.headers.get("retry-after", "")))
                raise BrowserBlocked("mobile.de-Bildabruf abgewiesen; Portal pausiert")
            if response.status != 200 or not response.headers.get("content-type", "").startswith("image/"):
                raise MobilePageError("Keine gültigen Bilddaten")
            if int(response.headers.get("content-length", "0")) > 8 * 1024 * 1024:
                raise MobilePageError("Bild zu groß")
            data = response.body()
            if len(data) > 8 * 1024 * 1024:
                raise MobilePageError("Bild zu groß")
            # CDN success does NOT clear a challenge on the search host.
            return data
        finally:
            response.dispose()

    def _open(self, proxy):
        if self._context is not None:
            if proxy != self._proxy:
                raise MobilePageError("Proxy-Wechsel erfordert einen Add-on-Neustart")
            return
        import os
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                self._profile_dir.chmod(0o700)
            kwargs = dict(headless=not bool(os.environ.get("DISPLAY")),
                          locale="de-DE", timezone_id="Europe/Berlin",
                          viewport={"width": 1440, "height": 900},
                          accept_downloads=False,
                          firefox_user_prefs={"signon.rememberSignons": False})
            if proxy:
                kwargs["proxy"] = {"server": proxy}
            self._context = self._playwright.firefox.launch_persistent_context(str(self._profile_dir), **kwargs)
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._proxy = proxy
        except Exception as exc:
            self._close()
            raise BrowserUnavailable("mobile.de-Browser konnte nicht gestartet werden") from exc

    def _fetch(self, url, store, proxy, kind):
        # Only this worker accesses the gate and browser; no check/submit races.
        self._defer_during_login()
        if store is not None:
            self._control = RequestControl(store)
        control = self._control
        delay = control.reserve(kind)
        if delay:
            time.sleep(delay)
        try:
            self._open(proxy)
            page = self._page
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = response.status if response else 0
            if status in (403, 429):
                retry = (response.headers or {}).get("retry-after", "0")
                control.blocked(retry_after_seconds(retry))
                raise BrowserBlocked("mobile.de: HTTP 403/429 – alle Abrufe pausiert")
            if status >= 400:
                raise MobilePageError(f"mobile.de: HTTP {status}")
            if not is_mobile_url(page.url):
                raise MobilePageError("mobile.de: Unerwartete Weiterleitung")
            # Consent is not an anti-bot challenge. Reject optional cookies.
            consent = page.get_by_text("Ablehnen", exact=True)
            if consent.count() == 1 and consent.is_visible():
                consent.click(timeout=3000)
            deadline = time.monotonic() + 25
            previous = None
            stable = 0
            expected = int(parse_qs(urlparse(url).query).get("pageNumber", ["1"])[0])
            while time.monotonic() < deadline:
                html = page.content()
                if _is_block_page(html):
                    control.blocked()
                    raise BrowserBlocked("mobile.de: Verifikation erforderlich – alle Abrufe pausiert")
                if kind == "detail":
                    # A generic error/login page can have an h1 too. Require the
                    # same vehicle ID, technical content and a stable rendering.
                    from .portals.mobile_de import MobileDe
                    from .battery_analyzer import _relevant_detail_text
                    detail = _relevant_detail_text(BeautifulSoup(html, "lxml"))
                    ready = (MobileDe._listing_id(page.url) == MobileDe._listing_id(url)
                             and bool(page.locator("h1").count()) and len(detail) >= 150
                             and bool(re.search(r"Erstzulassung|Kilometerstand|Fahrzeugbeschreibung|Batteriekapazität", detail, re.I)))
                    stable = stable + 1 if ready and detail == previous else 0
                    previous = detail
                    if stable >= 2:
                        control.success()
                        return html
                else:
                    info = search_page_info(html)
                    # Requested URL alone cannot confirm SPA pagination. A
                    # selected page (or a genuine one-page/empty result) must.
                    correct_page = info["page"] == expected or (
                        expected == 1 and info["has_next"] is False)
                    signature = (frozenset(info["ids"]), info["page"], info["total"])
                    ready = correct_page and (info["ids"] or info["empty"])
                    stable = stable + 1 if ready and signature == previous else 0
                    previous = signature
                    if stable >= 2:
                        verify_filters(info, url)
                        control.success()
                        return html
                time.sleep(.7)
            raise MobilePageError("mobile.de: Seite/Filter nicht vollständig geladen; Bestand bleibt erhalten")
        except BrowserBlocked:
            raise
        except (MobilePageError, BrowserUnavailable) as exc:
            control.failed(exc)
            raise
        except Exception as exc:
            # Preserve profile, but recreate crashed browser on the next run.
            self._close()
            control.failed("Browserabruf unterbrochen")
            raise MobilePageError("mobile.de: Browserabruf unterbrochen") from exc

    def _close(self):
        try:
            if self._context:
                self._context.close()
        finally:
            self._context = self._page = None
            if self._playwright:
                self._playwright.stop()
                self._playwright = None

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._executor.submit(self._close).result()
            finally:
                self._executor.shutdown(wait=True)


_instance = None
_instance_lock = threading.Lock()


def mobile_browser() -> MobileBrowser:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = MobileBrowser()
        return _instance


def close_mobile_browser():
    global _instance
    with _instance_lock:
        if _instance is not None:
            try:
                _instance.close()
            except RuntimeError:
                # Interpreter shutdown may already have joined executor threads.
                logger.debug("mobile.de worker already stopped")
            _instance = None


def mobile_status(store) -> dict:
    state = load_state(store, STATE_KEY)
    counts = state.get("counts", {}) if time.time() - state.get("window_start", 0) < 3600 else {}
    return {"mode": "autonomous", "status": state.get("status", "ready"),
            "blocked_until": state.get("blocked_until", 0),
            "last_success": state.get("last_success"),
            "requests": counts,
            "limits": RequestControl.LIMITS.copy(),
            "last_error": state.get("last_error", ""),
            "desktop_required": False}


atexit.register(close_mobile_browser)
