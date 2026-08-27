"""FastAPI-App mit Ingress-Weboberfläche und Hintergrund-Scheduler.

Zeigt gefundene Schnäppchen in einem Dashboard und führt die Suchen im
konfigurierten Intervall aus. Wird per uvicorn auf 0.0.0.0:8099 gestartet und
über den Home-Assistant-Ingress erreichbar gemacht.
"""

from __future__ import annotations

import asyncio
import logging
import time
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
from .models import SearchQuery, extract_battery_kwh, extract_ev_range_km
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
    "emission_class": ["", "euro4", "euro5", "euro6", "euro6d", "euro6e"],
    "drivetrain": ["", "allrad", "front", "heck"],
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


def _load_cfg() -> Config:
    """Optionen frisch laden, damit Änderungen ohne Neustart greifen."""
    return build_config(load_options())


def _run_all(app: FastAPI, only_id: str | None = None) -> dict:
    """Führt die (aktiven) Suchen aus der DB aus (blockierend -> via to_thread)."""
    cfg = _load_cfg()  # globale Einstellungen/Portale/Benachrichtigung
    store: SeenStore = app.state.store
    # mobile.de-Cookies aus dem Store (Browser-Add-on-Import) übernehmen.
    cfg.settings.mobile_cookies = store.get_setting("mobile_cookies", "")
    app.state.cfg = cfg
    summary = {}
    total = 0
    for spec in store.list_searches():
        if only_id and spec.get("id") != only_id:
            continue
        # Inaktive Suchen laufen nicht im Scheduler / bei „Alle suchen" – aber ein
        # expliziter Einzellauf über den „Suchen"-Button (only_id) führt sie aus.
        if not only_id and not spec.get("active", True):
            continue
        query = SearchQuery.from_dict(spec)
        try:
            deals = run_search(cfg, query, store)
            summary[query.name] = len(deals)
            total += len(deals)
            # Neue Schnäppchen an Home Assistant / Telegram melden.
            if deals:
                try:
                    notify_all(cfg.notify, query.name, deals)
                except Exception:
                    logger.exception("Benachrichtigung fehlgeschlagen")
        except Exception as e:  # eine Suche darf die anderen nicht stoppen
            logger.exception("Suche '%s' fehlgeschlagen: %s", query.name, e)
            summary[query.name] = -1
    return {"total": total, "per_search": summary}


async def _do_run(app: FastAPI, only_id: str | None = None) -> None:
    async with app.state.run_lock:
        app.state.running = True
        app.state.last_run_at = _now_iso()
        try:
            report = await asyncio.to_thread(_run_all, app, only_id)
            app.state.last_report = report
            # Asynchrone Hintergrund-Bildanalyse für SoH anstoßen (blockiert die UI/Suche nicht)
            try:
                from kfz_crawler.battery_analyzer import run_background_image_enrichment
                asyncio.create_task(asyncio.to_thread(run_background_image_enrichment, app.state.store))
            except Exception:
                pass
        finally:
            app.state.running = False
            app.state.last_finished_at = _now_iso()


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
        interval = max(1, int(getattr(app.state.cfg.settings, "interval_minutes", 30))) \
            if hasattr(app.state, "cfg") else 30
        # interval_minutes steckt in den Optionen, nicht in Settings -> neu lesen.
        try:
            interval = max(5, int(load_options().get("interval_minutes", 30)))
        except Exception:
            interval = 30
        app.state.next_run_at = datetime.now(timezone.utc).timestamp() + interval * 60
        await asyncio.sleep(interval * 60)


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
    app.state.next_run_at = None
    app.state.last_report = {}
    # Token für den direkten LAN-Zugriff (Browser-Extension). Einmalig erzeugen.
    if not app.state.store.get_setting("ingest_token", ""):
        import secrets
        app.state.store.set_setting("ingest_token", secrets.token_hex(16))
    app.state.scheduler = asyncio.create_task(_scheduler(app))
    try:
        yield
    finally:
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
        "searches": searches,
        "last_report": app.state.last_report,
        "mobile": _mobile_status(app),
    }


