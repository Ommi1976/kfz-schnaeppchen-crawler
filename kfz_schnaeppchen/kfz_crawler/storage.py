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
                battery_soh   REAL,
                ev_range_km   INTEGER,
                is_deal       INTEGER DEFAULT 0,
                is_suspicious INTEGER DEFAULT 0,
                reasons       TEXT,
                body          TEXT,
                image_urls    TEXT,
                warranty      TEXT,
                location      TEXT,
                location_zip  TEXT,
                location_city TEXT,
                distance_km   INTEGER,
                first_seen    REAL
            )
            """
        )
        for ddl in [
            "ALTER TABLE deals ADD COLUMN battery_kwh REAL",
            "ALTER TABLE deals ADD COLUMN battery_soh REAL",
            "ALTER TABLE deals ADD COLUMN ev_range_km INTEGER",
            "ALTER TABLE deals ADD COLUMN is_deal INTEGER DEFAULT 0",
            "ALTER TABLE deals ADD COLUMN is_suspicious INTEGER DEFAULT 0",
            "ALTER TABLE deals ADD COLUMN reasons TEXT",
            "ALTER TABLE deals ADD COLUMN body TEXT",
            "ALTER TABLE deals ADD COLUMN image_urls TEXT",
            "ALTER TABLE deals ADD COLUMN warranty TEXT",
            "ALTER TABLE deals ADD COLUMN location TEXT",
            "ALTER TABLE deals ADD COLUMN location_zip TEXT",
            "ALTER TABLE deals ADD COLUMN location_city TEXT",
            "ALTER TABLE deals ADD COLUMN distance_km INTEGER",
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
        # Allgemeiner Key-Value-Speicher (z. B. mobile.de-Cookies + Status).
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        self.conn.commit()

    # ---- Key-Value-Einstellungen --------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            cur = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row and row["value"] is not None else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.conn.commit()

    # ---- Suchen (UI-Verwaltung) ---------------------------------------
    def list_searches(self) -> List[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM searches ORDER BY sort_order ASC, created ASC"
            )
            rows = []
            for r in cur.fetchall():
                d = json.loads(r["spec_json"])
                d["id"] = r["id"]
                d["name"] = r["name"]
                d["active"] = bool(r["active"])
                rows.append(d)
            return rows

    def get_search(self, search_id: str) -> Optional[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM searches WHERE id = ?", (search_id,)
            )
            r = cur.fetchone()
            if not r:
                return None
            d = json.loads(r["spec_json"])
            d["id"] = r["id"]
            d["name"] = r["name"]
            d["active"] = bool(r["active"])
            return d

    def create_search(self, spec: dict) -> dict:
        with self._lock:
            sid = spec.get("id") or str(uuid.uuid4())
            name = spec.get("name") or "Unbenannt"
            active = 1 if spec.get("active", True) else 0
            cur = self.conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM searches")
            order = cur.fetchone()[0]
            clean_spec = dict(spec)
            clean_spec["id"] = sid
            clean_spec["name"] = name
            clean_spec["active"] = bool(active)
            self.conn.execute(
                "INSERT OR REPLACE INTO searches (id, name, active, spec_json, sort_order, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, name, active, json.dumps(clean_spec, ensure_ascii=False), order, time.time()),
            )
            self.conn.commit()
            return clean_spec

    def update_search(self, search_id: str, patch: dict) -> Optional[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT spec_json, active FROM searches WHERE id = ?", (search_id,)
            )
            r = cur.fetchone()
            if not r:
                return None
            data = json.loads(r["spec_json"])
            data.update(patch)
            data["id"] = search_id
            name = data.get("name", "Unbenannt")
            active = 1 if data.get("active", True) else 0
            self.conn.execute(
                "UPDATE searches SET name = ?, active = ?, spec_json = ? WHERE id = ?",
                (name, active, json.dumps(data, ensure_ascii=False), search_id),
            )
            self.conn.commit()
            data["active"] = bool(active)
            return data

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

    def init_default_searches(self, specs: List[dict]) -> None:
        self.seed_searches(specs)

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
            imgs_json = json.dumps(listing.image_urls or [], ensure_ascii=False) if listing.image_urls else None
            self.conn.execute(
                "INSERT INTO deals "
                "(fingerprint, search_name, portal, title, url, price, market_price, "
                " discount, year, mileage, fuel, battery_kwh, battery_soh, ev_range_km, is_deal, is_suspicious, "
                " reasons, body, image_urls, warranty, location, location_zip, location_city, distance_km, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(fingerprint) DO UPDATE SET "
                "search_name=excluded.search_name, portal=excluded.portal, title=excluded.title, "
                "url=excluded.url, price=excluded.price, market_price=excluded.market_price, "
                "discount=excluded.discount, year=excluded.year, mileage=excluded.mileage, "
                "fuel=excluded.fuel, battery_kwh=excluded.battery_kwh, battery_soh=excluded.battery_soh, "
                "ev_range_km=excluded.ev_range_km, is_deal=excluded.is_deal, is_suspicious=excluded.is_suspicious, "
                "reasons=excluded.reasons, body=COALESCE(excluded.body, deals.body), "
                "image_urls=COALESCE(excluded.image_urls, deals.image_urls), "
                "warranty=COALESCE(excluded.warranty, deals.warranty), "
                "location=COALESCE(excluded.location, deals.location), "
                "location_zip=COALESCE(excluded.location_zip, deals.location_zip), "
                "location_city=COALESCE(excluded.location_city, deals.location_city), "
                "distance_km=COALESCE(excluded.distance_km, deals.distance_km)",
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
                    listing.battery_soh,
                    listing.ev_range_km,
                    1 if listing.is_deal else 0,
                    1 if listing.is_suspicious else 0,
                    "; ".join(listing.suspicious_reasons or []),
                    listing.body,
                    imgs_json,
                    listing.warranty,
                    listing.location,
                    listing.location_zip,
                    listing.location_city,
                    listing.distance_km,
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
                   deals_only: bool = False, portal: str | None = None) -> List[dict]:
        with self._lock:
            where = []
            params: list = []
            if search_name:
                where.append("search_name = ?")
                params.append(search_name)
            if deals_only:
                where.append("is_deal = 1")
            if portal:
                where.append("portal = ?")
                params.append(portal)
            wsql = (" WHERE " + " AND ".join(where)) if where else ""
            # Deals zuerst, dann nach Rabatt, dann neueste.
            params.append(limit)
            cur = self.conn.execute(
                f"SELECT * FROM deals{wsql} "
                "ORDER BY is_deal DESC, discount DESC, first_seen DESC LIMIT ?",
                params,
            )
            return [dict(r) for r in cur.fetchall()]

    def count_deals_by_portal(self, search_name: str | None = None,
                              deals_only: bool = False) -> dict:
        with self._lock:
            where = []
            params: list = []
            if search_name:
                where.append("search_name = ?")
                params.append(search_name)
            if deals_only:
                where.append("is_deal = 1")
            wsql = (" WHERE " + " AND ".join(where)) if where else ""
            cur = self.conn.execute(
                f"SELECT portal, COUNT(*) as c FROM deals{wsql} GROUP BY portal",
                params,
            )
            return {r["portal"]: int(r["c"]) for r in cur.fetchall()}

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

    def clear_deals(self, search_name: Optional[str] = None) -> None:
        with self._lock:
            if search_name:
                self.conn.execute("DELETE FROM deals WHERE search_name = ?", (search_name,))
            else:
                self.conn.execute("DELETE FROM deals")
            self.conn.commit()

    def purge_unmatching_deals(self, search_name: str, query) -> int:
        """Entfernt Inserate aus der DB, die den aktuellen Suchkriterien nicht mehr entsprechen."""
        from .models import Listing, matches_query
        deals = self.list_deals(limit=1000, search_name=search_name)
        to_delete = []
        for d in deals:
            l = Listing(
                portal=d.get("portal") or "",
                title=d.get("title") or "",
                url=d.get("url") or "",
                price=d.get("price"),
                year=d.get("year"),
                mileage=d.get("mileage"),
                fuel=d.get("fuel"),
                power_ps=d.get("power_ps"),
                battery_kwh=d.get("battery_kwh"),
                battery_soh=d.get("battery_soh"),
                ev_range_km=d.get("ev_range_km"),
                location=d.get("location"),
                body=d.get("body") or "",
            )
            if not matches_query(l, query):
                to_delete.append(d["fingerprint"])

        if to_delete:
            with self._lock:
                placeholders = ",".join("?" * len(to_delete))
                self.conn.execute(f"DELETE FROM deals WHERE fingerprint IN ({placeholders})", to_delete)
                self.conn.commit()
        return len(to_delete)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
