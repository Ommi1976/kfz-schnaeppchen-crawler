"""Persistent search coverage; deliberately independent of account/session state.

``progress_status(store)`` is the JSON-ready contract for /api/status.crawl_progress.
There is one latest attempt per (search_name, portal). Timestamps are Unix seconds;
unknown counters are None. pages/observed_unique/kept/filtered_count describe THIS
attempt; sweep_observed_unique also includes previous persisted mobile chunks.
Provider counts are separate observations per query variant, never an additive
total or a denominator for local matches. ``full`` means verified search traversal
AND successful persistence, not that every provider-reported ad was retained.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid

STATE_KEY = "crawl.progress.v1"
_LOCK = threading.RLock()  # Covers the entire settings read/modify/write operation.


def _read(store) -> dict:
    if store is None or not hasattr(store, "get_setting"):
        return {}
    try:
        value = json.loads(store.get_setting(STATE_KEY, "{}"))
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def progress_status(store) -> list[dict]:
    """Read detached latest-attempt snapshots, including unfinished old attempts."""
    with _LOCK:
        rows = [row for row in _read(store).values()
                if isinstance(row, dict) and "search_name" in row and "portal" in row]
    return sorted(rows, key=lambda row: (row["search_name"], row["portal"]))


class CrawlProgress:
    """Run-scoped writer. Older concurrent attempts cannot overwrite newer ones."""

    def __init__(self, store, query, portal: str, portal_key: str = ""):
        self.store = store
        self.key = json.dumps([query.name, portal], ensure_ascii=False)
        signature = hashlib.sha256(json.dumps(query.to_dict(), sort_keys=True).encode()).hexdigest()
        now = time.time()
        self.row = dict(
            search_name=query.name, portal=portal, portal_key=portal_key,
            query_signature=signature, run_id=uuid.uuid4().hex,
            phase="starting", completeness="partial", mode="unknown",
            pages=None, observed_unique=None, sweep_observed_unique=None,
            provider_reported_counts=[], kept=None, filtered_count=None,
            persisted_count=None, persisted=False, reason="coverage_unverified", error="",
            started_at=now, refreshed_at=now, finished_at=None,
            last_persisted_at=None, last_full_at=None,
        )
        with _LOCK:
            rows = _read(store)
            previous = rows.get(self.key, {})
            if isinstance(previous, dict) and previous.get("query_signature") == signature:
                for name in ("last_persisted_at", "last_full_at"):
                    self.row[name] = previous.get(name)
            self._write(rows)

    def _write(self, rows):
        if self.store is not None and hasattr(self.store, "set_setting"):
            # JSON also detaches caller-owned coverage lists from stored snapshots.
            rows[self.key] = self.row
            self.store.set_setting(STATE_KEY, json.dumps(rows, ensure_ascii=False))

    def update(self, **changes):
        with _LOCK:
            rows = _read(self.store)
            current = rows.get(self.key, {})
            if isinstance(current, dict) and current.get("run_id", self.row["run_id"]) != self.row["run_id"]:
                return
            self.row.update(changes, refreshed_at=time.time())
            self._write(rows)

    def coverage(self, coverage: dict, *, phase="crawling"):
        """Publish crawl-time measurements without declaring persisted completion."""
        changes = {"phase": phase}
        for source, target in (
            ("mode", "mode"), ("pages", "pages"), ("run_unique_seen", "observed_unique"),
            ("unique_seen", "sweep_observed_unique"), ("reason", "reason"),
            ("provider_reported_counts", "provider_reported_counts"),
            ("variant", "variant"), ("next_page", "next_page"),
        ):
            if source in coverage:
                changes[target] = coverage[source]
        self.update(**changes)

    def finish(self, result, *, persisted_count: int):
        """Called only after result persistence, reconciliation and cursor commit."""
        self.coverage(result.coverage, phase="persisting")
        reason = result.coverage.get("reason", self.row["reason"])
        blocked = result.status == "blocked" or reason == "blocked"
        deferred = result.status == "cooldown" or reason == "deferred"
        failed = result.status == "error" or reason == "error"
        completeness = "partial"
        if blocked:
            completeness = "blocked"
        elif result.status == "ok" and result.complete:
            completeness = "full"
        elif result.status == "incremental" and reason == "delta":
            completeness = "delta"
        phase = "blocked" if blocked else "deferred" if deferred else "error" if failed else "finished"
        now = time.time()
        persisted = result.status not in {"blocked", "error", "cooldown"}
        changes = dict(phase=phase, completeness=completeness, reason=reason,
                       error=result.error, persisted=persisted,
                       persisted_count=persisted_count if persisted else None,
                       finished_at=now)
        if persisted:
            changes["last_persisted_at"] = now
        if completeness == "full":
            changes["last_full_at"] = now
        self.update(**changes)

    def fail(self, error: Exception):
        self.update(phase="error", completeness="partial", persisted=False,
                    persisted_count=None, reason="run_error", error=str(error),
                    finished_at=time.time())
