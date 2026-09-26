"""HA-owned portal sessions and a short-lived, authenticated remote login view.

All browser access (including human input) is serialized on the portal worker.
No passwords, page HTML, account names or cookie values are stored in settings.
Login observations never release a search cooldown or claim successful crawling.
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .browser import BrowserBlocked, BrowserUnavailable, _is_block_page
from .mobile_runtime import (MobileBrowser, MobileDeferred, MobilePageError,
                             load_state, save_state, mobile_browser, mobile_status,
                             retry_after_seconds)

logger = logging.getLogger(__name__)

PORTALS = {
    # Official buyer-login entry observed via www.mobile.de -> Anmelden.
    # Let the portal generate its OIDC state/nonce and redirect to id.mobile.de;
    # the search-management page is not the login entry.
    "mobile_de": {"label": "mobile.de", "url": "https://www.mobile.de/api/auth/login?cf_template=OTP&source_uri=https%3A%2F%2Fwww.mobile.de%2F", "domains": ("mobile.de",)},
    "kleinanzeigen": {"label": "Kleinanzeigen", "url": "https://www.kleinanzeigen.de/m-einloggen.html", "domains": ("kleinanzeigen.de",)},
    "autoscout24": {"label": "AutoScout24", "url": "https://www.autoscout24.de/", "domains": ("autoscout24.de", "autoscout24.com")},
    "autouncle": {"label": "AutoUncle", "url": "https://www.autouncle.de/de/mein-autouncle/suchanfragen", "domains": ("autouncle.de", "autouncle.com")},
}
SESSION_SECONDS = 20 * 60
AUTH_MAX_AGE = 12 * 3600
_workers = {}
_workers_lock = threading.Lock()


class AccountError(RuntimeError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def account_key(key):
    if key not in PORTALS:
        raise AccountError("Unbekanntes Portal", 404)
    return "portal.account.v1." + key


def account_enabled(store, key):
    return bool(load_state(store, account_key(key)).get("enabled"))


def profile_path(key):
    account_key(key)
    if key == "mobile_de":
        from . import mobile_runtime
        if mobile_runtime._instance is not None:
            return mobile_runtime._instance._profile_dir
        return mobile_runtime.mobile_profile_dir()
    if key == "autouncle":
        return Path(os.environ.get("AUTO_UNCLE_PROFILE") or (
            "/data/autouncle_profile" if Path("/data").exists()
            else Path(__file__).parent.parent / "autouncle_profile"))
    root = Path("/data") if Path("/data").exists() else Path(__file__).parent
    return root / "portal_profiles" / key


def allowed_navigation(key, url):
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        return (p.scheme == "https" and not p.username and not p.password
                and p.port in (None, 443)
                and any(host == d or host.endswith("." + d) for d in PORTALS[key]["domains"]))
    except (ValueError, KeyError):
        return False


BLOCKED_LOGIN_MESSAGE = (
    "Das Portal verweigert diesem Add-on-Browser den Zugriff. Hier ist keine Anmeldung möglich. "
    "Bitte keine Zugangsdaten eingeben. Eine Anmeldung oder das Ende der Suchpause garantiert keine Freigabe."
)


def _access_state(html):
    """A hard denial is not an interactive verification or a login form."""
    soup = BeautifulSoup(html or "", "lxml")
    for node in soup.select("script, style, noscript, template, [hidden], [aria-hidden='true']"):
        node.decompose()
    text = soup.get_text(" ", strip=True).lower()
    if any(marker in text for marker in ("zugriff verweigert", "access denied")):
        return "blocked"
    if _is_block_page(str(soup)):
        return "verification_required"
    return None


def auth_evidence(html):
    """Only explicit visible-page logout/login controls are evidence, not cookies."""
    soup = BeautifulSoup(html, "lxml")
    for el in soup.select("script, style, noscript, template, [hidden], [aria-hidden='true']"):
        el.decompose()
    controls = " ".join(el.get_text(" ", strip=True) for el in soup.select("a, button, [role='button']"))
    access = _access_state(str(soup))
    if access:
        return access
    if re.search(r"\b(?:abmelden|ausloggen|log\s*out|sign\s*out)\b", controls, re.I):
        return "authenticated"
    if soup.select_one("input[type='password']") or re.search(r"\b(?:anmelden|einloggen|log\s*in|sign\s*in)\b", controls, re.I):
        return "anonymous"
    return "unverified"


def _observe(store, key, page):
    # Only visible actionable controls; a help article mentioning 'Abmelden'
    # or a hidden anonymous menu is not evidence of an authenticated account.
    from html import escape
    controls = page.locator("a, button, [role=button], input[type=password]").evaluate_all("""els => els
        .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
        .map(e => ({text: e.innerText || e.getAttribute('aria-label') || '', password: e.type === 'password'}))""")
    markup = " ".join("<input type='password'>" if c["password"] else "<button>" + escape(c["text"]) + "</button>" for c in controls)
    observation = _access_state(page.content()) or auth_evidence(markup)
    data = load_state(store, account_key(key))
    data.update(auth_state=observation, checked_at=time.time())
    if observation == "authenticated":
        data["last_authenticated_at"] = time.time()
    save_state(store, account_key(key), data)
    return observation


def _guard_context(worker, key):
    if getattr(worker, "_guarded_context", None) is worker._context:
        return

    def route_request(route):
        req = route.request
        p = urlparse(req.url)
        host = (p.hostname or "").lower()
        private = host in {"localhost", "supervisor", "homeassistant", "metadata.google.internal"} or host.endswith((".local", ".internal"))
        try:
            private = private or not ipaddress.ip_address(host).is_global
        except ValueError:
            private = private or ("." not in host)
        if p.scheme not in {"http", "https"} or private:
            return route.abort()
        # The user cannot navigate this browser to an arbitrary site or the LAN.
        if req.is_navigation_request() and req.frame.parent_frame is None and not allowed_navigation(key, req.url):
            return route.abort()
        return route.continue_()

    worker._context.route("**/*", route_request)
    worker._guarded_context = worker._context


class PortalBrowser(MobileBrowser):
    def __init__(self, key):
        super().__init__(profile_path(key))
        self.key = key

    def fetch(self, url, *, store=None, proxy=None, kind="search", **_kwargs):
        if not allowed_navigation(self.key, url):
            raise MobilePageError("Portaladresse nicht zugelassen")
        return self._executor.submit(self._fetch_portal, url, store, proxy).result()

    def _fetch_portal(self, url, store, proxy):
        self._defer_during_login()
        state_key = "portal.runtime.v1." + self.key
        state = load_state(store, state_key)
        now = time.time()
        if state.get("blocked_until", 0) > now:
            raise MobileDeferred("Portal-Schutzpause aktiv; Fortsetzung automatisch")
        if now - state.get("window_start", 0) >= 3600:
            state.update(window_start=now, count=0)
        if state.get("count", 0) >= 90:
            raise MobileDeferred("Stundenbudget erreicht; Fortsetzung automatisch")
        delay = max(0, state.get("next_request", 0) - now)
        state.update(count=state.get("count", 0) + 1, next_request=now + delay + 4)
        save_state(store, state_key, state)
        if delay:
            time.sleep(delay)
        self._open(proxy)
        _guard_context(self, self.key)
        page = self._page
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = response.status if response else 0
            page.wait_for_timeout(1500)
            html = page.content()
            if status in (403, 429) or _is_block_page(html):
                retry = retry_after_seconds((response.headers or {}).get("retry-after", "0")) if response else 0
                state.update(blocked_until=time.time() + max(2 * 3600, retry), status="blocked")
                save_state(store, state_key, state)
                raise BrowserBlocked("Portal verlangt eine Verifikation; Abrufe pausiert")
            if status == 404:
                from .mobile_runtime import PortalPageMissing
                raise PortalPageMissing("Portalseite existiert nicht (404)")
            if status >= 400 or not allowed_navigation(self.key, page.url):
                raise MobilePageError("Portalseite nicht verfügbar")
            # Account pages/login prompts are not empty search result pages.
            if page.locator("input[type=password]:visible").count():
                _observe(store, self.key, page)
                raise MobileDeferred("Erneute Anmeldung im Add-on erforderlich")
            # Allow dynamically rendered listing grids to settle without new navigation.
            previous = ""
            stable = 0
            for _ in range(12):
                text = page.locator("body").inner_text(timeout=5000)
                stable = stable + 1 if text == previous and len(text) > 100 else 0
                if stable >= 2:
                    break
                previous = text
                page.wait_for_timeout(500)
            html = page.content()
            if _is_block_page(html):
                state.update(blocked_until=time.time() + 7200, status="blocked")
                save_state(store, state_key, state)
                raise BrowserBlocked("Portal verlangt eine Verifikation; Abrufe pausiert")
            if len(page.locator("body").inner_text(timeout=5000)) < 100:
                raise MobilePageError("Portalseite nicht vollständig geladen")
            state.update(status="ok", last_success=time.time())
            save_state(store, state_key, state)
            return html
        except (MobileDeferred, BrowserBlocked, MobilePageError):
            raise
        except Exception as exc:
            # Search navigation only, no login input: the type and first line
            # (e.g. a timeout) carry no credentials but name the cause.
            kind = type(exc).__name__
            detail = (str(exc).splitlines() or [""])[0][:200]
            logger.warning("%s: Portal-Browserabruf unterbrochen (%s: %s)", self.key, kind, detail)
            # Like the mobile.de worker: keep the profile, restart a stuck or
            # bloated browser on the next request instead of reusing it.
            try:
                self._close()
            except Exception:
                pass  # _close has already dropped its references
            raise MobilePageError(f"Portal-Browserabruf unterbrochen ({kind})") from None


def portal_browser(key):
    account_key(key)
    if key == "mobile_de":
        return mobile_browser()
    with _workers_lock:
        if key not in _workers:
            _workers[key] = PortalBrowser(key)
        return _workers[key]


def connected_fetch(portal_name, url, store, proxy=None):
    key = next((k for k, p in PORTALS.items() if p["label"] == portal_name), None)
    if key and account_enabled(store, key):
        return portal_browser(key).fetch(url, store=store, proxy=proxy)
    return None


def account_status(store, key, enabled=True):
    data = load_state(store, account_key(key))
    runtime = mobile_status(store) if key == "mobile_de" else load_state(store, "portal.runtime.v1." + key)
    health = [h for h in store.list_portal_health() if h["portal"] == PORTALS[key]["label"]]
    last_success = max((h.get("last_success") or 0 for h in health), default=0) or None
    latest = max(health, key=lambda h: h.get("last_run", 0), default={})
    worker = None
    if key == "mobile_de":
        from . import mobile_runtime
        worker = mobile_runtime._instance
    else:
        worker = _workers.get(key)
    session = getattr(worker, "_account_session", None)
    auth = data.get("auth_state", "unverified") if data.get("enabled") else "disconnected"
    if auth == "authenticated" and time.time() - data.get("checked_at", 0) > AUTH_MAX_AGE:
        auth = "unverified"
    return {"key": key, "label": PORTALS[key]["label"], "enabled": enabled,
            "connected": bool(data.get("enabled")), "auth_state": auth,
            "checked_at": data.get("checked_at"), "last_authenticated_at": data.get("last_authenticated_at"),
            "session_active": bool(session and session["expires_at"] > time.time()),
            "search_status": "blocked" if runtime.get("blocked_until", 0) > time.time() else latest.get("status", "untested"),
            "last_search_success": last_success, "blocked_until": runtime.get("blocked_until", 0),
            "message": BLOCKED_LOGIN_MESSAGE if auth == "blocked" else "Die Anmeldung bestätigt keinen erfolgreichen Suchabruf."}


def _require_session(worker, session_id, owner):
    session = worker._account_session
    if not session or session["expires_at"] <= time.time():
        if session:
            try:
                worker._close()
            finally:
                worker._account_session = None
        raise AccountError("Anmeldefenster abgelaufen. Bitte erneut öffnen.", 410)
    if not secrets.compare_digest(session.get("owner", ""), owner) or not secrets.compare_digest(session["id"], session_id or ""):
        raise AccountError("Anmeldefenster gehört zu einer anderen Sitzung.", 403)
    return session


def _login_page(worker, key):
    pages = [p for p in worker._context.pages if not p.is_closed()]
    page = pages[-1] if pages else worker._page
    if page.url != "about:blank" and not allowed_navigation(key, page.url):
        raise AccountError("Weiterleitung außerhalb des Portals. Bitte die direkte Portal-Anmeldung verwenden.")
    return page


def _manual_budget(store, key):
    state = load_state(store, "portal.manual.v1." + key)
    now = time.time()
    if now - state.get("window", 0) >= 3600:
        state = {"window": now, "count": 0}
    if state.get("count", 0) >= 6:
        raise AccountError("Zu viele Anmeldeaufrufe. Bitte später erneut versuchen.", 429)
    state["count"] = state.get("count", 0) + 1
    save_state(store, "portal.manual.v1." + key, state)


def _connect(worker, key, store, proxy, owner):
    existing = worker._account_session
    if existing and existing["expires_at"] > time.time():
        if existing["owner"] != owner:
            raise AccountError("Das Anmeldefenster wird bereits verwendet.")
        return {"session_id": existing["id"], "expires_at": existing["expires_at"]}
    _manual_budget(store, key)
    worker._open(proxy)
    _guard_context(worker, key)
    if worker._page.is_closed():
        worker._page = worker._context.new_page()
    for old_page in worker._context.pages:
        if old_page is not worker._page:
            old_page.close()
    session = {"id": secrets.token_urlsafe(32), "owner": owner, "expires_at": time.time() + SESSION_SECONDS}
    worker._account_session = session
    data = load_state(store, account_key(key))
    data.update(enabled=True, auth_state="unverified")
    save_state(store, account_key(key), data)
    try:
        worker._page.goto(PORTALS[key]["url"], wait_until="domcontentloaded", timeout=30000)
    except Exception:
        # Keep the view usable for consent/challenges already rendered by the site.
        if worker._page.url == "about:blank":
            worker._account_session = None
            raise AccountError("Anmeldeseite konnte nicht geladen werden.", 502) from None
    return {"session_id": session["id"], "expires_at": session["expires_at"]}


def _snapshot(worker, key, store, session_id, owner):
    session = _require_session(worker, session_id, owner)
    page = _login_page(worker, key)
    state = _observe(store, key, page)
    # Chrome on the GPU compositor has no emulated viewport; report the real size.
    size = page.viewport_size or page.evaluate("({width: innerWidth, height: innerHeight})")
    session["size"] = (int(size["width"]), int(size["height"]))
    # Sensitive pixels stay in memory and are never logged, cached or written to disk.
    image = base64.b64encode(page.screenshot(type="jpeg", quality=70, timeout=7000)).decode("ascii")
    return {"image": image, "width": session["size"][0], "height": session["size"][1],
            "auth_state": state, "host": urlparse(page.url).hostname,
            "expires_at": session["expires_at"],
            "message": BLOCKED_LOGIN_MESSAGE if state == "blocked" else "Die Suche dieses Portals pausiert, solange dieses Anmeldefenster geöffnet ist."}


def _input(worker, key, store, payload, owner):
    session = _require_session(worker, payload.get("session_id"), owner)
    now = time.time()
    if now - session.get("last_input", 0) < .08:
        raise AccountError("Bitte kurz warten.", 429)
    session["last_input"] = now
    page = _login_page(worker, key)
    # Check the current page, not just the last screenshot. A redirect may have
    # replaced a form with a denial between rendering and submitting input.
    if _access_state(page.content()) == "blocked":
        raise AccountError("Portalzugriff blockiert. Auf dieser Seite ist keine Anmeldung möglich.")
    action = payload.get("action")
    if action == "click":
        x, y = payload.get("x"), payload.get("y")
        width, height = session.get("size", (1440, 900))
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or not (0 <= x < width and 0 <= y < height):
            raise AccountError("Ungültige Position", 400)
        page.mouse.click(x, y)
    elif action == "text":
        value = payload.get("text")
        if not isinstance(value, str) or not 1 <= len(value) <= 2048:
            raise AccountError("Ungültige Eingabe", 400)
        page.keyboard.insert_text(value)
    elif action == "key":
        keyname = payload.get("key")
        if keyname not in {"Tab", "Shift+Tab", "Enter", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "Control+A"}:
            raise AccountError("Taste nicht zugelassen", 400)
        page.keyboard.press(keyname)
    elif action == "scroll":
        delta = payload.get("delta", 0)
        if not isinstance(delta, (int, float)) or not -900 <= delta <= 900:
            raise AccountError("Ungültiger Scrollwert", 400)
        page.mouse.wheel(0, delta)
    else:
        raise AccountError("Unbekannte Aktion", 400)
    return {"ok": True}


def _check(worker, key, store, proxy, owner, session_id=None):
    session = worker._account_session
    if session and session["expires_at"] > time.time():
        _require_session(worker, session_id, owner)
    else:
        # A check without an open login is a network request; honor portal pauses.
        runtime = mobile_status(store) if key == "mobile_de" else load_state(store, "portal.runtime.v1." + key)
        if runtime.get("blocked_until", 0) > time.time():
            raise AccountError("Schutzpause aktiv. Eine Prüfung ist nach Ablauf möglich.")
        _manual_budget(store, key)
        worker._open(proxy)
        _guard_context(worker, key)
        worker._page.goto(PORTALS[key]["url"], wait_until="domcontentloaded", timeout=30000)
    _observe(store, key, _login_page(worker, key))
    return account_status(store, key)


def _close_login(worker, key, store, session_id, owner):
    _require_session(worker, session_id, owner)
    try:
        _observe(store, key, _login_page(worker, key))
    except Exception:
        pass  # Even a broken page must be closable; no raw browser errors/logs.
    finally:
        worker._account_session = None
        # Closing flushes Firefox's session data before the next automatic search.
        worker._close()
    return {"ok": True}


def _disconnect(worker, key, store):
    if worker._account_session and worker._account_session["expires_at"] > time.time():
        raise AccountError("Bitte zuerst das Anmeldefenster schließen.")
    worker._close()
    target = worker._profile_dir
    expected = profile_path(key)
    valid_names = {"mobile_de": {"firefox_profile", "chrome_profile"}, "autouncle": {"autouncle_profile"},
                   "kleinanzeigen": {"kleinanzeigen"}, "autoscout24": {"autoscout24"}}
    if (target != expected or target.name not in valid_names[key] or target.is_symlink()
            or target.resolve() != target.absolute() or target.parent.resolve() == target.resolve()):
        raise AccountError("Profilpfad konnte nicht sicher geprüft werden.")
    if target.exists():
        shutil.rmtree(target)
    save_state(store, account_key(key), {"enabled": False, "auth_state": "disconnected", "checked_at": time.time()})
    # Portal pause and listings intentionally survive unlinking an account.
    return {"ok": True}


def account_action(key, action, store, owner, *, proxy=None, payload=None):
    worker = portal_browser(key)
    payload = payload or {}
    if action == "connect":
        call = lambda: _connect(worker, key, store, proxy, owner)
    elif action == "session":
        call = lambda: _snapshot(worker, key, store, payload.get("session_id"), owner)
    elif action == "input":
        call = lambda: _input(worker, key, store, payload, owner)
    elif action == "check":
        call = lambda: _check(worker, key, store, proxy, owner, payload.get("session_id"))
    elif action == "close":
        call = lambda: _close_login(worker, key, store, payload.get("session_id"), owner)
    elif action == "disconnect":
        call = lambda: _disconnect(worker, key, store)
    else:
        raise AccountError("Unbekannte Aktion", 404)
    try:
        return worker._executor.submit(call).result()
    except AccountError:
        raise
    except (BrowserUnavailable, MobileDeferred):
        raise AccountError("Browser derzeit nicht verfügbar. Bitte später erneut versuchen.", 503) from None
    except Exception:
        # Playwright errors may contain submitted credentials: never surface/log them.
        raise AccountError("Browseraktion fehlgeschlagen. Bitte Ansicht aktualisieren.", 502) from None


def close_account_browsers():
    with _workers_lock:
        for worker in _workers.values():
            worker.close()
        _workers.clear()


def reap_expired_sessions():
    from . import mobile_runtime
    with _workers_lock:
        workers = list(_workers.values())
    if mobile_runtime._instance is not None:
        workers.append(mobile_runtime._instance)
    for worker in workers:
        session = worker._account_session
        if session and session["expires_at"] <= time.time():
            def reap(w=worker):
                current = w._account_session
                if current and current["expires_at"] <= time.time():
                    w._account_session = None
                    w._close()
            worker._executor.submit(reap).result()
