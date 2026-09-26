"""Deterministic tests: no portal traffic, cookies or production database."""
import json
import threading
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock

import pytest

from kfz_crawler.browser import BrowserBlocked, _is_block_page
from kfz_crawler.mobile_runtime import (
    MobileBrowser, MobileDeferred, MobilePageError, RequestControl, STATE_KEY,
    is_mobile_url, load_state, save_state, search_page_info, verify_filters,
    retry_after_seconds,
)
from kfz_crawler.models import Listing, SearchQuery
from kfz_crawler.portals.base import PortalPartialError
from kfz_crawler.portals.mobile_de import MobileDe
from kfz_crawler.storage import SeenStore


@pytest.fixture
def store():
    db = SeenStore(":memory:")
    yield db
    db.close()


def page_html(page=1, last=3, total=3, *, lid=None):
    lid = lid or str(10000000 + page)
    return f'''<h1>{total} Angebote</h1>
    <button aria-label="Weiter" data-testid="CarouselNextButton">Weiter</button>
    <article><a href="/fahrzeuge/details.html?id={lid}&amp;vc=Car&amp;s=Car">
      <h2 data-testid="base-result-listing-1-title">NEU Test EV Long Range</h2>
      <span data-testid="main-price-label">25.000 €¹</span>
      <div data-testid="listing-details-attributes">EZ 04/2022 • 54.000 km • 150 kW (204 PS) • Elektro</div>
    </a></article>
    <button disabled aria-label="Seite {page}">{page}</button>
    <button data-testid="pagination:next" aria-label="Weiter" {'disabled' if page == last else ''}>Weiter</button>'''


def fetch_pages(calls, last=3):
    def fetch(url):
        params = parse_qs(urlparse(url).query)
        page = int(params['pageNumber'][0])
        calls.append(page)
        return page_html(page, last, last)
    return fetch


def acknowledge(portal):
    save_state(portal.store, portal.checkpoint_key, portal.pending_checkpoint)


def test_native_ev_filters_round_outward_and_acc_is_radio():
    q = SearchQuery(name='EV', battery_from_kwh=65, ev_range_from=450, equipment=[34, 38, 133])
    params = parse_qs(urlparse(MobileDe()._build_url(q, 2)).query)
    assert params['bc'] == ['65']
    assert params['re'] == ['400']
    assert params['spc'] == ['ADAPTIVE_CRUISE_CONTROL']
    assert params['fe'] == ['ELECTRIC_HEATED_SEATS']
    assert params['sb'] == ['doc'] and params['od'] == ['down']
    assert 'bat' not in params


def test_real_pagination_takes_precedence_over_dealer_carousel():
    info = search_page_info(page_html(3, 3))
    assert info['page'] == 3 and info['has_next'] is False
    assert not info['empty']


@pytest.mark.parametrize('count', [10, 20, 100, 910])
def test_count_ending_in_zero_is_not_empty(count):
    assert not search_page_info(f'<h1>{count} Angebote</h1>')['empty']


def test_unverified_blank_is_not_empty_but_explicit_zero_is():
    assert not search_page_info('<html><body>Lädt...</body></html>')['empty']
    assert search_page_info('<h1>0 Angebote</h1>')['empty']
    assert search_page_info('<main>Keine passenden Fahrzeuge</main>')['empty']


def test_silently_dropped_filters_rejected_even_if_words_visible():
    url = MobileDe()._build_url(SearchQuery(name='EV', battery_from_kwh=65, ev_range_from=450, equipment=[133]), 1)
    info = search_page_info(page_html() + 'Batteriekapazität: ab 65 kWh Reichweite: ab 400 km Abstandstempomat')
    with pytest.raises(MobilePageError, match='nicht übernommen'):
        verify_filters(info, url)
    info['effective'] = [parse_qs(urlparse(url).query)]
    verify_filters(info, url)
    info['text'] = 'Reichweite: ab 400 km Abstandstempomat'
    with pytest.raises(MobilePageError, match='Batteriekapazität'):
        verify_filters(info, url)


