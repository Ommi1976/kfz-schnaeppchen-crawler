import json
import time
from unittest.mock import Mock

import pytest

from kfz_crawler.battery_analyzer import mobile_detail_snapshot, parse_mobile_de_detail_html
from kfz_crawler.mobile_runtime import MobileDeferred, RequestControl, load_state, save_state, STATE_KEY
from kfz_crawler.models import Listing, SearchQuery
from kfz_crawler.portals.mobile_de import MobileDe
from kfz_crawler.storage import SeenStore


DETAIL = '''<main><h1>Test EV</h1>
<article data-testid="vip-battery-information-box">Batterie-Status 94,6 % Sehr gut
Reichweite (WLTP) 450 km Batteriekapazität 77 kWh netto</article>
<article data-testid="vip-technical-data-box">Erstzulassung 08/2023 Leistung 150 kW (204 PS)</article>
<article data-testid="vip-vehicle-description">Unfallfrei, Sitzheizung, Abstandstempomat.</article>
<img src="https://img.classistatic.de/uuid-document.jpg" alt="Batteriezertifikat">
<img src="https://img.classistatic.de/car.jpg" alt="Test EV SoH 94,6%">
<article data-testid="vehicle-recommendations-carousel">Anderes Auto, SoH 70 %, Reichweite 800 km
<img src="https://img.classistatic.de/other-soh.jpg"></article>
<script>privateSession='never-cache-this'</script>
<img src="http://127.0.0.1/admin" alt="Batteriezertifikat"></main>'''


@pytest.fixture
def store():
    db = SeenStore(':memory:')
    yield db
    db.close()


def listing(lid):
    return Listing(portal='mobile.de', title='Test EV', raw_id=str(lid),
        url=f'https://suchen.mobile.de/fahrzeuge/details.html?id={lid}',
        price=20000, fuel='elektro', mileage=30000, body='Kurze Karte')


def test_sanitized_cache_preserves_evidence_and_excludes_other_cars():
    html = mobile_detail_snapshot(DETAIL)
    assert 'never-cache' not in html and '127.0.0.1' not in html and 'Anderes Auto' not in html
    assert 'other-soh' not in html
    car = listing(1)
    parse_mobile_de_detail_html(html, car)
    assert car.battery_soh == 94.6
    assert car.battery_soh_level in ('belegt', 'bestaetigt')
    assert car.ev_range_km == 450 and car.ev_range_standard == 'wltp'
    assert car.battery_kwh == 77 and car.battery_observed_kind == 'netto'
    assert car.year == 2023 and car.year_kind == 'ez' and car.first_registration_month == 8
    assert car.field_evidence['certificate_images']['urls'] == ['https://img.classistatic.de/uuid-document.jpg']
    old_body = car.body
    parse_mobile_de_detail_html(html, car)
    assert car.body == old_body


def test_backlog_is_enriched_without_refreshing_seen_price_or_deal(store, monkeypatch):
    old = listing(10000001); old.market_price = 30000; old.is_deal = True
    store.record_listing('EV', old)
    store.conn.execute('UPDATE deals SET last_seen=123, first_seen=100 WHERE fingerprint=?', (old.fingerprint,)); store.conn.commit()
    worker = Mock(); worker.fetch.return_value = DETAIL
    monkeypatch.setattr('kfz_crawler.mobile_runtime.mobile_browser', lambda: worker)
    p = MobileDe(); p.store = store
    assert p.enrich([], SearchQuery(name='EV')) == []
    row = dict(store.conn.execute('SELECT * FROM deals').fetchone())
    assert row['battery_soh'] == 94.6 and row['year'] == 2023
    assert row['last_seen'] == 123 and row['first_seen'] == 100
    assert row['price'] == 20000 and row['market_price'] == 30000 and row['is_deal'] == 1
    assert worker.fetch.call_count == 1
    p.enrich([], SearchQuery(name='EV'))
    assert worker.fetch.call_count == 1  # Persisted positive cache


def test_unchecked_backlog_progresses_under_three_detail_limit(store, monkeypatch):
    for lid in range(10000001, 10000008):
        store.record_listing('EV', listing(lid))
    worker = Mock(); worker.fetch.return_value = DETAIL
    monkeypatch.setattr('kfz_crawler.mobile_runtime.mobile_browser', lambda: worker)
    p = MobileDe(); p.store = store
    for expected in (3, 6, 7):
        p.enrich([], SearchQuery(name='EV'))
        assert worker.fetch.call_count == expected
    assert store.conn.execute('SELECT COUNT(*) FROM deals WHERE battery_soh=94.6').fetchone()[0] == 7


def test_cached_details_still_applied_after_network_budget_used(store, monkeypatch):
    cars = [listing(x) for x in range(1, 5)]
    save_state(store, 'mobile.detail.v1.4', {'html': mobile_detail_snapshot(DETAIL), 'checked_at': time.time(), 'until': time.time() + 3600})
    worker = Mock(); worker.fetch.return_value = DETAIL
    monkeypatch.setattr('kfz_crawler.mobile_runtime.mobile_browser', lambda: worker)
    p = MobileDe(); p.store = store
    p.enrich(cars, SearchQuery(name='EV'))
    assert worker.fetch.call_count == 3 and all(c.battery_soh == 94.6 for c in cars)


