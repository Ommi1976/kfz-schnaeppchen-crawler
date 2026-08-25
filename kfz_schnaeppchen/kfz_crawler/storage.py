"""Persistenter Speicher: Duplikat-Filter (seen) + gefundene Deals (für die UI)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

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
        # Alle gefundenen Inserate für die Weboberfläche (nicht nur Deals).
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deals (
                fingerprint   TEXT PRIMARY KEY,
                search_name   TEXT,
                portal        TEXT,
                title         TEXT,
                url           TEXT,
                price         INTEGER,
                market_price  INTEGER,
                discount      REAL,
                year          INTEGER,
                mileage       INTEGER,
                fuel          TEXT,
                battery_kwh   REAL,
                ev_range_km   INTEGER,
                is_deal       INTEGER DEFAULT 0,
                is_suspicious INTEGER DEFAULT 0,
                reasons       TEXT,
                first_seen    REAL
            )
            """
        )
        for ddl in [
            "ALTER TABLE deals ADD COLUMN battery_kwh REAL",
            "ALTER TABLE deals ADD COLUMN ev_range_km INTEGER",
            "ALTER TABLE deals ADD COLUMN is_deal INTEGER DEFAULT 0",
            "ALTER TABLE deals ADD COLUMN is_suspicious INTEGER DEFAULT 0",
            "ALTER TABLE deals ADD COLUMN reasons TEXT",
        ]:
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # Spalte existiert bereits
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_ym ON deals(year, mileage)")
        # In der UI verwaltete Suchen.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id         TEXT PRIMARY KEY,
                name       TEXT,
                active     INTEGER DEFAULT 1,
                spec_json  TEXT,
                sort_order INTEGER,
                created    REAL
            )
            """
        )
        self.conn.commit()

    # ---- Suchen (UI-Verwaltung) ---------------------------------------
    def list_searches(self) -> List[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT id, name, active, spec_json FROM searches "
                "ORDER BY sort_order ASC, created ASC"
            )
            out = []
            for r in cur.fetchall():
                spec = json.loads(r["spec_json"] or "{}")
                spec["id"] = r["id"]
                spec["name"] = r["name"]
                spec["active"] = bool(r["active"])
                out.append(spec)
            return out

    def get_search(self, search_id: str) -> Optional[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT id, name, active, spec_json FROM searches WHERE id = ?", (search_id,)
            )
            r = cur.fetchone()
            if not r:
                return None
            spec = json.loads(r["spec_json"] or "{}")
            spec["id"] = r["id"]
            spec["name"] = r["name"]
            spec["active"] = bool(r["active"])
            return spec

    def create_search(self, spec: dict) -> dict:
        sid = str(uuid.uuid4())[:8]
        spec = dict(spec)
        spec["id"] = sid
        with self._lock:
            cur = self.conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM searches")
            order = int(cur.fetchone()["m"]) + 1
            self.conn.execute(
                "INSERT INTO searches (id, name, active, spec_json, sort_order, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, spec.get("name", "Suche"), int(bool(spec.get("active", True))),
                 json.dumps(spec, ensure_ascii=False), order, time.time()),
            )
            self.conn.commit()
        return self.get_search(sid)

    def update_search(self, search_id: str, spec: dict) -> Optional[dict]:
        with self._lock:
            if self.conn.execute("SELECT 1 FROM searches WHERE id = ?", (search_id,)).fetchone() is None:
                return None
            spec = dict(spec)
            spec["id"] = search_id
            self.conn.execute(
                "UPDATE searches SET name = ?, active = ?, spec_json = ? WHERE id = ?",
                (spec.get("name", "Suche"), int(bool(spec.get("active", True))),
                 json.dumps(spec, ensure_ascii=False), search_id),
            )
            self.conn.commit()
        return self.get_search(search_id)

    def delete_search(self, search_id: str) -> bool:
        with self._lock:
            cur = self.conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def count_searches(self) -> int:
        with self._lock:
            return int(self.conn.execute("SELECT COUNT(*) AS c FROM searches").fetchone()["c"])

    def seed_searches(self, specs: List[dict]) -> None:
        """Einmalig aus den Add-on-Optionen befüllen, falls noch keine Suchen da sind."""
        if self.count_searches() > 0:
            return
        for spec in specs:
            self.create_search(spec)

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

    # ---- Inserate (für die Weboberfläche) -----------------------------
    def record_listing(self, search_name: str, listing: Listing) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO deals "
                "(fingerprint, search_name, portal, title, url, price, market_price, "
                " discount, year, mileage, fuel, battery_kwh, ev_range_km, is_deal, is_suspicious, reasons, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    listing.battery_kwh,
                    listing.ev_range_km,
                    1 if listing.is_deal else 0,
                    1 if listing.is_suspicious else 0,
                    "; ".join(listing.suspicious_reasons or []),
                    time.time(),
                ),
            )
            self.conn.commit()

    # Rückwärtskompatibler Alias.
    record_deal = record_listing

    def similar_exists(self, year, mileage, price=None, tol=0.3) -> bool:
        """Dublette über Läufe hinweg: gleiches Baujahr + exakter km bereits
        gespeichert (optional mit Preisnähe)."""
        if not year or not mileage or mileage <= 0:
            return False
        with self._lock:
            cur = self.conn.execute(
                "SELECT price FROM deals WHERE year = ? AND mileage = ?", (year, mileage)
            )
            rows = cur.fetchall()
        if not rows:
            return False
        if price is None:
            return True
        for r in rows:
            op = r["price"]
            if not op:
                return True
            if min(price, op) >= (1 - tol) * max(price, op):
                return True
        return False

    def list_deals(self, limit: int = 300, search_name: str | None = None,
                   deals_only: bool = False) -> List[dict]:
        with self._lock:
            where = []
            params: list = []
            if search_name:
                where.append("search_name = ?")
                params.append(search_name)
            if deals_only:
                where.append("is_deal = 1")
            wsql = (" WHERE " + " AND ".join(where)) if where else ""
            # Deals zuerst, dann nach Rabatt, dann neueste.
            params.append(limit)
            cur = self.conn.execute(
                f"SELECT * FROM deals{wsql} "
                "ORDER BY is_deal DESC, discount DESC, first_seen DESC LIMIT ?",
                params,
            )
            return [dict(r) for r in cur.fetchall()]

    def deal_count(self, deals_only: bool = True) -> int:
        with self._lock:
            sql = "SELECT COUNT(*) AS c FROM deals"
            if deals_only:
                sql += " WHERE is_deal = 1"
            return int(self.conn.execute(sql).fetchone()["c"])

    def total_count(self) -> int:
        with self._lock:
            return int(self.conn.execute("SELECT COUNT(*) AS c FROM deals").fetchone()["c"])

    def prune(self, keep: int = 3000) -> None:
        """Älteste Einträge kappen, damit die Tabelle nicht unbegrenzt wächst."""
        with self._lock:
            self.conn.execute(
                "DELETE FROM deals WHERE fingerprint IN ("
                "  SELECT fingerprint FROM deals ORDER BY first_seen DESC LIMIT -1 OFFSET ?"
                ")",
                (keep,),
            )
            self.conn.commit()

    def clear_deals(self) -> int:
        with self._lock:
            cur = self.conn.execute("DELETE FROM deals")
            self.conn.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self.conn.close()
