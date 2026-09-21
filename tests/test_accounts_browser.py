"""Real local Firefox + Chromium smoke test with entirely synthetic portal pages.

Skipped when Playwright browser binaries are absent (the standard CI unit job).
No real portal request, account or password is used.
"""
import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest


def test_interactive_login_ui_and_shared_profile(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn
    from kfz_crawler import portal_accounts as accounts, mobile_runtime
    from kfz_crawler.web import app

    with playwright.sync_playwright() as pw:
        if not Path(pw.firefox.executable_path).exists() or not Path(pw.chromium.executable_path).exists():
            pytest.skip("Local Playwright browser binaries not installed")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("KFZ_DB_PATH", str(tmp_path / "ui.db"))
        monkeypatch.setattr(mobile_runtime, "PROFILE_DIR", tmp_path / "firefox_profile")
        async def idle(_app):
            await asyncio.Future()
        monkeypatch.setattr("kfz_crawler.web._scheduler", idle)
        html = """<!doctype html><html><body style='margin:0;font:20px Arial'>
          <h1>Testportal</h1><input id='mail' style='position:absolute;left:20px;top:80px;width:300px;height:40px'>
          <input id='pass' type='password' style='position:absolute;left:20px;top:140px;width:300px;height:40px'>
          <button id='login' style='position:absolute;left:20px;top:210px;width:160px;height:40px'
            onclick="document.cookie='test_session=synthetic; Max-Age=3600; Secure; SameSite=Lax'; document.body.innerHTML='<button>Abmelden</button><h1>Angemeldet</h1>'">Anmelden</button>
          </body></html>"""
        def synthetic_routes(worker, key):
            if getattr(worker, "_guarded_context", None) is worker._context:
                return
            worker._context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
            worker._guarded_context = worker._context
        monkeypatch.setattr(accounts, "_guard_context", synthetic_routes)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
            log_level="error", access_log=False, proxy_headers=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time()+10
        while not server.started and time.time() < deadline:
            time.sleep(.05)
        assert server.started
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(f"http://127.0.0.1:{port}/")
            page.get_by_role("button", name="Portalkonten", exact=True).click()
            page.locator(".accounts-access summary").click()
            page.locator("#accounts-token").fill(app.state.store.ingest_token())
            page.get_by_role("button", name="Token verwenden", exact=True).click()
            connect = page.get_by_role("button", name="mobile.de: Verbinden", exact=True)
            playwright.expect(connect).to_be_enabled()
            connect.click()
            screen = page.locator("#accounts-screen")
            playwright.expect(screen).to_be_visible(timeout=30000)
            playwright.expect(screen).to_have_attribute("aria-disabled", "false", timeout=10000)

            def click_remote(x, y):
                playwright.expect(screen).to_have_attribute("aria-disabled", "false", timeout=10000)
                rect = screen.bounding_box()
                screen.click(position={"x":x * rect["width"]/1440, "y":y * rect["height"]/900})
                playwright.expect(screen).to_have_attribute("aria-disabled", "false", timeout=10000)

            click_remote(100,100)
            page.locator("#accounts-text").fill("synthetic@example.test")
            page.get_by_role("button", name="Text senden", exact=True).click()
            playwright.expect(page.locator("#accounts-text")).to_have_value("")
            click_remote(100,160)
            page.locator("#accounts-text").fill("synthetic-password")
            page.get_by_role("button", name="Text senden", exact=True).click()
            click_remote(100,230)
            page.get_by_role("button", name="Anmeldung prüfen", exact=True).click()
            playwright.expect(page.locator("#accounts-session-auth")).to_have_text("Angemeldet bestätigt", timeout=10000)
            page.get_by_role("button", name="Schließen & Profil behalten", exact=True).click()
            playwright.expect(page.locator("#accounts-session")).not_to_be_visible(timeout=10000)
            card = page.locator('[data-portal="mobile_de"]')
            playwright.expect(card).to_contain_text("Angemeldet bestätigt")
            page.screenshot(path=str(tmp_path / "accounts-desktop.png"))
            print(f"UI_SCREENSHOT: {tmp_path / 'accounts-desktop.png'}")
            assert not errors
            # The account profile belongs to the exact same worker as the search.
            worker = mobile_runtime.mobile_browser()
            assert worker._account_session is None and worker._context is None
            def reopen():
                worker._open(None)
                return any(c["name"] == "test_session" for c in worker._context.cookies())
            assert worker._executor.submit(reopen).result()
            # Narrow viewport must keep every account card and control accessible.
            page.set_viewport_size({"width":390,"height":844})
            assert page.locator(".accounts-card").count() == 4
            page.screenshot(path=str(tmp_path / "accounts-mobile.png"))
        finally:
            browser.close()
            server.should_exit = True
            thread.join(timeout=15)
            assert not thread.is_alive()
