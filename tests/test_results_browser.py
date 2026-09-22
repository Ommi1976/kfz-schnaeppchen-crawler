"""Render representative listing data in the real UI, without portal traffic."""
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture
def results_page():
    pw_api = pytest.importorskip("playwright.sync_api")
    root = Path(__file__).resolve().parents[1] / "kfz_schnaeppchen/kfz_crawler/web"

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def translate_path(self, path):
            return super().translate_path(path.replace("/static/", "/", 1))

    with pw_api.sync_playwright() as pw:
        if not Path(pw.chromium.executable_path).exists():
            pytest.skip("Local Chromium not installed")
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        state = {"deals_error": False, "status_error": False}
        listing = {
            "title": "Test EV mit SoH", "portal": "AutoUncle", "url": "https://www.autouncle.de/de/d/123-test",
            "price": 25000, "year": 2022, "mileage": 40000, "battery_soh": 94.6,
            "battery_kwh": 77, "ev_range_km": 520, "field_evidence": {
                "battery_soh": {"source": "fahrzeugakte", "confidence": .95, "evidence": "94,6 % sehr gut"}},
        }
        listing["origin_portal"] = "mobile.de"
        def api(route):
            path = route.request.url.split("/api/", 1)[1].split("?", 1)[0]
            if state.get(path + "_error"):
                route.fulfill(status=503, json={"detail": "Testausfall"})
                return
            deals = [listing, {**listing, "title": "Test EV ohne SoH", "battery_soh": None, "origin_portal": None}]
            if parse_qs(urlsplit(route.request.url).query).get("portal") == ["mobile.de"]:
                deals = deals[:1]
            values = {
                "meta": {k: [] for k in ("fuel", "transmission", "body_type", "seller", "doors", "emission_class", "drivetrain", "unknown_policy")},
                "status": {"version": "test", "searches": [], "portals_active": ["autouncle"], "crawl_progress": []},
                "deals": {"deals": deals, "count": len(deals), "portal_counts": {"AutoUncle": 2},
                          "mobile_coverage": {"direct": 0, "via_autouncle": 1, "direct_status": "blocked"}},
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(values.get(path, {})))
        page.route("**/api/**", api)
        try:
            yield page, state, f"http://127.0.0.1:{server.server_port}/", pw_api.expect
        finally:
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_soh_listing_does_not_break_results_and_filters(results_page):
    page, state, url, expect = results_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.goto(url)
    expect(page.locator("#deals-body tr")).to_have_count(2)
    expect(page.locator("#deals-body")).to_contain_text("94.6 % SoH")
    expect(page.locator(".soh-badge")).to_contain_text("↔")
    page.locator("#qf-soh-min").fill("90")
    expect(page.locator("#deals-body tr")).to_have_count(1)
    expect(page.locator("#deals-body")).to_contain_text("Test EV mit SoH")
    assert not errors


def test_failed_status_does_not_prevent_results(results_page):
    page, state, url, expect = results_page
    state["status_error"] = True
    page.goto(url)
    expect(page.locator("#deals-body tr")).to_have_count(2)


def test_mobile_source_filter_keeps_autouncle_link_and_unique_total(results_page):
    page, state, url, expect = results_page
    page.goto(url)
    expect(page.locator("#deals-body tr")).to_have_count(2)
    expect(page.locator('#portal-filters [data-portal=""] .p-count')).to_have_text("2")
    mobile = page.locator('#portal-filters [data-portal="mobile.de"]')
    expect(mobile.locator(".p-count")).to_have_text("1")
    mobile.click()
    expect(page.locator("#deals-body tr")).to_have_count(1)
    expect(page.locator("#deals-body")).to_contain_text("mobile.de via AutoUncle")
    expect(page.locator("#deals-body a.link")).to_have_attribute("href", "https://www.autouncle.de/de/d/123-test")
    expect(page.locator('#portal-filters [data-portal=""] .p-count')).to_have_text("2")


def test_results_error_is_visible_and_recovers(results_page):
    page, state, url, expect = results_page
    state["deals_error"] = True
    page.goto(url)
    expect(page.locator("#deals-error")).to_be_visible()
    expect(page.locator("#deals-body")).not_to_contain_text("lädt…")
    state["deals_error"] = False
    page.locator("#refresh").click()
    expect(page.locator("#deals-body tr")).to_have_count(2)
    expect(page.locator("#deals-error")).to_be_hidden()
    state["deals_error"] = True
    page.locator("#refresh").click()
    expect(page.locator("#deals-error")).to_be_visible()
    expect(page.locator("#deals-body tr")).to_have_count(2)
