import pytest
from kfz_crawler.models import Listing
from kfz_crawler.storage import SeenStore


@pytest.fixture
def store():
    s = SeenStore(":memory:")
    yield s
    s.close()


def test_searches_crud(store):
    assert store.count_searches() == 0

    spec = {
        "name": "Golf 7 Diesel",
        "make": "volkswagen",
        "model": "golf",
        "active": True,
        "zip_code": "66111",
        "radius_km": 50,
    }
    created = store.create_search(spec)
    assert created["id"] is not None
    assert created["name"] == "Golf 7 Diesel"
    assert created["zip_code"] == "66111"
    assert store.count_searches() == 1

    # Get
    fetched = store.get_search(created["id"])
    assert fetched["name"] == "Golf 7 Diesel"

    # Update
    updated = store.update_search(created["id"], {**spec, "name": "Golf 7 TDI Navi", "active": False})
    assert updated["name"] == "Golf 7 TDI Navi"
    assert updated["active"] is False

    # Delete
    assert store.delete_search(created["id"]) is True
    assert store.count_searches() == 0


def test_seen_and_deal_storage(store):
    l = Listing(
        portal="AutoScout24",
        title="VW Golf",
        url="http://x/1",
        price=15000,
        year=2019,
        mileage=60000,
        is_deal=True,
    )
    assert store.is_new(l) is True
    store.mark_seen(l)
    assert store.is_new(l) is False

    store.record_listing("Golf Suche", l)
    assert store.deal_count(deals_only=True) == 1
    assert store.total_count() == 1

    deals = store.list_deals(deals_only=True)
    assert len(deals) == 1
    assert deals[0]["title"] == "VW Golf"
    assert deals[0]["price"] == 15000

    # Cross-run duplicate test
    assert store.similar_exists(year=2019, mileage=60000, price=15000) is True
    assert store.similar_exists(year=2019, mileage=90000) is False


def test_settings_storage(store):
    assert store.get_setting("token", "default") == "default"
    store.set_setting("token", "secret123")
    assert store.get_setting("token") == "secret123"
    store.set_setting("token", "secret456")
    assert store.get_setting("token") == "secret456"


def test_sync_active_deals(store):
    l1 = Listing(portal="AS24", title="Car 1", url="http://x/1", price=10000, year=2020, mileage=50000)
    l2 = Listing(portal="AS24", title="Car 2", url="http://x/2", price=12000, year=2020, mileage=60000)
    l3 = Listing(portal="mobile.de", title="Car 3", url="http://x/3", price=15000, year=2021, mileage=30000)

    store.record_listing("Suche 1", l1)
    store.record_listing("Suche 1", l2)
    store.record_listing("Suche 1", l3)
    assert store.total_count() == 3

    # Simuliere Folgelauf: l1 ist noch aktiv, l2 wurde auf AS24 gelöscht
    # mobile.de hatte in diesem Lauf 0 Treffer (z.B. Block) -> l3 bleibt geschützt
    portal_active = {
        "AS24": {l1.fingerprint},
        "mobile.de": set(),  # leer -> keine Bereinigung für mobile.de
    }

    stale = store.sync_active_deals("Suche 1", portal_active)
    assert stale == 1
    assert store.total_count() == 3  # Historie bleibt nachvollziehbar erhalten

    # include_stale: hier wird gerade geprüft, wie veraltete markiert werden.
    remaining_titles = {d["title"] for d in store.list_deals(include_stale=True)}
    assert "Car 1" in remaining_titles
    assert "Car 3" in remaining_titles
    assert "Car 2" in remaining_titles
    car2 = next(d for d in store.list_deals(include_stale=True) if d["title"] == "Car 2")
    assert car2["is_stale"] == 1


def test_discovered_ev_models(store):
    is_new, rec = store.record_discovered_ev_model("Lucid Air Pure 88 kWh", battery_kwh=88.0, ev_range_km=650)
    assert is_new is True
    assert rec["count"] == 1
    assert rec["avg_battery_kwh"] == 88.0

    # Zweiter Fund desselben Modells mit leichten Abweichungen
    is_new2, rec2 = store.record_discovered_ev_model("Lucid Air Pure 88 kWh AWD", battery_kwh=90.0, ev_range_km=670)
    assert is_new2 is False
    assert rec2["count"] == 2
    assert rec2["avg_battery_kwh"] == 89.0  # Durchschnitt (88 + 90) / 2

    discovered = store.list_discovered_ev_models()
    assert len(discovered) == 1
    # Inseratsdaten werden nie ungeprüft als Referenzdaten freigeschaltet.
    assert discovered[0]["status"] == "discovered"
    assert len(store.get_approved_ev_models()) == 0

    # Status manuell ändern (z. B. auf rejected)
    ok = store.set_discovered_ev_status(rec["model_key"], "rejected")
    assert ok is True
    assert len(store.list_discovered_ev_models(status="rejected")) == 1
    assert len(store.list_discovered_ev_models(status="approved")) == 0


