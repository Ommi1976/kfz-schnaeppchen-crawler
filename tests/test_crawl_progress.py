from concurrent.futures import ThreadPoolExecutor

from kfz_crawler.crawl_progress import CrawlProgress, progress_status
from kfz_crawler.main import PortalSearchResult
from kfz_crawler.models import SearchQuery
from kfz_crawler.storage import SeenStore


def test_no_full_claim_before_persistence_and_variants_not_summed():
    store = SeenStore(":memory:")
    try:
        progress = CrawlProgress(store, SearchQuery(name="EV"), "mobile.de")
        coverage = {"complete":True, "pages":2, "mode":"full", "reason":"end", "provider_reported_counts":[
            {"variant":1,"reported_total":159}, {"variant":2,"reported_total":200}]}
        progress.coverage(coverage)
        assert progress_status(store)[0]["completeness"] == "partial"
        progress.finish(PortalSearchResult("mobile.de", complete=True, coverage=coverage), persisted_count=20)
        row = progress_status(store)[0]
        assert row["completeness"] == "full" and row["persisted_count"] == 20
        assert len(row["provider_reported_counts"]) == 2
        assert "reported_total" not in row
    finally:
        store.close()


def test_older_run_cannot_overwrite_newer_and_no_cross_portal_loss():
    store = SeenStore(":memory:")
    try:
        query = SearchQuery(name="EV")
        old = CrawlProgress(store, query, "mobile.de")
        new = CrawlProgress(store, query, "mobile.de")
        old.update(pages=999)
        assert progress_status(store)[0]["run_id"] == new.row["run_id"]
        runs = [new] + [CrawlProgress(store, query, name) for name in ("AutoUncle","AutoScout24","Kleinanzeigen")]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda p: p.update(pages=7), runs))
        assert len(progress_status(store)) == 4
        assert all(r["pages"] == 7 for r in progress_status(store))
    finally:
        store.close()


def test_blocked_run_never_claims_a_complete_search():
    store = SeenStore(":memory:")
    try:
        progress = CrawlProgress(store, SearchQuery(name="EV"), "mobile.de")
        progress.finish(PortalSearchResult("mobile.de", status="blocked", error="challenge"), persisted_count=0)
        row = progress_status(store)[0]
        assert row["completeness"] == "blocked"
        assert not row["persisted"] and row["last_full_at"] is None
    finally:
        store.close()
