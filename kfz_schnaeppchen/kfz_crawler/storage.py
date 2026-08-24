"""Persistenter Speicher für bereits gesehene Inserate (Duplikat-Filter)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .models import Listing


class SeenStore:
    def __init__(self, db_path: str | Path):
        self.conn = sqlite3.connect(str(db_path))
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
        self.conn.commit()

    def is_new(self, listing: Listing) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen WHERE fingerprint = ?", (listing.fingerprint,)
        )
        return cur.fetchone() is None

    def mark_seen(self, listing: Listing) -> None:
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

    def close(self) -> None:
        self.conn.close()