def test_portal_health_and_failed_run_marks_stale(store):
    listing = Listing(portal="mobile.de", title="EV", url="https://example.test/ev")
    store.record_listing("EV-Suche", listing)
    store.record_portal_run("EV-Suche", "mobile.de", "blocked", error="HTTP 429")
    assert store.mark_portal_stale("EV-Suche", "mobile.de") == 1
    health = store.list_portal_health("EV-Suche")[0]
    assert health["status"] == "blocked"
    assert health["error"] == "HTTP 429"
    assert store.list_deals(include_stale=True)[0]["is_stale"] == 1


def test_mobile_portal_cooldown_after_block_and_partial(store):
    """Schutzpause eskaliert 2 h -> 6 h -> 24 h und wird bei Erfolg zurückgesetzt."""
    # Erster Block: kurze Pause, ein Ausreißer soll den Lauf nicht lange lahmlegen.
    store.record_portal_run("EV-Suche", "mobile.de", "blocked", error="HTTP 403")
    remaining = store.portal_cooldown_remaining("EV-Suche", "mobile.de")
    assert 1.9 * 3600 < remaining <= 2 * 3600

    # Zweiter Block in Folge: das Portal lehnt ernsthaft ab.
    store.record_portal_run("EV-Suche", "mobile.de", "blocked", error="HTTP 403")
    remaining = store.portal_cooldown_remaining("EV-Suche", "mobile.de")
    assert 5.9 * 3600 < remaining <= 6 * 3600

    # Dritter und jeder weitere: Höchstwert, nicht darüber hinaus.
    store.record_portal_run("EV-Suche", "mobile.de", "blocked", error="HTTP 403")
    assert 23.9 * 3600 < store.portal_cooldown_remaining("EV-Suche", "mobile.de") <= 24 * 3600
    store.record_portal_run("EV-Suche", "mobile.de", "blocked", error="HTTP 403")
    assert store.portal_cooldown_remaining("EV-Suche", "mobile.de") <= 24 * 3600

    # Teilergebnis hat eine eigene, kürzere Pause.
    store.record_portal_run("EV-Suche", "mobile.de", "partial", raw_count=20)
    remaining = store.portal_cooldown_remaining("EV-Suche", "mobile.de")
    assert 89 * 60 < remaining <= 90 * 60

    # Ein erfolgreicher Lauf hebt die Pause auf und setzt die Eskalation zurück.
    store.record_portal_run("EV-Suche", "mobile.de", "ok", raw_count=159)
    assert store.portal_cooldown_remaining("EV-Suche", "mobile.de") == 0
    store.record_portal_run("EV-Suche", "mobile.de", "blocked", error="HTTP 403")
    remaining = store.portal_cooldown_remaining("EV-Suche", "mobile.de")
    assert 1.9 * 3600 < remaining <= 2 * 3600


def test_purge_obsolete_settings_removes_dead_credentials(tmp_path):
    """Altlasten aus abgelösten Funktionen verschwinden beim Start."""
    from kfz_crawler.storage import SeenStore

    db = tmp_path / "legacy.sqlite"
    first = SeenStore(str(db))
    first.set_setting("mobile_cookies", "_abck=GEHEIM~-1~xyz")
    first.set_setting("mobile_status", '{"state": "ok"}')
    first.set_setting("ingest_token", "abc123")      # wieder in Gebrauch – bleibt
    first.set_setting("unknown_policy", "lenient")   # aktiv – muss bleiben
    first.close()

    # Ein neuer Store räumt beim Öffnen auf.
    second = SeenStore(str(db))
    assert second.get_setting("mobile_cookies", "") == ""
    assert second.get_setting("mobile_status", "") == ""
    # ingest_token schützt den Cookie-Endpunkt und darf nicht entfernt werden.
    assert second.get_setting("ingest_token", "") == "abc123"
    assert second.get_setting("unknown_policy", "") == "lenient"

    # Wiederholter Aufruf ist folgenlos.
    assert second.purge_obsolete_settings() == []
    second.close()


