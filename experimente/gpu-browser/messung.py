"""Lesende Messung: Zeigt ein Chrome im Container mit Intel-GPU echte Merkmale?

Stufe 1 (immer): nur lokale Seiten, kein Kontakt zu mobile.de.
Stufe 2 (--mobile URL): genau EIN Seitenaufruf bei mobile.de, und nur, wenn
Stufe 1 bestanden ist. Kein Klick, kein Blättern, keine Wiederholung.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from patchright.sync_api import sync_playwright

OUT = Path("/out")
# Wie kfz_crawler/browser.py, ergänzt um Akamais Krypto-Challenge.
BLOCK_MARKERS = ("zugriff verweigert", "access denied", "temporarily blocked",
                 "unusual traffic", "captcha")
CARD_LINKS = "a[href*='details.html?id='], a[href*='/auto-inserat/']"
SOFTWARE_RENDERER = ("swiftshader", "llvmpipe", "softpipe", "software")
ERLAUBTE_HOSTS = {"suchen.mobile.de", "www.mobile.de"}

PROBE_JS = """() => {
  const r = {};
  r.webdriver = navigator.webdriver;
  r.userAgent = navigator.userAgent;
  r.brands = navigator.userAgentData
    ? navigator.userAgentData.brands.map(b => b.brand + " " + b.version) : null;
  r.platform = navigator.platform;
  r.languages = navigator.languages;
  r.plugins = navigator.plugins.length;
  r.hardwareConcurrency = navigator.hardwareConcurrency;
  r.deviceMemory = navigator.deviceMemory;
  r.screen = [screen.width, screen.height, screen.colorDepth];
  r.window = [outerWidth, outerHeight, innerWidth, innerHeight];
  r.windowChrome = typeof window.chrome;
  r.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const gl = document.createElement("canvas").getContext("webgl");
  if (gl) {
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    r.webglVendor = gl.getParameter(ext ? ext.UNMASKED_VENDOR_WEBGL : gl.VENDOR);
    r.webglRenderer = gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
  } else {
    r.webglVendor = r.webglRenderer = null;
  }
  r.webgl2 = !!document.createElement("canvas").getContext("webgl2");
  return r;
}"""

# Bekannter CDP-Test: Ist Runtime.enable aktiv, liest DevTools beim Loggen den
# stack-Getter des Fehlers. Ergebnis landet im DOM, weil patchright in einer
# isolierten Welt auswertet.
LEAK_HTML = """<html><body><script>
const probe = () => {
  const e = new Error();
  Object.defineProperty(e, "stack", {get() {
    document.documentElement.dataset.leak = "1"; return ""; }});
  console.debug(e);
};
probe(); setInterval(probe, 200);
</script></body></html>"""


def starte(p):
    args = ["--ozone-platform=wayland", "--lang=de-DE"]
    args += [a for a in os.environ.get("EXTRA_CHROME_FLAGS", "").split() if a]
    optionen = dict(user_data_dir="/tmp/profil", channel="chrome",
                    headless=False, no_viewport=True, args=args)
    try:
        return p.chromium.launch_persistent_context(**optionen)
    except Exception as exc:
        # Playwright verlangt für sichtbare Browser teils ein DISPLAY, obwohl
        # Chrome hier über Wayland läuft.
        if "DISPLAY" not in str(exc) and "XServer" not in str(exc):
            raise
        os.environ["DISPLAY"] = ":0"
        return p.chromium.launch_persistent_context(**optionen)


def lokale_seite() -> str:
    """Liefert LEAK_HTML über 127.0.0.1 aus. Anders als about:blank und data:
    ist das ein sicherer Kontext – nur dort gibt es userAgentData und
    deviceMemory."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = LEAK_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


def stufe1(page) -> dict:
    page.goto(lokale_seite())
    time.sleep(2)
    werte = page.evaluate(PROBE_JS)
    werte["cdpLeak"] = page.evaluate("document.documentElement.dataset.leak === '1'")

    renderer = (werte.get("webglRenderer") or "").lower()
    brands = " ".join(werte.get("brands") or [])
    pruefung = {
        "webdriver_aus": werte.get("webdriver") is False,
        "gpu_hardware": bool(renderer) and not any(s in renderer for s in SOFTWARE_RENDERER),
        "echtes_chrome": "Google Chrome" in brands and "Headless" not in werte["userAgent"],
        "kein_cdp_leck": not werte["cdpLeak"],
    }
    return {"werte": werte, "pruefung": pruefung, "bestanden": all(pruefung.values())}


def stufe2(page, url: str) -> dict:
    antwort = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(8)  # Akamai-Sensor und nachladende Karten
    (OUT / "mobile.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(OUT / "mobile.png"))
    text = (page.evaluate("document.body ? document.body.innerText : ''") or "").lower()
    karten = page.locator(CARD_LINKS).count()
    sperre = [m for m in BLOCK_MARKERS if m in text]
    return {
        "status": antwort.status if antwort else None,
        "url": page.url,
        "titel": page.title(),
        "sperrmerkmale": sperre,
        "karten": karten,
        "treffer": karten > 0 and not sperre,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mobile", metavar="URL")
    args = ap.parse_args()
    if args.mobile and urlparse(args.mobile).hostname not in ERLAUBTE_HOSTS:
        print("Nur mobile.de-URLs erlaubt", file=sys.stderr)
        return 2

    OUT.mkdir(exist_ok=True)
    ergebnis: dict = {}
    with sync_playwright() as p:
        ctx = starte(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ergebnis["stufe1"] = stufe1(page)
        if args.mobile:
            if ergebnis["stufe1"]["bestanden"]:
                ergebnis["stufe2"] = stufe2(page, args.mobile)
            else:
                ergebnis["stufe2"] = "übersprungen: Stufe 1 nicht bestanden"
        ctx.close()

    text = json.dumps(ergebnis, indent=2, ensure_ascii=False)
    (OUT / "ergebnis.json").write_text(text, encoding="utf-8")
    print(text)
    return 0 if ergebnis["stufe1"]["bestanden"] else 1


if __name__ == "__main__":
    sys.exit(main())