def _mobile_status(app: FastAPI) -> dict:
    """Status der mobile.de-Anbindung (autark via Firefox Playwright)."""
    cfg: Config = app.state.cfg
    active = bool(cfg.portals.get("mobile_de"))
    return {
        "active": active,
        "has_cookies": True,
        "state": "ok" if active else "none",
        "message": "Autarker Firefox-Headless-Betrieb aktiv",
        "checked_at": time.time(),
        "ingest_token": app.state.store.get_setting("ingest_token", ""),
    }


@app.get("/api/mobile-status")
async def mobile_status():
    return _mobile_status(app)


def _test_mobile_cookies(cookies: str) -> dict:
    """Cookies sofort gegen mobile.de testen (kleiner Suchlauf)."""
    from .models import SearchQuery as _SQ
    from .portals.mobile_de import MobileDe
    from .portals.base import CookiesExpired, PortalError
    portal = MobileDe(request_delay=0.5, max_pages=1, cookies=cookies)
    try:
        found = portal.search(_SQ(name="test", make="volkswagen", model="golf"))
        return {"ok": True, "count": len(found),
                "message": f"{len(found)} Treffer – Cookies gültig."}
    except CookiesExpired as e:
        return {"ok": False, "message": str(e)}
    except PortalError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"Fehler: {e}"}


@app.post("/api/mobile-cookies")
async def save_mobile_cookies(request: Request, payload: dict = Body(...)):
    import json as _json
    import time as _time
    store = app.state.store
    # Token-Schutz: sowohl die (Ingress-)Oberfläche als auch die Browser-Extension
    # senden den X-KFZ-Token mit. So ist der Endpunkt auch bei freigegebenem
    # LAN-Port abgesichert, ohne die übrige Oberfläche zu beeinträchtigen.
    token = store.get_setting("ingest_token", "")
    if not token or request.headers.get("X-KFZ-Token") != token:
        raise HTTPException(status_code=401, detail="Ungültiger oder fehlender Token.")
    cookies = str(payload.get("cookies", "") or "").strip()
    if not cookies or "_abck" not in cookies:
        raise HTTPException(status_code=400,
                            detail="Ungültig: erwarte den mobile.de-Cookie-String (mit _abck).")
    result = await asyncio.to_thread(_test_mobile_cookies, cookies)
    if result["ok"]:
        store.set_setting("mobile_cookies", cookies)
    store.set_setting("mobile_status", _json.dumps({
        "state": "ok" if result["ok"] else "expired",
        "message": result["message"], "checked_at": _time.time()}))
    return result


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
    return app.state.store.create_search(_validate_spec(payload))


@app.put("/api/searches/{search_id}")
async def update_search(search_id: str, payload: dict = Body(...)):
    updated = app.state.store.update_search(search_id, _validate_spec(payload))
    if updated is None:
        raise HTTPException(status_code=404, detail="Suche nicht gefunden.")
    return updated


@app.delete("/api/searches/{search_id}", status_code=204)
async def delete_search(search_id: str):
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
async def deals(search: str | None = None, limit: int = 400, deals_only: bool = False, portal: str | None = None):
    rows = app.state.store.list_deals(
        limit=min(limit, 2000), search_name=search, deals_only=deals_only, portal=portal
    )
    specs = {s["name"]: SearchQuery.from_dict(s) for s in app.state.store.list_searches()}
    filtered = []
    for row in rows:
        query = specs.get(row.get("search_name"))
        if query:
            kwh = row.get("battery_kwh")
            rng = row.get("ev_range_km")
            if query.battery_from_kwh and query.ev_range_from:
                if kwh is not None and rng is not None and kwh < query.battery_from_kwh and rng < query.ev_range_from:
                    continue
            elif query.battery_from_kwh and kwh is not None and kwh < query.battery_from_kwh:
                continue
            elif query.ev_range_from and rng is not None and rng < query.ev_range_from:
                continue
        filtered.append(row)
    rows = filtered

    # Portal-Aufteilung für die aktuelle Suche & Deals-Filterung berechnen
    portal_counts = {}
    all_filtered_rows = app.state.store.list_deals(
        limit=min(limit, 2000), search_name=search, deals_only=deals_only
    )
    for r in all_filtered_rows:
        p = r.get("portal") or "Unbekannt"
        portal_counts[p] = portal_counts.get(p, 0) + 1

    return {
        "count": len(rows),
        "deals": rows,
        "portal_counts": portal_counts,
        "total_deals": sum(1 for r in rows if r.get("is_deal")),
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