def test_reevaluation_recovers_fields_without_network(tmp_path):
    """Altbestände werden aus gespeichertem Text nachgezogen – ohne Portalabruf."""
    from kfz_crawler.storage import SeenStore
    from kfz_crawler.models import Listing, DETECTOR_VERSION
    from kfz_crawler.reevaluate import reevaluate_stored_listings

    store = SeenStore(str(tmp_path / "alt.sqlite"))
    listing = Listing(
        portal="mobile.de", url="https://example.test/1", title="VW ID.4 Pro",
        price=25000, year=2022, mileage=50000,
        body=("EZ 04/2022 110 kW (150 PS) Elektro 77 kWh netto "
              "Reichweite (WLTP) 520 km AVILOO SoH 94,6 %"),
    )
    store.record_listing("EV", listing)
    # Alten Stand simulieren: abgeleitete Felder leer, ältere Parser-Version.
    store.conn.execute(
        "UPDATE deals SET detector_version='0.9', power_ps=NULL, battery_kwh=NULL, "
        "ev_range_km=NULL, battery_soh=NULL"
    )
    store.conn.commit()

    stats = reevaluate_stored_listings(store)
    assert stats["aktualisiert"] == 1

    row = store.conn.execute(
        "SELECT power_ps, battery_kwh, battery_observed_kind, ev_range_km, "
        "ev_range_standard, battery_soh, battery_soh_level, year_kind, "
        "first_registration_month, detector_version FROM deals"
    ).fetchone()
    assert row["power_ps"] == 150            # war 0 % gefüllt – jetzt zurück
    assert row["battery_kwh"] == 77.0
    assert row["battery_observed_kind"] == "netto"
    assert row["ev_range_km"] == 520
    assert row["ev_range_standard"] == "wltp"
    assert row["battery_soh"] == 94.6
    assert row["battery_soh_level"] == "bestaetigt"
    assert row["year_kind"] == "ez"
    assert row["first_registration_month"] == 4
    assert row["detector_version"] == DETECTOR_VERSION

    # Ein zweiter Lauf findet nichts mehr zu tun.
    assert reevaluate_stored_listings(store) == {"geprueft": 0}
    store.close()


def test_schema_migration_is_versioned_backed_up_and_idempotent(tmp_path):
    """Migrationen laufen genau einmal, mit Sicherung und festgehaltener Version."""
    from kfz_crawler.storage import SeenStore
    from kfz_crawler.migrations import SCHEMA_VERSION

    db = tmp_path / "mig.sqlite"
    store = SeenStore(str(db))
    assert store.schema_version == SCHEMA_VERSION

    tabellen = {r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"vehicles", "offers", "vehicle_links"} <= tabellen
    store.close()

    # Zweites Öffnen darf nichts erneut migrieren.
    wieder = SeenStore(str(db))
    assert wieder.schema_version == SCHEMA_VERSION
    wieder.close()

    # Die erste Migration einer bestehenden Datei legt eine Sicherung an.
    # (Bei der Neuanlage existiert noch keine Datei zum Sichern.)
    assert list(tmp_path.glob("mig.sqlite.backup-*"))


def test_failed_migration_keeps_previous_version(tmp_path, monkeypatch):
    """Eine fehlgeschlagene Migration rollt zurück und behält den alten Stand."""
    import sqlite3
    from kfz_crawler import migrations

    db = tmp_path / "kaputt.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE probe (x INTEGER)")
    conn.commit()

    def kaputte_migration(c):
        c.execute("CREATE TABLE halb (x INTEGER)")
        raise RuntimeError("Abbruch mitten drin")

    monkeypatch.setattr(migrations, "MIGRATIONS",
                        [(1, "absichtlich fehlerhaft", kaputte_migration)])

    version = migrations.migriere(conn, str(db))
    assert version == 0                       # Version nicht hochgesetzt
    tabellen = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "halb" not in tabellen             # Teilzustand zurückgerollt
    assert "probe" in tabellen                # Bestand unberührt
    conn.close()


def test_migration_survives_open_transaction_from_earlier_step(tmp_path):
    """Eine offen gelassene Transaktion darf die Migration nicht scheitern lassen."""
    import sqlite3
    from kfz_crawler.migrations import migriere, SCHEMA_VERSION

    db = tmp_path / "offen.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    # Ein DELETE ohne Treffer öffnet eine Transaktion, die offen bleibt.
    conn.execute("DELETE FROM settings WHERE key = 'gibtsnicht'")
    assert conn.in_transaction

    assert migriere(conn, str(db)) == SCHEMA_VERSION
    tabellen = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"vehicles", "offers", "vehicle_links"} <= tabellen
    conn.close()


def test_stale_listings_are_hidden_by_default(tmp_path):
    """Auf dem Portal verschwundene Inserate gehören nicht in die Trefferliste."""
    from kfz_crawler.storage import SeenStore
    from kfz_crawler.models import Listing

    store = SeenStore(str(tmp_path / "stale.sqlite"))
    for i in range(3):
        store.record_listing("S", Listing(
            portal="mobile.de", url=f"https://example.test/{i}",
            title=f"Auto {i}", price=20000 + i, year=2022, mileage=50000 + i))
    store.conn.execute("UPDATE deals SET is_stale = 1 WHERE title IN ('Auto 0','Auto 1')")
    store.conn.commit()

    assert len(store.list_deals()) == 1                      # nur das aktive
    assert len(store.list_deals(include_stale=True)) == 3     # auf Wunsch alle
    assert store.count_stale() == 2                           # und zählbar
    store.close()
