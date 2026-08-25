"""Persistenter Speicher: Duplikat-Filter (seen) + gefundene Deals (für die UI)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import List

from .models import Listing


class SeenStore:
    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                fingerprint TEXT PRIMARY KEY,
                portal      TEXT,
                url         TEXT,
                title       TEXT,
                price       INTEGER,
                first_seen  REAL
            )
            """
        )
        # Volltext-Deals für die Weboberfläche.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deals (
                fingerprint  TEXT PRIMARY KEY,
                search_name  TEXT,
                portal       TEXT,
                title        TEXT,
                url          TEXT,
                price        INTEGER,
                market_price INTEGER,
                discount     REAL,
                year         INTEGER,
                mileage      INTEGER,
                fuel         TEXT,
                first_seen   REAL
            )
            """
        )
        self.conn.commit()

    def is_new(self, listing: Listing) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "SELECT 1 FROM seen WHERE fingerprint = ?", (listing.fingerprint,)
            )
            return cur.fetchone() is None

    def mark_seen(self, listing: Listing) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO seen "
                "(fingerprint, portal, url, title, price, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    listing.fingerprint,
                    listing.portal,
                    listing.url,
                    listing.title,
                    listing.price,
                    time.time(),
                ),
            )
            self.conn.commit()

    # ---- Deals (für die Weboberfläche) --------------------------------
    def record_deal(self, search_name: str, listing: Listing) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO deals "
                "(fingerprint, search_name, portal, title, url, price, market_price, "
                " discount, year, mileage, fuel, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    listing.fingerprint,
                    search_name,
                    listing.portal,
                    listing.title,
                    listing.url,
                    listing.price,
                    listing.market_price,
                    listing.discount,
                    listing.year,
                    listing.mileage,
                    listing.fuel,
                    time.time(),
                ),
            )
            self.conn.commit()

    def list_deals(self, limit: int = 300, search_name: str | None = None) -> List[dict]:
        with self._lock:
            if search_name:
                cur = self.conn.execute(
                    "SELECT * FROM deals WHERE search_name = ? "
                    "ORDER BY first_seen DESC LIMIT ?",
                    (search_name, limit),
                )
            else:
                cur = self.conn.execute(
                    "SELECT * FROM deals ORDER BY first_seen DESC LIMIT ?", (limit,)
                )
            return [dict(r) for r in cur.fetchall()]

    def deal_count(self) -> int:
        with self._lock:
            cur = self.conn.execute("SELECT COUNT(*) AS c FROM deals")
            return int(cur.fetchone()["c"])

    def clear_deals(self) -> int:
        with self._lock:
            cur = self.conn.execute("DELETE FROM deals")
            self.conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self.conn.close()
