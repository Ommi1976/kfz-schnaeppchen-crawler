"""FastAPI-App mit Ingress-Weboberfläche und Hintergrund-Scheduler.

Zeigt gefundene Schnäppchen in einem Dashboard und führt die Suchen im
konfigurierten Intervall aus. Wird per uvicorn auf 0.0.0.0:8099 gestartet und
über den Home-Assistant-Ingress erreichbar gemacht.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Config
from .ha_run import build_config, load_options
from .main import run_search
from .models import (SearchQuery, Listing, matches_query, evaluate_query,
                     extract_battery_kwh, extract_ev_range_km)
from .notify import notify_all
from .portals import REGISTRY
from .portals.as24_taxonomy import EQUIPMENT, EQUIPMENT_GROUPS
from .storage import SeenStore

# Auswahllisten für das Suchformular in der UI.
META = {
    "portals": list(REGISTRY.keys()),
    "fuel": ["", "benzin", "diesel", "elektro", "hybrid", "lpg", "cng"],
    "transmission": ["", "schaltgetriebe", "automatik"],
    "body_type": ["", "limousine", "kombi", "suv", "cabrio", "coupe", "van", "kleinwagen"],
    "seller": ["", "haendler", "privat"],
    "doors": ["", "2/3", "4/5"],
    "country": ["DE", "AT", "CH", "FR", "NL", "BE", "IT", "ES", "PL", "LU", "ALL"],
    "emission_class": ["", "euro4", "euro5", "euro6", "euro6d", "euro6e"],
    "drivetrain": ["", "allrad", "front", "heck"],
    "unknown_policy": ["tolerant", "strict"],
    "equipment_groups": [
        {"group": name, "items": [{"id": i, "label": EQUIPMENT[i]} for i in ids]}
        for name, ids in EQUIPMENT_GROUPS
    ],
}

WEB_DIR = Path(__file__).parent / "web"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("kfz")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _interval_minutes() -> int:
    """Intervall aus den Add-on-Optionen (mind. 5 Min), robust gegen Fehler."""
    try:
        return max(5, int(load_options().get("interval_minutes", 30)))
    except Exception:
        return 30


def _schedule_next(app: FastAPI) -> None:
    """Setzt den Zeitpunkt des nächsten geplanten Laufs (Epoch-Sekunden)."""
    app.state.next_run_at = datetime.now(timezone.utc).timestamp() + _interval_minutes() * 60


def _load_cfg() -> Config:
    """Optionen frisch laden, damit Änderungen ohne Neustart greifen."""
    return build_config(load_options())


def _run_one_search(cfg: Config, store: SeenStore, spec: dict) -> tuple[str, int]:
    """Führt eine einzelne Suche aus (thread-safe: Store nutzt intern ein Lock)."""
    query = SearchQuery.from_dict(spec)
    try:
        deals = run_search(cfg, query, store)
        # Neue Schnäppchen an Home Assistant / Telegram melden.
        if deals:
            try:
                notify_all(cfg.notify, query.name, deals)
            except Exception:
                logger.exception("Benachrichtigung fehlgeschlagen")
        return query.name, len(deals)
    except Exception as e:  # eine Suche darf die anderen nicht stoppen
        logger.exception("Suche '%s' fehlgeschlagen: %s", query.name, e)
        return query.name, -1


def _run_all(app: FastAPI, only_id: str | None = None) -> dict:
    """Führt die (aktiven) Suchen aus der DB aus – parallel und CPU-gedeckelt.

    Suchen laufen jetzt nebenläufig (bounded über max_parallel_searches). Der
    Store ist thread-sicher (RLock) und das Browser-Backend serialisiert Firefox
    global, sodass mobile.de nicht mehrfach gleichzeitig rendert.
    """
    cfg = _load_cfg()  # globale Einstellungen/Portale/Benachrichtigung
    store: SeenStore = app.state.store
    app.state.cfg = cfg

    specs = []
    for spec in store.list_searches():
        if only_id and spec.get("id") != only_id:
            continue
        # Inaktive Suchen laufen nicht im Scheduler / bei „Alle suchen" – aber ein
        # expliziter Einzellauf über den „Suchen"-Button (only_id) führt sie aus.
        if not only_id and not spec.get("active", True):
            continue
        specs.append(spec)

    summary: dict = {}
    total = 0
    if not specs:
        return {"total": 0, "per_search": summary}

    workers = max(1, min(int(getattr(cfg.settings, "max_parallel_searches", 2)), len(specs)))
    if workers == 1:
        for spec in specs:
            name, count = _run_one_search(cfg, store, spec)
            summary[name] = count
            total += max(0, count)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_run_one_search, cfg, store, spec) for spec in specs]
            for fut in as_completed(futures):
                name, count = fut.result()
                summary[name] = count
                total += max(0, count)
    return {"total": total, "per_search": summary}


async def _do_run(app: FastAPI, only_id: str | None = None) -> None:
    async with app.state.run_lock:
        app.state.running = True
        app.state.last_run_at = _now_iso()
        try:
            report = await asyncio.to_thread(_run_all, app, only_id)
            app.state.last_report = report

            # Altbestand mit der aktuellen Erkennung nachziehen. Kostet keine
            # Portalanfrage: es werden nur gespeicherte Texte neu ausgewertet.
            try:
                from kfz_crawler.reevaluate import reevaluate_stored_listings
                home_zip = getattr(app.state.cfg.settings, "home_zip", "") or None
                stats = await asyncio.to_thread(
                    reevaluate_stored_listings, app.state.store, 200, home_zip
                )
                if stats.get("aktualisiert"):
                    logger.info("Offline-Neuauswertung: %s", stats)
            except Exception:
                logger.exception("Offline-Neuauswertung fehlgeschlagen")

            # Angebote zu Fahrzeugen zusammenfuehren. Ebenfalls reine
            # Datenbankarbeit; dasselbe Auto auf zwei Portalen wird damit
            # einmal gefuehrt, ohne dass die Zweit-URL verlorengeht.
            try:
                from kfz_crawler.vehicles import synchronisiere_fahrzeuge
                fz = await asyncio.to_thread(synchronisiere_fahrzeuge, app.state.store)
                if fz.get("neue_fahrzeuge") or fz.get("zugeordnet"):
                    logger.info("Fahrzeugabgleich: %s", fz)
            except Exception:
                logger.exception("Fahrzeugabgleich fehlgeschlagen")
            # Asynchrone Hintergrund-Bildanalyse für SoH anstoßen (blockiert die UI/Suche nicht)
            try:
                from kfz_crawler.battery_analyzer import run_background_image_enrichment
                task = getattr(app.state, "enrichment_task", None)
                if task is None or task.done():
                    app.state.enrichment_task = asyncio.create_task(
                        asyncio.to_thread(run_background_image_enrichment, app.state.store)
                    )
            except Exception:
                pass
        finally:
            app.state.running = False
            app.state.last_finished_at = _now_iso()
            # Nach JEDEM Lauf (auch manuell) den nächsten Zeitpunkt fortschreiben,
            # damit die UI „nächster Lauf" nie leer bleibt.
            _schedule_next(app)


async def _scheduler(app: FastAPI) -> None:
    # Kleiner Vorlauf, damit der Server zuerst sauber hochkommt.
    await asyncio.sleep(5)
    while True:
        try:
            await _do_run(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduler-Durchlauf fehlgeschlagen")
        interval = _interval_minutes()
        _schedule_next(app)
        await asyncio.sleep(interval * 60)


async def _reevaluate_beim_start(app: FastAPI) -> None:
    """Zieht den Altbestand einmalig auf die aktuelle Erkennung nach."""
    try:
        from kfz_crawler.reevaluate import reevaluate_stored_listings
        home_zip = getattr(app.state.cfg.settings, "home_zip", "") or None
        gesamt: dict = {}
        # In Schueben, damit ein grosser Bestand den Start nicht blockiert.
        for _ in range(20):
            stats = await asyncio.to_thread(
                reevaluate_stored_listings, app.state.store, 200, home_zip
            )
            if not stats.get("geprueft"):
                break
            for k, v in stats.items():
                gesamt[k] = gesamt.get(k, 0) + v
        if gesamt:
            logger.info("Neuauswertung beim Start: %s", gesamt)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Neuauswertung beim Start fehlgeschlagen")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = _load_cfg()
    app.state.cfg = cfg
    app.state.store = SeenStore(cfg.settings.db_path)
    # Beim ersten Start die Suchen aus den Add-on-Optionen übernehmen.
    app.state.store.seed_searches([s.to_dict() for s in cfg.searches])
    app.state.run_lock = asyncio.Lock()
    app.state.running = False
    app.state.last_run_at = None
    app.state.last_finished_at = None
    # Direkt einen Erst-Zeitpunkt setzen (Vorlauf 5 s + Intervall), damit die UI
    # sofort „nächster Lauf" anzeigt und nicht erst nach dem ersten Durchlauf.
    _schedule_next(app)
    app.state.last_report = {}
    app.state.enrichment_task = None
    # Einmal beim Start ausgeben: die Browser-Erweiterung braucht es.
    try:
        logger.info("Cookie-Token für die Browser-Erweiterung: %s",
                    app.state.store.ingest_token())
    except Exception:
        logger.exception("Cookie-Token konnte nicht bereitgestellt werden")
    # Nach einem Versionssprung ist der Altbestand veraltet: Felder, die die
    # neue Erkennung fuellen wuerde, bleiben leer, und falsch uebernommene
    # Werte (etwa der Kraftstoff bei Kleinanzeigen) filtern weiter falsch.
    # Bisher lief die Neuauswertung erst nach dem naechsten Crawl - zu spaet.
    # Sie kostet keine Portalanfrage, nur gespeicherten Text.
    app.state.startup_reevaluation = asyncio.create_task(
        _reevaluate_beim_start(app)
    )
    app.state.scheduler = asyncio.create_task(_scheduler(app))
    try:
        yield
    finally:
        app.state.startup_reevaluation.cancel()
        app.state.scheduler.cancel()
        try:
            await app.state.scheduler
        except asyncio.CancelledError:
            pass
        app.state.store.close()


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


app = FastAPI(title="KFZ Schnäppchen Crawler", version=__version__, lifespan=lifespan)

if WEB_DIR.exists():
    app.mount("/static", NoCacheStaticFiles(directory=WEB_DIR), name="static")


# Schreibende Zugriffe von ausserhalb des Ingress brauchen das Token.
#
# Ueber den LAN-Port ist die gesamte API erreichbar – auch /api/searches und
# DELETE /api/deals. Der Home-Assistant-Ingress setzt X-Ingress-Path; fehlt der
# Header, kommt die Anfrage direkt aus dem Netz und muss sich ausweisen.
_GESCHUETZTE_METHODEN = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def token_bei_direktzugriff(request: Request, call_next):
    if request.method in _GESCHUETZTE_METHODEN and not request.headers.get("X-Ingress-Path"):
        try:
            erwartet = app.state.store.ingest_token()
        except Exception:
            erwartet = ""
        geliefert = request.headers.get("X-KFZ-Token", "")
        if not erwartet or not secrets.compare_digest(geliefert, erwartet):
            return JSONResponse(
                {"detail": "Direktzugriff erfordert ein gültiges X-KFZ-Token"},
                status_code=401,
            )
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(
        WEB_DIR / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/ready")
async def ready():
    return {"ready": True}


@app.get("/api/meta")
async def meta():
    return META


@app.get("/api/status")
async def status():
    cfg: Config = app.state.cfg
    per = app.state.last_report.get("per_search", {})
    searches = []
    for spec in app.state.store.list_searches():
        spec = dict(spec)
        spec["count"] = per.get(spec.get("name"))
        searches.append(spec)
    return {
        "version": __version__,
        "running": app.state.running,
        "last_run_at": app.state.last_run_at,
        "last_finished_at": app.state.last_finished_at,
        "next_run_at": app.state.next_run_at,
        "portals_active": [k for k, v in cfg.portals.items() if v],
        "deal_threshold": cfg.settings.deal_threshold,
        "total_deals": app.state.store.deal_count(deals_only=True),
        "total_listings": app.state.store.total_count(),
        "portal_counts": app.state.store.count_deals_by_portal(),
        "portal_health": app.state.store.list_portal_health(),
        "searches": searches,
        "last_report": app.state.last_report,
        # Ohne frisches Sitzungscookie liefert mobile.de nichts. Das war
        # bisher nur im Protokoll sichtbar.
        "mobile_cookies": _mobile_cookie_status(),
    }


def _mobile_cookie_status() -> dict:
    try:
        from .cookie_storage import get_mobile_cookies_status
        return get_mobile_cookies_status()
    except Exception:
        logger.exception("Cookie-Status nicht lesbar")
        return {"has_cookies": False, "is_fresh": False}


# ---- Suchen verwalten -------------------------------------------------
def _validate_spec(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Bitte einen Namen angeben.")
    # Über SearchQuery normalisieren (coerct Typen, säubert Freitext) und zurück
    # als sauberes Dict speichern.
    spec = SearchQuery.from_dict(payload).to_dict()
    spec["name"] = name
    spec["active"] = bool(payload.get("active", True))
    return spec


@app.get("/api/searches")
async def list_searches():
    return app.state.store.list_searches()


@app.post("/api/searches", status_code=201)
async def create_search(payload: dict = Body(...)):
    spec = _validate_spec(payload)
    created = app.state.store.create_search(spec)
    query = SearchQuery.from_dict(spec)
    app.state.store.purge_unmatching_deals(query.name, query)
    return created


@app.put("/api/searches/{search_id}")
async def update_search(search_id: str, payload: dict = Body(...)):
    spec = _validate_spec(payload)
    updated = app.state.store.update_search(search_id, spec)
    if updated is None:
        raise HTTPException(status_code=404, detail="Suche nicht gefunden.")
    query = SearchQuery.from_dict(spec)
    app.state.store.purge_unmatching_deals(query.name, query)
    return updated


@app.delete("/api/searches/{search_id}", status_code=204)
async def delete_search(search_id: str):
    s = app.state.store.get_search(search_id)
    if s:
        app.state.store.clear_deals(s.get("name"))
    if not app.state.store.delete_search(search_id):
        raise HTTPException(status_code=404, detail="Suche nicht gefunden.")


@app.post("/api/searches/{search_id}/run")
async def run_one(search_id: str):
    if app.state.store.get_search(search_id) is None:
        raise HTTPException(status_code=404, detail="Suche nicht gefunden.")
    if app.state.running:
        return JSONResponse({"status": "already_running"}, status_code=409)
    asyncio.create_task(_do_run(app, only_id=search_id))
    return {"status": "started"}


@app.get("/api/deals")
async def deals(search: str | None = None, limit: int = 400, deals_only: bool = False,
                portal: str | None = None, include_stale: bool = False):
    rows = app.state.store.list_deals(
        limit=min(limit, 2000), search_name=search, deals_only=deals_only, portal=portal,
        include_stale=include_stale
    )
    specs = {s["name"]: SearchQuery.from_dict(s) for s in app.state.store.list_searches()}
    active_search_names = set(specs.keys())

    # Bezugsgroesse fuer die Preis-Plausibilitaet: je Suche der Median der
    # eigenen Treffer. Ein fester Betrag waere falsch - eine Suche nach
    # Kleinwagen hat einen ganz anderen Median als eine nach Oberklasse.
    # Der Median ist gegenueber den wenigen Ausreissern unempfindlich, die er
    # gerade aussortieren soll.
    preise_je_suche: dict[str, list[int]] = {}
    for row in rows:
        preis = row.get("price")
        if preis and preis > 0:
            preise_je_suche.setdefault(row.get("search_name") or "", []).append(preis)
    median_je_suche = {
        name: statistics.median(werte)
        for name, werte in preise_je_suche.items()
        if len(werte) >= 8      # unter acht Inseraten ist der Median kein Massstab
    }

    filtered = []
    for row in rows:
        s_name = row.get("search_name")
        query = specs.get(s_name)
        if not query:
            # Falls kein spezifischer Suchname passt, erste aktive Suche als Referenz prüfen oder verwerfen
            if len(specs) == 1:
                query = next(iter(specs.values()))
            else:
                continue

        l = Listing(
            portal=row.get("portal") or "",
            title=row.get("title") or "",
            url=row.get("url") or "",
            price=row.get("price"),
            year=row.get("year"),
            mileage=row.get("mileage"),
            fuel=row.get("fuel"),
            power_ps=row.get("power_ps"),
            battery_kwh=row.get("battery_kwh"),
            battery_net_kwh=row.get("battery_net_kwh"),
            battery_gross_kwh=row.get("battery_gross_kwh"),
            battery_soh=row.get("battery_soh"),
            ev_range_km=row.get("ev_range_km"),
            location=row.get("location"),
            body=row.get("body") or "",
            country=row.get("country"),
            is_stale=bool(row.get("is_stale")),
        )
        if not evaluate_query(
            l, query, referenz_median=median_je_suche.get(s_name)
        ).passed:
            continue
        for json_field, target, fallback in (
            ("evidence_json", "field_evidence", {}),
            ("unknown_fields", "unknown_fields_list", []),
        ):
            try:
                raw_json = row.get(json_field) or ("{}" if isinstance(fallback, dict) else "[]")
                row[target] = json.loads(raw_json)
            except (TypeError, ValueError):
                row[target] = fallback
        filtered.append(row)
    rows = filtered

    # Portal-Aufteilung für die aktuelle Suche & Deals-Filterung berechnen
    portal_counts = {}
    for r in rows:
        p = r.get("portal") or "Unbekannt"
        portal_counts[p] = portal_counts.get(p, 0) + 1

    # Wie viele Inserate wurden ausgeblendet, weil sie auf dem Portal nicht
    # mehr auffindbar sind. Die Oberfläche kann das anbieten, statt tote
    # Einträge stillschweigend wegzulassen.
    ausgeblendet = 0
    if not include_stale:
        ausgeblendet = app.state.store.count_stale(search_name=search, portal=portal)

    return {
        "count": len(rows),
        "deals": rows,
        "portal_counts": portal_counts,
        "total_deals": sum(1 for r in rows if r.get("is_deal")),
        "stale_count": sum(1 for r in rows if r.get("is_stale")),
        "stale_hidden": ausgeblendet,
        "portal_health": app.state.store.list_portal_health(search),
    }


@app.post("/api/run")
async def run_now():
    if app.state.running:
        return JSONResponse({"status": "already_running"}, status_code=409)
    asyncio.create_task(_do_run(app))
    return {"status": "started"}


@app.delete("/api/deals")
async def clear():
    deleted = app.state.store.clear_deals()
    return {"deleted": deleted}


@app.get("/api/mobile-cookies/status")
async def mobile_cookies_status():
    from .cookie_storage import get_mobile_cookies_status
    return get_mobile_cookies_status()


@app.post("/api/mobile-cookies")
async def save_mobile_cookies_endpoint(request: Request, payload: dict = Body(...)):
    """Nimmt mobile.de-Sitzungscookies entgegen – nur mit gueltigem Token.

    Der Endpunkt ist ueber den LAN-Port erreichbar, damit die Browser-
    Erweiterung ihn ansprechen kann. Ohne Pruefung koennte jedes Geraet im
    Heimnetz fremde Sitzungsdaten hinterlegen.
    """
    from .cookie_storage import save_mobile_cookies
    erwartet = app.state.store.ingest_token()
    geliefert = request.headers.get("X-KFZ-Token", "")
    if not secrets.compare_digest(geliefert, erwartet):
        raise HTTPException(status_code=401, detail="Ungültiges oder fehlendes Token")

    raw = payload.get("cookies") or payload.get("cookie") or payload
    saved = save_mobile_cookies(raw)
    if "_abck" not in saved:
        raise HTTPException(status_code=400, detail="Kein _abck-Cookie enthalten")
    logger.info("mobile.de-Cookies aktualisiert: %d Werte", len(saved))

    # Mit einer frischen Sitzung ist die Ausgangslage eine andere. Die
    # Schutzpause aus vorherigen Blocks würde den nächsten Versuch sonst um
    # Stunden verzögern, obwohl sich die Bedingungen gerade geändert haben.
    freigegeben = 0
    try:
        store = app.state.store
        with store._lock:
            cur = store.conn.execute(
                "UPDATE portal_health SET block_count = 0, last_run = 0 "
                "WHERE portal LIKE '%mobile%' AND status != 'ok'"
            )
            store.conn.commit()
            freigegeben = cur.rowcount
        if freigegeben:
            logger.info("mobile.de-Schutzpause aufgehoben – neue Sitzung liegt vor")
    except Exception:
        logger.exception("Schutzpause konnte nicht aufgehoben werden")

    return {"status": "ok", "saved_count": len(saved), "cooldown_geloest": freigegeben}


@app.get("/api/discovered-ev")
async def get_discovered_ev(status: str | None = None):
    models = app.state.store.list_discovered_ev_models(status=status)
    return {"count": len(models), "models": models}


@app.post("/api/discovered-ev/{model_key:path}/status")
async def update_discovered_ev_status(model_key: str, payload: dict = Body(...)):
    new_status = payload.get("status")
    if new_status not in ("discovered", "review", "conflict", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="Ungültiger Status")
    ok = app.state.store.set_discovered_ev_status(model_key, new_status)
    return {"success": ok, "model_key": model_key, "status": new_status}



@app.delete("/api/mobile-cookies")
async def delete_mobile_cookies():
    from .cookie_storage import COOKIE_FILE
    if COOKIE_FILE.exists():
        try:
            COOKIE_FILE.unlink()
        except Exception:
            pass
    return {"status": "deleted"}