def test_deferred_detail_is_not_negative_cached(store, monkeypatch):
    worker = Mock(); worker.fetch.side_effect = MobileDeferred('budget')
    monkeypatch.setattr('kfz_crawler.mobile_runtime.mobile_browser', lambda: worker)
    p = MobileDe(); p.store = store
    p.enrich([listing(1)], SearchQuery(name='EV'))
    assert load_state(store, 'mobile.detail.v1.1') == {}


def test_deferred_network_still_applies_later_cache(store, monkeypatch):
    save_state(store, 'mobile.detail.v1.2', {'html': mobile_detail_snapshot(DETAIL), 'checked_at': time.time(), 'until': time.time() + 3600})
    worker = Mock(); worker.fetch.side_effect = MobileDeferred('budget')
    monkeypatch.setattr('kfz_crawler.mobile_runtime.mobile_browser', lambda: worker)
    p = MobileDe(); p.store = store
    cars = [listing(1), listing(2)]
    p.enrich(cars, SearchQuery(name='EV'))
    assert worker.fetch.call_count == 1 and cars[1].battery_soh == 94.6


def test_local_recheck_keeps_net_gross_and_soh_evidence(store):
    car = listing(1)
    car.battery_kwh = 58; car.battery_net_kwh = 58; car.battery_gross_kwh = 62
    car.battery_observed_kind = 'netto'
    car.battery_soh = 95; car.battery_soh_level = 'belegt'
    store.record_listing('EV', car)
    store.purge_unmatching_deals('EV', SearchQuery(name='EV', battery_from_kwh=62))
    assert len(store.list_deals(search_name='EV')) == 1
    assert store.list_deals(search_name='EV')[0]['battery_soh_level'] == 'belegt'


def test_failed_ocr_download_is_not_cached_as_no_soh(store, monkeypatch):
    import kfz_crawler.battery_analyzer as analyzer
    analyzer._URL_SOH_CACHE.clear()
    car = listing(1); car.image_urls = ['https://img.classistatic.de/cert.jpg']
    store.record_listing('EV', car)
    monkeypatch.setattr(analyzer, 'HAS_OCR', True)
    worker = Mock(); worker.fetch_image.side_effect = MobileDeferred('budget')
    monkeypatch.setattr('kfz_crawler.mobile_runtime.mobile_browser', lambda: worker)
    analyzer.run_background_image_enrichment(store)
    assert analyzer._URL_SOH_CACHE == {}
    assert store.conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'mobile.ocr.%'").fetchone()[0] == 0
    assert car.fingerprint not in analyzer._OCR_TRIED_FP


def test_upgrade_keeps_existing_legacy_block(store):
    store.record_portal_run('EV', 'mobile.de', 'blocked', error='HTTP 403')
    with pytest.raises(MobileDeferred):
        RequestControl(store).reserve('search')
    assert load_state(store, STATE_KEY)['status'] == 'blocked'


def test_detail_write_failure_does_not_destroy_successful_snapshot(store, monkeypatch):
    store.record_listing('EV', listing(1))
    worker = Mock(); worker.fetch.return_value = DETAIL
    monkeypatch.setattr('kfz_crawler.mobile_runtime.mobile_browser', lambda: worker)
    monkeypatch.setattr(store, 'update_mobile_details', Mock(side_effect=RuntimeError('DB failed')))
    p = MobileDe(); p.store = store
    with pytest.raises(RuntimeError, match='DB failed'):
        p.enrich([], SearchQuery(name='EV'))
    assert '94,6' in load_state(store, 'mobile.detail.v1.1')['html']


@pytest.mark.parametrize('fail_write', [False, True])
def test_search_commits_cursor_after_listings_only(store, monkeypatch, fail_write):
    from kfz_crawler.main import run_search, PortalSearchResult
    from kfz_crawler.config import Config, Settings, NotifyConfig
    cfg = Config(settings=Settings(home_zip=''), portals={'mobile_de': True}, searches=[], notify=NotifyConfig())
    car = listing(1)
    result = PortalSearchResult('mobile.de', [car], raw_count=1, complete=False, status='partial',
        checkpoint_key='mobile.search.test', checkpoint={'page': 3})
    monkeypatch.setattr('kfz_crawler.main._search_one_portal', lambda *a: result)
    sync = Mock(); monkeypatch.setattr(store, 'sync_active_deals', sync)
    if fail_write:
        monkeypatch.setattr(store, 'record_listings', Mock(side_effect=RuntimeError('DB write failed')))
        with pytest.raises(RuntimeError, match='DB write'):
            run_search(cfg, SearchQuery(name='EV'), store)
        assert load_state(store, result.checkpoint_key) == {}
    else:
        run_search(cfg, SearchQuery(name='EV'), store)
        assert load_state(store, result.checkpoint_key) == {'page': 3}
        sync.assert_called_once_with('EV', {})
        assert store.conn.execute('SELECT COUNT(*) FROM deals').fetchone()[0] == 1