def test_resume_across_restart_and_ack_only_after_data_commit(store):
    q = SearchQuery(name='EV')
    calls = []
    first = MobileDe(); first.store = store; first.page_budget = 2
    result = first._crawl_pages(q, fetch_pages(calls))
    assert calls == [1, 2] and len(result) == 2
    assert not first.complete_sweep
    assert load_state(store, first.checkpoint_key) == {}
    acknowledge(first)
    second = MobileDe(); second.store = store; second.page_budget = 2
    result2 = second._crawl_pages(q, fetch_pages(calls))
    assert calls == [1, 2, 2, 3]  # overlap, no missing page
    assert second.complete_sweep
    assert len(second.active_fingerprints) == 3
    assert {l.fingerprint for l in result + result2} == second.active_fingerprints


def test_single_page_budget_progresses_and_filter_edit_invalidates_cursor(store):
    p = MobileDe(); p.store = store; p.page_budget = 1
    q = SearchQuery(name='EV'); calls = []
    p._crawl_pages(q, fetch_pages(calls)); old_key = p.checkpoint_key; acknowledge(p)
    p._crawl_pages(q, fetch_pages(calls)); acknowledge(p)
    p._crawl_pages(replace(q, year_from=2022), fetch_pages(calls))
    assert calls == [1, 2, 1]
    assert p.checkpoint_key != old_key


def test_tolerant_supplement_and_delta_budget(store):
    p = MobileDe(); p.store = store
    q = SearchQuery(name='EV', battery_from_kwh=65, ev_range_from=450)
    urls = []
    def one(url):
        urls.append(url)
        return page_html(1, 1, 1)
    result = p._crawl_pages(q, one)
    assert len(result) == 1 and p.complete_sweep
    assert len(urls) == 2 and 'bc=65' in urls[0] and 'bc=' not in urls[1]
    acknowledge(p)
    calls = []
    p._crawl_pages(q, fetch_pages(calls, last=20))
    assert calls == [1, 2, 1, 2] and not p.complete_sweep
    assert p.pending_checkpoint is None and p.coverage['reason'] == 'delta'


def test_strict_search_does_not_add_battery_fallback():
    p = MobileDe(); calls = []
    p._crawl_pages(SearchQuery(name='strict', battery_from_kwh=65, ev_range_from=450, unknown_policy='strict'), fetch_pages(calls, 1))
    assert calls == [1]


def test_repeated_and_unverified_pages_preserve_partial_results():
    p = MobileDe()
    with pytest.raises(PortalPartialError) as err:
        p._crawl_pages(SearchQuery(name='EV'), lambda _: page_html())
    assert len(err.value.listings) == 1 and p.coverage['reason'] == 'repeated_page'
    pages = iter([page_html(), '<h1>Bitte warten</h1>'])
    with pytest.raises(PortalPartialError) as err:
        p._crawl_pages(SearchQuery(name='EV'), lambda _: next(pages))
    assert len(err.value.listings) == 1 and not p.complete_sweep


def test_large_query_splits_disjoint_price_intervals_with_same_budget(store):
    p = MobileDe(); p.store = store; p.page_budget = 3
    urls = []
    def fetch(url):
        urls.append(url)
        return page_html(1, 1, 1000 if len(urls) == 1 else 1)
    p._crawl_pages(SearchQuery(name='EV', price_to=30000), fetch)
    prices = [parse_qs(urlparse(u).query)['p'][0] for u in urls]
    assert prices == [':30000', '0:15000', '15001:30000']
    assert p.complete_sweep


def test_split_resumes_first_subrange_when_budget_exhausted(store):
    p = MobileDe(); p.store = store; p.page_budget = 1
    q = SearchQuery(name='EV', price_to=30000)
    p._crawl_pages(q, lambda _: page_html(1, 50, 1000)); acknowledge(p)
    urls = []
    p._crawl_pages(q, lambda u: urls.append(u) or page_html(1, 1, 1))
    assert parse_qs(urlparse(urls[0]).query)['p'] == ['0:15000']
    assert p.pending_checkpoint['variant'] == 1


