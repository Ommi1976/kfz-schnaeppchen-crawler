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

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Config
from .ha_run import build_config, load_options
from .main import run_search
from .storage import SeenStore

WEB_DIR = Path(__file__).parent / "web"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("kfz")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_cfg() -> Config:
    """Optionen frisch laden, damit Änderungen ohne Neustart greifen."""
    return build_config(load_options())


def _run_all(app: FastAPI) -> dict:
    """Führt alle aktiven Suchen aus (blockierend -> via to_thread aufrufen)."""
    cfg = _load_cfg()
    app.state.cfg = cfg
    store: SeenStore = app.state.store
    summary = {}
    total = 0
    for query in cfg.searches:
        try:
            deals = run_search(cfg, query, store)
            summary[query.name] = len(deals)
            total += len(deals)
        except Exception as e:  # eine Suche darf die anderen nicht stoppen
            logger.exception("Suche '%s' fehlgeschlagen: %s", query.name, e)
            summary[query.name] = -1
    return {"total": total, "per_search": summary}


async def _do_run(app: FastAPI) -> None:
    async with app.state.run_lock:
        app.state.running = True
        app.state.last_run_at = _now_iso()
        try:
            report = await asyncio.to_thread(_run_all, app)
            app.state.last_report = report
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
    app.state.run_lock = asyncio.Lock()
    app.state.running = False
    app.state.last_run_at = None
    app.state.last_finished_at = None
    app.state.next_run_at = None
    app.state.last_report = {}
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


app = FastAPI(title="KFZ Schnäppchen Crawler", version=__version__, lifespan=lifespan)

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(
        WEB_DIR / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/ready")
async def ready():
    return {"ready": True}


@app.get("/api/status")
async def status():
    cfg: Config = app.state.cfg
    searches = [
        {
            "name": s.name,
            "make": s.make,
            "model": s.model,
            "fuel": s.fuel,
            "price_from": s.price_from,
            "price_to": s.price_to,
            "year_from": s.year_from,
            "year_to": s.year_to,
            "count": app.state.last_report.get("per_search", {}).get(s.name),
        }
        for s in cfg.searches
    ]
    return {
        "version": __version__,
        "running": app.state.running,
        "last_run_at": app.state.last_run_at,
        "last_finished_at": app.state.last_finished_at,
        "next_run_at": app.state.next_run_at,
        "portals_active": [k for k, v in cfg.portals.items() if v],
        "deal_threshold": cfg.settings.deal_threshold,
        "total_deals": app.state.store.deal_count(),
        "searches": searches,
        "last_report": app.state.last_report,
    }


@app.get("/api/deals")
async def deals(search: str | None = None, limit: int = 300):
    rows = app.state.store.list_deals(limit=min(limit, 1000), search_name=search)
    return {"count": len(rows), "deals": rows}


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
