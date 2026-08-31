"""Versionierte, gesicherte Schema-Migrationen (K4 §18).

Bisher wurden Schemaaenderungen als "ALTER TABLE versuchen, Fehler ignorieren"
ausgefuehrt. Das genuegt fuer einzelne Spalten, aber nicht fuer Aenderungen mit
Datenbewegung: ein Abbruch mitten drin hinterlaesst einen halben Zustand, und
es ist nicht nachvollziehbar, welcher Stand vorliegt.

Hier bekommt die Datenbank eine ``schema_version``. Jede Migration laeuft in
einer Transaktion, und vor dem ersten Schritt wird eine Sicherung angelegt.
Schlaegt eine Migration fehl, bleibt der alte Stand erhalten.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Aktueller Sollstand. Muss mit der letzten Migration uebereinstimmen.
SCHEMA_VERSION = 1

# Wie viele Sicherungen aufbewahrt werden. Aeltere werden entfernt, damit die
# Datenpartition nicht volllaeuft.
BACKUP_KEEP = 3


def _lies_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _schreibe_version(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA erlaubt keine Parameterbindung.
    conn.execute(f"PRAGMA user_version = {int(version)}")


def sichere_datenbank(db_path: str | Path) -> Optional[Path]:
    """Legt vor einer Migration eine Kopie an und raeumt alte Sicherungen ab.

    Gibt den Pfad der Sicherung zurueck, oder None bei einer In-Memory-Datenbank
    beziehungsweise wenn die Sicherung nicht moeglich war.
    """
    pfad = Path(str(db_path))
    if str(db_path) in (":memory:", "") or not pfad.exists():
        return None

    ziel = pfad.with_name(f"{pfad.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        # sqlite3.backup kopiert konsistent, auch wenn Verbindungen offen sind.
        quelle = sqlite3.connect(str(pfad))
        kopie = sqlite3.connect(str(ziel))
        with kopie:
            quelle.backup(kopie)
        kopie.close()
        quelle.close()
    except Exception:
        logger.exception("Sicherung vor Migration fehlgeschlagen: %s", pfad)
        return None

    alte = sorted(pfad.parent.glob(f"{pfad.name}.backup-*"))
    for veraltet in alte[:-BACKUP_KEEP]:
        try:
            veraltet.unlink()
        except OSError:
            pass
    return ziel


def _migration_1(conn: sqlite3.Connection) -> None:
    """Fahrzeuge und Portalangebote trennen (K4 §4).

    Ein reales Fahrzeug kann gleichzeitig auf mehreren Portalen angeboten
    werden – gemessen ueberschneiden sich die Kataloge zu rund 18 %. Ohne
    Trennung erscheint dasselbe Auto mehrfach, und die Portal-URLs der
    Zweitangebote gehen verloren.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicles (
            vehicle_id      TEXT PRIMARY KEY,
            make            TEXT,
            model           TEXT,
            variant         TEXT,
            year            INTEGER,
            first_registration_month INTEGER,
            mileage         INTEGER,
            power_ps        INTEGER,
            fuel            TEXT,
            battery_net_kwh  REAL,
            battery_gross_kwh REAL,
            ev_range_km     INTEGER,
            ev_range_standard TEXT,
            battery_soh     REAL,
            battery_soh_level TEXT,
            location        TEXT,
            location_zip    TEXT,
            distance_km     INTEGER,
            identity_confidence REAL DEFAULT 0,
            quality_status  TEXT,
            created         REAL,
            updated         REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS offers (
            offer_id        TEXT PRIMARY KEY,
            vehicle_id      TEXT NOT NULL,
            portal          TEXT NOT NULL,
            portal_id       TEXT,
            title           TEXT,
            price           INTEGER,
            dealer          TEXT,
            location        TEXT,
            url             TEXT,
            autouncle_url   TEXT,
            source_portal   TEXT,
            image_urls      TEXT,
            body            TEXT,
            status          TEXT DEFAULT 'aktiv',
            first_seen      REAL,
            last_seen       REAL
        )
        """
    )
    # Zuordnungen muessen rueckgaengig gemacht werden koennen (K4 §7): hier
    # wird protokolliert, welches Angebot warum welchem Fahrzeug zugeschlagen
    # wurde. Ohne das ist eine Fehlzusammenfuehrung nicht mehr aufloesbar.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicle_links (
            offer_id        TEXT PRIMARY KEY,
            vehicle_id      TEXT NOT NULL,
            confidence      REAL,
            evidence        TEXT,
            manual          INTEGER DEFAULT 0,
            created         REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_offers_vehicle ON offers(vehicle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_offers_portal ON offers(portal, portal_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vehicles_match ON vehicles(year, mileage, power_ps)")


# (Zielversion, Beschreibung, Funktion)
MIGRATIONS: List[Tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "Fahrzeuge und Portalangebote trennen", _migration_1),
]


def migriere(conn: sqlite3.Connection, db_path: str | Path) -> int:
    """Bringt die Datenbank auf SCHEMA_VERSION. Gibt die neue Version zurueck.

    Jede Migration laeuft in einer eigenen Transaktion. Schlaegt eine fehl,
    wird zurueckgerollt und die Version bleibt auf dem letzten guten Stand –
    die Anwendung startet dann mit dem alten Schema weiter.
    """
    version = _lies_version(conn)
    offen = [m for m in MIGRATIONS if m[0] > version]
    if not offen:
        return version

    sicherung = sichere_datenbank(db_path)
    if sicherung:
        logger.info("Sicherung vor Migration angelegt: %s", sicherung.name)

    for ziel, beschreibung, funktion in offen:
        try:
            # BEGIN ausdruecklich: Pythons sqlite3 oeffnet fuer DDL (CREATE,
            # ALTER) von sich aus keine Transaktion, sodass ein "with conn"
            # halbfertige Tabellen stehen liesse. SQLite selbst beherrscht
            # transaktionales DDL – es muss nur angefordert werden.
            conn.execute("BEGIN")
            funktion(conn)
            _schreibe_version(conn, ziel)
            conn.commit()
            version = ziel
            logger.info("Migration auf Version %s: %s", ziel, beschreibung)
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            logger.exception("Migration auf Version %s fehlgeschlagen: %s", ziel, beschreibung)
            break
    return version