def test_persisted_gate_covers_search_detail_images_and_restarts(store):
    now = [10000.0]
    gate = RequestControl(store, clock=lambda: now[0])
    assert gate.reserve('search') == 0
    assert RequestControl(store, clock=lambda: now[0]).reserve('detail') == 12
    gate = RequestControl(store, clock=lambda: now[0]); gate.blocked()
    for kind in ('search', 'detail', 'image'):
        with pytest.raises(MobileDeferred):
            RequestControl(store, clock=lambda: now[0]).reserve(kind)
    now[0] += 7201
    gate = RequestControl(store, clock=lambda: now[0]); gate.reserve('search'); gate.blocked()
    assert load_state(store, STATE_KEY)['blocked_until'] == now[0] + 6 * 3600


def test_hourly_attempt_limit_does_not_increase_block_counter(store):
    now = [10000.0]; gate = RequestControl(store, clock=lambda: now[0], interval=0)
    for _ in range(10):
        gate.reserve('detail')
    with pytest.raises(MobileDeferred, match='Stundenbudget'):
        gate.reserve('detail')
    assert gate.state.get('block_count', 0) == 0
    now[0] += 3600
    assert gate.reserve('detail') == 0


def test_retry_after_parses_seconds_and_date():
    assert retry_after_seconds('3600', 0) == 3600
    assert retry_after_seconds('Thu, 01 Jan 1970 02:00:00 GMT', 0) == 7200
    assert retry_after_seconds('invalid') == 0


@pytest.mark.parametrize('url', ['http://suchen.mobile.de/x', 'https://mobile.de.evil/x', 'https://evil@www.mobile.de/x', 'https://suchen.mobile.de:bad/x', 'https://127.0.0.1/x'])
def test_request_targets_are_allowlisted(url):
    assert not is_mobile_url(url)


def test_normal_page_scripts_do_not_trigger_challenge():
    assert not _is_block_page('<script>function captcha(){}</script><h1>91 Angebote</h1>')
    assert _is_block_page('<h1>Access Denied</h1>')


def test_worker_serializes_calls_and_reuses_thread():
    browser = MobileBrowser(); seen = []
    browser._fetch = lambda *args: seen.append(threading.get_ident()) or 'ok'
    browser._close = lambda: seen.append(threading.get_ident())
    try:
        assert browser.fetch('https://suchen.mobile.de/fahrzeuge/search.html') == 'ok'
        assert browser.fetch('https://suchen.mobile.de/fahrzeuge/details.html?id=1', kind='detail') == 'ok'
    finally:
        browser.close(); browser.close()
    assert len(seen) == 3 and len(set(seen)) == 1 and seen[0] != threading.get_ident()


def test_429_stops_all_followup_requests_without_recreating_identity(store, monkeypatch):
    browser = MobileBrowser()
    browser._page = Mock()
    browser._page.goto.return_value = SimpleNamespace(status=429, headers={'retry-after': '10800'})
    browser._open = Mock()
    browser._close = Mock()
    monkeypatch.setattr('kfz_crawler.mobile_runtime.time.sleep', lambda _: None)
    try:
        with pytest.raises(BrowserBlocked):
            browser.fetch('https://suchen.mobile.de/fahrzeuge/search.html', store=store)
        with pytest.raises(MobileDeferred):
            browser.fetch('https://suchen.mobile.de/fahrzeuge/details.html?id=1', store=store, kind='detail')
        with pytest.raises(MobileDeferred):
            browser.fetch_image('https://img.classistatic.de/cert.jpg', store=store)
        assert browser._page.goto.call_count == 1
        browser._close.assert_not_called()
    finally:
        browser.close()


def test_partial_crawl_never_claims_complete_for_missing_cards():
    p = MobileDe()
    html = page_html(1, 1) + '<a href="/fahrzeuge/details.html?id=12345678">Unrecognized card layout</a>'
    with pytest.raises(PortalPartialError):
        p._crawl_pages(SearchQuery(name='EV'), lambda _: html)
    assert p.coverage['reason'] == 'parser_incomplete' and not p.complete_sweep


