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
