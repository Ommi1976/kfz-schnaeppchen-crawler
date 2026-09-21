"""Private endpoints for interactive portal sessions; no credentials in responses."""
import asyncio
import hashlib
import secrets

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from .portal_accounts import PORTALS, AccountError, account_action, account_status

router = APIRouter(prefix="/api/accounts")


def trusted_ingress(request):
    # Uvicorn must preserve the actual TCP peer (--no-proxy-headers in run.sh).
    # A caller on the LAN cannot gain access by supplying X-Ingress-Path itself.
    return bool(request.client and request.client.host == "172.30.32.2"
                and request.headers.get("X-Ingress-Path"))


def authorized(request):
    if trusted_ingress(request):
        return True
    expected = request.app.state.store.ingest_token()
    return bool(expected and secrets.compare_digest(
        request.headers.get("X-KFZ-Token", ""), expected))


def owner(request):
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        raise HTTPException(403, "Portal-Anmeldung nur über die Add-on-Oberfläche öffnen.")
    if not authorized(request):
        raise HTTPException(401, "Portalkonten benötigen Home-Assistant-Ingress oder ein gültiges X-KFZ-Token.")
    identity = ("ha:" + request.headers.get("X-Remote-User-Id", "ingress")) if trusted_ingress(request) else "token:" + request.headers.get("X-KFZ-Token", "")
    return hashlib.sha256(identity.encode()).hexdigest()


def private_response(value):
    return JSONResponse(value, headers={"Cache-Control": "no-store, private", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"})


@router.get("")
async def list_accounts(request: Request):
    owner(request)
    return private_response({"portals": [account_status(request.app.state.store, key,
        enabled=bool(request.app.state.cfg.portals.get(key))) for key in PORTALS]})


async def perform(request, key, action, payload):
    identity = owner(request)
    try:
        result = await asyncio.to_thread(account_action, key, action,
            request.app.state.store, identity,
            proxy=request.app.state.cfg.settings.proxy or None, payload=payload)
        return private_response(result)
    except AccountError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("/{key}/session")
async def session(request: Request, key: str, session_id: str):
    return await perform(request, key, "session", {"session_id": session_id})


@router.post("/{key}/{action}")
async def action(request: Request, key: str, action: str, payload: dict = Body(default={})):
    if action not in {"connect", "input", "check", "close", "disconnect"}:
        raise HTTPException(404, "Unbekannte Kontoaktion")
    # Input is never logged or persisted, including failures from the browser.
    return await perform(request, key, action, payload)