def test_browser_waits_for_selected_page_not_just_requested_url(store, monkeypatch):
    browser = MobileBrowser(); page = Mock()
    target = 'https://suchen.mobile.de/fahrzeuge/search.html?pageNumber=2'
    page.url = target
    page.goto.return_value = SimpleNamespace(status=200, headers={})
    page.get_by_text.return_value.count.return_value = 0
    page.content.side_effect = [page_html(1), page_html(1), page_html(2), page_html(2), page_html(2)]
    browser._page = page; browser._open = Mock(); browser._close = Mock()
    monkeypatch.setattr('kfz_crawler.mobile_runtime.time.sleep', lambda _: None)
    try:
        assert search_page_info(browser.fetch(target, store=store))['page'] == 2
        assert page.content.call_count == 5
        assert load_state(store, STATE_KEY)['status'] == 'ok'
    finally:
        browser.close()


def test_browser_server_error_preserves_failure_status(store):
    browser = MobileBrowser(); browser._page = Mock()
    browser._page.goto.return_value = SimpleNamespace(status=503, headers={})
    browser._open = Mock(); browser._close = Mock()
    try:
        with pytest.raises(MobilePageError, match='503'):
            browser.fetch('https://suchen.mobile.de/fahrzeuge/search.html', store=store)
        assert load_state(store, STATE_KEY)['status'] == 'error'
        assert load_state(store, STATE_KEY).get('blocked_until', 0) == 0
    finally:
        browser.close()


def test_legacy_browser_entrypoints_cannot_bypass_shared_worker(monkeypatch):
    from kfz_crawler.browser import fetch_rendered, rendered_session, fetch_rendered_batch
    worker = Mock(); worker.fetch.return_value = 'html'
    monkeypatch.setattr('kfz_crawler.mobile_runtime.mobile_browser', lambda: worker)
    search = 'https://suchen.mobile.de/fahrzeuge/search.html'
    detail = 'https://suchen.mobile.de/fahrzeuge/details.html?id=1'
    assert fetch_rendered(search, max_retries=99) == 'html'
    with rendered_session(profile_dir='ignored-legacy-profile') as fetch:
        assert fetch(detail) == 'html'
    assert fetch_rendered_batch(search, [detail]) == ('html', {detail: 'html'})
    assert worker.fetch.call_count == 4


def test_record_card_does_not_mix_dealer_recommendations():
    html = page_html(1, 1, 1).replace('</article>', '<aside>Anderes Auto Akku 120 kWh SoH 60%</aside></article>')
    car = MobileDe()._parse_cards(html)[0]
    assert car.title == 'Test EV Long Range' and car.price == 25000
    assert 'Anderes Auto' not in car.body and car.battery_soh is None


def test_gpu_browser_only_with_compositor_socket(tmp_path, monkeypatch):
    from kfz_crawler import mobile_runtime
    chrome = tmp_path / "chrome"
    chrome.touch()
    runtime = tmp_path / "wayland"
    runtime.mkdir()
    monkeypatch.setattr(mobile_runtime, "CHROME_BINARY", chrome)
    monkeypatch.setattr(mobile_runtime, "PROFILE_DIR", tmp_path / "firefox_profile")
    monkeypatch.setattr(mobile_runtime, "CHROME_PROFILE_DIR", tmp_path / "chrome_profile")
    monkeypatch.setenv("KFZ_WAYLAND_RUNTIME", str(runtime))
    # Lock file alone means the compositor is not ready.
    (runtime / "wayland-0.lock").touch()
    assert mobile_runtime.wayland_socket() is None
    assert mobile_runtime.mobile_profile_dir() == tmp_path / "firefox_profile"

    (runtime / "wayland-0").touch()
    worker = MobileBrowser()
    portal = MobileBrowser(tmp_path / "autouncle_profile")
    try:
        assert worker.engine == "chrome-gpu"
        assert worker._profile_dir == tmp_path / "chrome_profile"
        # Other portals keep their own Firefox profile.
        assert portal.engine == "firefox"
    finally:
        worker.close()
        portal.close()

    chrome.unlink()
    assert mobile_runtime.wayland_socket() is None


def test_gpu_browser_disabled_without_runtime(monkeypatch):
    from kfz_crawler import mobile_runtime
    monkeypatch.delenv("KFZ_WAYLAND_RUNTIME", raising=False)
    worker = MobileBrowser()
    try:
        assert worker.engine == "firefox"
    finally:
        worker.close()
