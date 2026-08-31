"""Persistenter Speicher: Duplikat-Filter (seen) + gefundene Deals (für die UI)."""

from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set

from .models import Listing

logger = logging.getLogger(__name__)


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
            "ALTER TABLE deals ADD COLUMN country TEXT",
            "ALTER TABLE deals ADD COLUMN last_seen REAL",
            "ALTER TABLE deals ADD COLUMN power_ps INTEGER",
            "ALTER TABLE deals ADD COLUMN transmission TEXT",
            "ALTER TABLE deals ADD COLUMN body_type TEXT",
            "ALTER TABLE deals ADD COLUMN battery_net_kwh REAL",
            "ALTER TABLE deals ADD COLUMN battery_observed_kind TEXT",
            "ALTER TABLE deals ADD COLUMN battery_gross_kwh REAL",
            "ALTER TABLE deals ADD COLUMN evidence_json TEXT",
            "ALTER TABLE deals ADD COLUMN quality_score REAL",
            "ALTER TABLE deals ADD COLUMN unknown_fields TEXT",
            "ALTER TABLE deals ADD COLUMN is_stale INTEGER DEFAULT 0",
            "ALTER TABLE deals ADD COLUMN stale_since REAL",
            "ALTER TABLE deals ADD COLUMN detector_version TEXT",
        ]:
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # Spalte existiert bereits

        # WAL-Modus & Performance-Pragmas für unterbrechungsfreies paralleles Lesen/Schreiben
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA cache_size = -64000")  # 64 MB Cache
            self.conn.execute("PRAGMA temp_store = MEMORY")
        except Exception:
            pass

        for _key in self.purge_obsolete_settings():
            logger.info("Altlast aus settings entfernt: %s", _key)

        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_ym ON deals(year, mileage)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_filter ON deals(search_name, is_deal, price, year, mileage)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_portal ON deals(portal, search_name)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_deals_first_seen ON deals(first_seen DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_fp ON seen(fingerprint)")
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
        # Selbstlernender EV-Modellspeicher (automatisch entdeckte neue E-Autos).
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discovered_ev_models (
                model_key       TEXT PRIMARY KEY,
                make            TEXT,
                model           TEXT,
                sample_title    TEXT,
                count           INTEGER DEFAULT 1,
                avg_battery_kwh REAL,
                avg_range_km    INTEGER,
                power_kw        INTEGER,
                power_ps        INTEGER,
                status          TEXT DEFAULT 'discovered',
                first_seen      REAL,
                last_seen       REAL
            )
            """
        )
        # Allgemeiner Key-Value-Speicher (z. B. mobile.de-Cookies + Status).
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portal_health (
                search_name   TEXT,
                portal        TEXT,
                status        TEXT,
                raw_count     INTEGER DEFAULT 0,
                kept_count    INTEGER DEFAULT 0,
                excluded_json TEXT,
                error         TEXT,
                last_run      REAL,
                last_success  REAL,
                block_count   INTEGER DEFAULT 0,
                PRIMARY KEY (search_name, portal)
            )
            """
        )
        for ddl in [
            "ALTER TABLE portal_health ADD COLUMN block_count INTEGER DEFAULT 0",
            "ALTER TABLE discovered_ev_models ADD COLUMN sample_fingerprints TEXT",
            "ALTER TABLE discovered_ev_models ADD COLUMN portals TEXT",
            "ALTER TABLE discovered_ev_models ADD COLUMN min_battery_kwh REAL",
            "ALTER TABLE discovered_ev_models ADD COLUMN max_battery_kwh REAL",
        ]:
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
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

    def mark_seen_many(self, listings: List[Listing]) -> None:
        if not listings:
            return
        now = time.time()
        with self._lock:
            self.conn.executemany(
                "INSERT OR IGNORE INTO seen (fingerprint, portal, url, title, price, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (item.fingerprint, item.portal, item.url, item.title, item.price, now)
                    for item in listings
                ],
            )
            self.conn.commit()

    # ---- Inserate (für die Weboberfläche) -----------------------------
    def _record_listing_no_commit(self, search_name: str, listing: Listing) -> None:
            imgs_json = json.dumps(listing.image_urls or [], ensure_ascii=False) if listing.image_urls else None
            evidence_json = json.dumps(listing.field_evidence or {}, ensure_ascii=False)
            unknown_json = json.dumps(listing.unknown_fields or [], ensure_ascii=False)
            now = time.time()
            self.conn.execute(
                "INSERT INTO deals "
                "(fingerprint, search_name, portal, title, url, price, market_price, "
                " discount, year, mileage, fuel, power_ps, transmission, body_type, "
                " battery_kwh, battery_observed_kind, battery_net_kwh, battery_gross_kwh, battery_soh, ev_range_km, is_deal, is_suspicious, "
                " reasons, body, image_urls, warranty, location, location_zip, location_city, distance_km, country, "
                " evidence_json, quality_score, unknown_fields, is_stale, stale_since, detector_version, first_seen, last_seen) "
                "VALUES (" + ", ".join(["?"] * 39) + ")"
                " ON CONFLICT(fingerprint) DO UPDATE SET "
                "search_name=excluded.search_name, portal=excluded.portal, title=excluded.title, "
                "url=excluded.url, price=excluded.price, market_price=excluded.market_price, "
                "discount=excluded.discount, year=excluded.year, mileage=excluded.mileage, "
                "fuel=excluded.fuel, power_ps=COALESCE(excluded.power_ps, deals.power_ps), "
                "transmission=COALESCE(excluded.transmission, deals.transmission), "
                "body_type=COALESCE(excluded.body_type, deals.body_type), "
                "battery_kwh=excluded.battery_kwh, battery_observed_kind=excluded.battery_observed_kind, "
                "battery_net_kwh=excluded.battery_net_kwh, "
                "battery_gross_kwh=excluded.battery_gross_kwh, battery_soh=COALESCE(excluded.battery_soh, deals.battery_soh), "
                "ev_range_km=excluded.ev_range_km, is_deal=excluded.is_deal, is_suspicious=excluded.is_suspicious, "
                "reasons=excluded.reasons, body=COALESCE(excluded.body, deals.body), "
                "image_urls=COALESCE(excluded.image_urls, deals.image_urls), "
                "warranty=COALESCE(excluded.warranty, deals.warranty), "
                "location=COALESCE(excluded.location, deals.location), "
                "location_zip=COALESCE(excluded.location_zip, deals.location_zip), "
                "location_city=COALESCE(excluded.location_city, deals.location_city), "
                "distance_km=COALESCE(excluded.distance_km, deals.distance_km), "
                "country=COALESCE(excluded.country, deals.country), "
                "evidence_json=excluded.evidence_json, quality_score=excluded.quality_score, "
                "unknown_fields=excluded.unknown_fields, is_stale=0, stale_since=NULL, "
                "detector_version=excluded.detector_version, "
                "last_seen=excluded.last_seen",
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
                    listing.power_ps,
                    listing.transmission,
                    listing.body_type,
                    listing.battery_kwh,
                    getattr(listing, "battery_observed_kind", "unbekannt"),
                    listing.battery_net_kwh,
                    listing.battery_gross_kwh,
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
                    listing.country,
                    evidence_json,
                    listing.quality_score,
                    unknown_json,
                    1 if listing.is_stale else 0,
                    listing.stale_since,
                    listing.detector_version,
                    now,
                    now,
                ),
            )

    def record_listing(self, search_name: str, listing: Listing) -> None:
        with self._lock:
            self._record_listing_no_commit(search_name, listing)
            self.conn.commit()

    def record_listings(self, search_name: str, listings: List[Listing]) -> None:
        """Speichert einen kompletten Lauf in einer Transaktion."""
        if not listings:
            return
        with self._lock:
            for listing in listings:
                self._record_listing_no_commit(search_name, listing)
            self.conn.commit()

    def update_enrichments(self, updates: List[dict]) -> None:
        """Schreibt Detail-/OCR-Anreicherungen gesammelt und mit Herkunft."""
        if not updates:
            return
        with self._lock:
            for update in updates:
                fingerprint = update["fingerprint"]
                row = self.conn.execute(
                    "SELECT evidence_json FROM deals WHERE fingerprint = ?", (fingerprint,)
                ).fetchone()
                try:
                    evidence = json.loads(row["evidence_json"] or "{}") if row else {}
                except (TypeError, ValueError):
                    evidence = {}
                if update.get("battery_soh") is not None:
                    evidence["battery_soh"] = {
                        "source": update.get("soh_source", "detail_text"),
                        "confidence": update.get("soh_confidence", 0.9),
                        "evidence": update.get("soh_evidence", "Hintergrundanalyse"),
                        "detector_version": update.get("detector_version", "1.1.0"),
                    }
                self.conn.execute(
                    "UPDATE deals SET battery_soh=COALESCE(?, battery_soh), "
                    "ev_range_km=COALESCE(?, ev_range_km), battery_kwh=COALESCE(?, battery_kwh), "
                    "warranty=COALESCE(?, warranty), image_urls=COALESCE(?, image_urls), "
                    "evidence_json=? WHERE fingerprint=?",
                    (
                        update.get("battery_soh"), update.get("ev_range_km"),
                        update.get("battery_kwh"), update.get("warranty"),
                        update.get("image_urls"), json.dumps(evidence, ensure_ascii=False),
                        fingerprint,
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
        from .models import Listing, matches_query, infer_listing_details
        deals = self.list_deals(limit=2000, search_name=search_name)
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
                country=d.get("country"),
            )
            infer_listing_details(l)
            if not matches_query(l, query):
                to_delete.append(d["fingerprint"])
            else:
                # Aktualisierte Werte sichern
                self.conn.execute(
                    "UPDATE deals SET battery_kwh = ?, ev_range_km = ? WHERE fingerprint = ?",
                    (l.battery_kwh, l.ev_range_km, d["fingerprint"])
                )

        if to_delete:
            with self._lock:
                placeholders = ",".join("?" * len(to_delete))
                self.conn.execute(f"DELETE FROM deals WHERE fingerprint IN ({placeholders})", to_delete)
                self.conn.commit()
        return len(to_delete)

    def sync_active_deals(
        self,
        search_name: str,
        portal_active_fps: Dict[str, Set[str]],
    ) -> int:
        """Markiert nicht mehr bestätigte Inserate als veraltet.

        Sicherheitsgarantie: Nur Portale, die im aktuellen Lauf erfolgreich Treffer
        geliefert haben, werden bereinigt (schützt vor Datenverlust bei temporären Portal-Ausfällen).
        """
        stale_count = 0
        with self._lock:
            for portal, active_fps in portal_active_fps.items():
                if not active_fps:
                    # Ein leerer Satz kann auch "Abruf fehlgeschlagen" bedeuten.
                    # Bestätigte Leerseiten werden vom Aufrufer explizit als stale
                    # markiert; hier schützen wir bestehende Daten.
                    continue
                if active_fps:
                    placeholders = ",".join("?" * len(active_fps))
                    self.conn.execute(
                        f"UPDATE deals SET is_stale = 0, stale_since = NULL WHERE search_name = ? AND portal = ? AND fingerprint IN ({placeholders})",
                        [search_name, portal, *active_fps],
                    )
                cur = self.conn.execute(
                    "SELECT fingerprint FROM deals WHERE search_name = ? AND portal = ?",
                    (search_name, portal),
                )
                db_fps = {row["fingerprint"] for row in cur.fetchall()}
                stale_fps = list(db_fps - active_fps)
                if stale_fps:
                    placeholders = ",".join("?" * len(stale_fps))
                    changed = self.conn.execute(
                        f"UPDATE deals SET is_stale = 1, stale_since = COALESCE(stale_since, ?) "
                        f"WHERE search_name = ? AND portal = ? AND fingerprint IN ({placeholders}) AND is_stale = 0",
                        [time.time(), search_name, portal, *stale_fps],
                    )
                    stale_count += changed.rowcount
            if stale_count > 0:
                self.conn.commit()
        return stale_count

    def mark_portal_stale(self, search_name: str, portal: str) -> int:
        """Kennzeichnet Bestandsdaten nach einem fehlgeschlagenen Portalabruf."""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE deals SET is_stale = 1, stale_since = COALESCE(stale_since, ?) "
                "WHERE search_name = ? AND portal = ? AND is_stale = 0",
                (time.time(), search_name, portal),
            )
            self.conn.commit()
            return cur.rowcount

    def record_portal_run(
        self,
        search_name: str,
        portal: str,
        status: str,
        raw_count: int = 0,
        kept_count: int = 0,
        exclusions: Optional[dict] = None,
        error: str = "",
    ) -> None:
        now = time.time()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO portal_health
                (search_name, portal, status, raw_count, kept_count, excluded_json, error,
                 last_run, last_success, block_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(search_name, portal) DO UPDATE SET
                    status=excluded.status, raw_count=excluded.raw_count,
                    kept_count=excluded.kept_count, excluded_json=excluded.excluded_json,
                    error=excluded.error, last_run=excluded.last_run,
                    last_success=CASE WHEN excluded.status = 'ok' THEN excluded.last_run ELSE portal_health.last_success END,
                    block_count=CASE
                        WHEN excluded.status = 'ok' THEN 0
                        WHEN excluded.status IN ('blocked', 'partial')
                            THEN COALESCE(portal_health.block_count, 0) + 1
                        ELSE COALESCE(portal_health.block_count, 0)
                    END
                """,
                (
                    search_name, portal, status, raw_count, kept_count,
                    json.dumps(exclusions or {}, ensure_ascii=False), error[:500], now,
                    now if status == "ok" else None,
                    # Erster Eintrag: ein Block zählt sofort als Stufe 1.
                    1 if status in ("blocked", "partial") else 0,
                ),
            )
            self.conn.commit()

    # Schlüssel abgelöster Funktionen. Kein Codepfad liest sie noch; der
    # Cookie-Import läuft heute über cookie_storage (Datei in /data).
    # mobile_cookies enthielt ein echtes Akamai-Sessioncookie – ungenutzte
    # Zugangsdaten gehören nicht dauerhaft in die Datenbank.
    OBSOLETE_SETTINGS = ("mobile_cookies", "mobile_status", "ingest_token")

    def purge_obsolete_settings(self) -> List[str]:
        """Entfernt Altlasten aus settings und meldet, was entfernt wurde."""
        removed: List[str] = []
        with self._lock:
            try:
                for key in self.OBSOLETE_SETTINGS:
                    cur = self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                    if cur.rowcount:
                        removed.append(key)
                if removed:
                    self.conn.commit()
            except sqlite3.OperationalError:
                return []
        return removed

    # Gestaffelte Schutzpausen: der erste Block ist oft ein Ausreißer, ein
    # wiederholter zeigt an, dass die Quelle den Zugriff ernsthaft ablehnt.
    BLOCK_COOLDOWNS = (2 * 3600, 6 * 3600, 24 * 3600)
    PARTIAL_COOLDOWN = 90 * 60

    def portal_cooldown_remaining(
        self,
        search_name: str,
        portal: str,
        blocked_seconds: Optional[float] = None,
        partial_seconds: Optional[float] = None,
    ) -> float:
        """Restzeit bis zu einem sicheren erneuten Portalabruf.

        Ein Block wird nicht in jedem geplanten Suchlauf erneut provoziert.
        Die Pause eskaliert mit der Zahl aufeinanderfolgender Blocks
        (2 h, 6 h, dann 24 h); ein erfolgreicher Lauf setzt sie zurück.
        Erfolgreiche Läufe und unbekannte Zustände haben keine Sperrzeit.
        """
        with self._lock:
            row = self.conn.execute(
                "SELECT status, last_run, block_count FROM portal_health "
                "WHERE search_name = ? AND portal = ?",
                (search_name, portal),
            ).fetchone()
        if not row or not row["last_run"]:
            return 0.0

        status = row["status"]
        if status == "blocked":
            if blocked_seconds is not None:
                cooldown = blocked_seconds
            else:
                # block_count zählt ab 1 für den ersten Block.
                stufe = max(1, int(row["block_count"] or 1))
                idx = min(stufe, len(self.BLOCK_COOLDOWNS)) - 1
                cooldown = self.BLOCK_COOLDOWNS[idx]
        elif status == "partial":
            cooldown = partial_seconds if partial_seconds is not None else self.PARTIAL_COOLDOWN
        else:
            cooldown = 0.0
        return max(0.0, float(row["last_run"]) + cooldown - time.time())

    def list_portal_health(self, search_name: Optional[str] = None) -> List[dict]:
        with self._lock:
            if search_name:
                cur = self.conn.execute(
                    "SELECT * FROM portal_health WHERE search_name = ? ORDER BY portal",
                    (search_name,),
                )
            else:
                cur = self.conn.execute("SELECT * FROM portal_health ORDER BY search_name, portal")
            rows = []
            for row in cur.fetchall():
                item = dict(row)
                try:
                    item["exclusions"] = json.loads(item.pop("excluded_json") or "{}")
                except (TypeError, ValueError):
                    item["exclusions"] = {}
                rows.append(item)
            return rows

    # ---- Selbstlernender EV-Modellspeicher ----------------------------
    def record_discovered_ev_model(
        self,
        title: str,
        battery_kwh: Optional[float] = None,
        ev_range_km: Optional[int] = None,
        power_kw: Optional[int] = None,
        power_ps: Optional[int] = None,
        fingerprint: str = "",
        portal: str = "",
    ) -> tuple[bool, Optional[dict]]:
        """Registriert oder aktualisiert ein unbekanntes E-Auto-Modell.
        
        Gibt (is_new, record) zurück.
        """
        import re
        if not title:
            return False, None
            
        # Bereinige Titel für einen robusten Key (z. B. 'volkswagen id.3', 'mg mg4 luxury')
        clean = re.sub(r"[^\w\s]", " ", title.lower())
        words = clean.split()
        if not words:
            return False, None
            
        # Schlüssel aus den ersten 3-4 signifikanten Wörtern bilden
        key_words = [w for w in words if len(w) > 1 and not w.isdigit()][:4]
        if not key_words:
            key_words = words[:3]
        model_key = " ".join(key_words)
        
        # Plausibilitätsfilter für sichere Qualifizierung
        if battery_kwh is not None and (battery_kwh < 15.0 or battery_kwh > 200.0):
            battery_kwh = None
        if ev_range_km is not None and (ev_range_km < 100 or ev_range_km > 1200):
            ev_range_km = None

        sample_id = fingerprint or hashlib.sha1(
            f"{title}|{battery_kwh}|{ev_range_km}|{portal}".encode("utf-8")
        ).hexdigest()
        now = time.time()
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM discovered_ev_models WHERE model_key = ?",
                (model_key,)
            )
            row = cur.fetchone()
            if row:
                sample_ids = set(json.loads(row["sample_fingerprints"] or "[]"))
                portals = set(json.loads(row["portals"] or "[]"))
                if sample_id in sample_ids:
                    return False, dict(row)
                sample_ids.add(sample_id)
                if portal:
                    portals.add(portal)
                old_count = len(sample_ids) - 1
                new_count = len(sample_ids)
                
                # Inkrementelle Durchschnittsberechnung
                old_kwh = row["avg_battery_kwh"]
                new_kwh = old_kwh
                status = row["status"]
                if battery_kwh is not None:
                    if old_kwh is not None and abs(old_kwh - battery_kwh) > 4.0:
                        status = "conflict"
                    else:
                        new_kwh = round(((old_kwh or battery_kwh) * old_count + battery_kwh) / new_count, 1)
                    
                old_rng = row["avg_range_km"]
                new_rng = old_rng
                if ev_range_km is not None:
                    new_rng = round(((old_rng or ev_range_km) * old_count + ev_range_km) / new_count)
                
                # Automatisches Lernen erzeugt nur einen Prüfkandidaten. Eine
                # Freigabe darf niemals allein aus wiederholten Inseratstexten
                # entstehen und bleibt einer kuratierten Prüfung vorbehalten.
                if status == "discovered" and new_count >= 3 and len(portals) >= 2 and new_kwh is not None:
                    status = "review"
                    logger.info("EV-Variante zur Prüfung vorgemerkt: %s", model_key)
                    
                self.conn.execute(
                    """
                    UPDATE discovered_ev_models
                    SET count = ?, avg_battery_kwh = ?, avg_range_km = ?, status = ?, last_seen = ?,
                        sample_fingerprints = ?, portals = ?,
                        min_battery_kwh = CASE WHEN min_battery_kwh IS NULL THEN ? ELSE MIN(min_battery_kwh, ?) END,
                        max_battery_kwh = CASE WHEN max_battery_kwh IS NULL THEN ? ELSE MAX(max_battery_kwh, ?) END
                    WHERE model_key = ?
                    """,
                    (
                        new_count, new_kwh, new_rng, status, now,
                        json.dumps(sorted(sample_ids)), json.dumps(sorted(portals)),
                        battery_kwh, battery_kwh, battery_kwh, battery_kwh, model_key,
                    )
                )
                self.conn.commit()
                return False, {
                    "model_key": model_key,
                    "title": title,
                    "count": new_count,
                    "avg_battery_kwh": new_kwh,
                    "avg_range_km": new_rng,
                    "status": status,
                }
            else:
                self.conn.execute(
                    """
                    INSERT INTO discovered_ev_models
                    (model_key, make, model, sample_title, count, avg_battery_kwh, avg_range_km,
                     power_kw, power_ps, status, first_seen, last_seen, sample_fingerprints, portals,
                     min_battery_kwh, max_battery_kwh)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, 'discovered', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        model_key,
                        words[0].capitalize() if words else "",
                        " ".join(words[1:3]) if len(words) > 1 else "",
                        title[:100],
                        battery_kwh,
                        ev_range_km,
                        power_kw,
                        power_ps,
                        now,
                        now,
                        json.dumps([sample_id]),
                        json.dumps([portal] if portal else []),
                        battery_kwh,
                        battery_kwh,
                    )
                )
                self.conn.commit()
                return True, {
                    "model_key": model_key,
                    "title": title,
                    "count": 1,
                    "avg_battery_kwh": battery_kwh,
                    "avg_range_km": ev_range_km,
                    "status": "discovered",
                }

    def get_approved_ev_models(self) -> List[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT * FROM discovered_ev_models WHERE status = 'approved' ORDER BY count DESC"
            )
            return [dict(r) for r in cur.fetchall()]

    def list_discovered_ev_models(self, status: Optional[str] = None) -> List[dict]:
        with self._lock:
            if status:
                cur = self.conn.execute(
                    "SELECT * FROM discovered_ev_models WHERE status = ? ORDER BY count DESC, last_seen DESC",
                    (status,)
                )
            else:
                cur = self.conn.execute(
                    "SELECT * FROM discovered_ev_models ORDER BY count DESC, last_seen DESC"
                )
            return [dict(r) for r in cur.fetchall()]

    def set_discovered_ev_status(self, model_key: str, status: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE discovered_ev_models SET status = ? WHERE model_key = ?",
                (status, model_key)
            )
            self.conn.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
