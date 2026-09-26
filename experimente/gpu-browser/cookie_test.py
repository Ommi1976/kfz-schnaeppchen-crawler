"""Stufe 3: EINE Anfrage mit der echten Edge-Sitzung, gesendet über curl_cffi.

Eingabe ist ein "Als cURL (bash) kopieren" aus den Edge-DevTools: Er enthält
URL, Cookies, User-Agent und die passenden sec-ch-ua-Header. Diese Header
müssen zur Sitzung passen, sonst widerspricht der Abruf den Cookies.
Cookies werden nie ausgegeben.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests

OUT = Path("/out")
# Sichtbarer Text wie kfz_crawler/browser.py; ein Skriptname mit 'captcha' ist
# keine Sperre. Akamais Krypto-Challenge steckt dagegen im Markup.
BLOCK_MARKERS = ("zugriff verweigert", "access denied", "temporarily blocked",
                 "unusual traffic", "captcha")
CHALLENGE_MARKERS = ("cp_challenge", "sec-if-cpt")
ERLAUBTE_HOSTS = {"suchen.mobile.de", "www.mobile.de"}
MIT_WERT = {"-X", "--request", "-d", "--data", "--data-raw", "--data-binary"}


def lies_curl(text: str) -> tuple[str, dict]:
    if '^"' in text:
        raise SystemExit("Das ist das cmd-Format. Bitte 'Als cURL (bash) kopieren' verwenden.")
    text = text.replace("\\\r\n", " ").replace("\\\n", " ")
    teile = shlex.split(text)
    if not teile or teile[0] != "curl":
        raise SystemExit("Datei enthält keinen cURL-Befehl.")
    url, header = None, {}
    i = 1
    while i < len(teile):
        teil = teile[i]
        if teil in ("-H", "--header"):
            name, _, wert = teile[i + 1].partition(":")
            header[name.strip().lower()] = wert.strip()
            i += 2
        elif teil in ("-b", "--cookie"):
            header["cookie"] = teile[i + 1]
            i += 2
        elif teil in ("-A", "--user-agent"):
            header["user-agent"] = teile[i + 1]
            i += 2
        elif teil in MIT_WERT:
            i += 2
        elif teil.startswith("-"):
            i += 1
        else:
            url = url or teil
            i += 1
    # Komprimierung übernimmt die Chrome-Imitation selbst.
    header.pop("accept-encoding", None)
    return url, header


def main() -> int:
    quelle = Path(sys.argv[1] if len(sys.argv) > 1 else "/eingabe/edge-curl.txt")
    if not quelle.exists():
        print(f"{quelle} fehlt", file=sys.stderr)
        return 2
    url, header = lies_curl(quelle.read_text(encoding="utf-8"))
    if urlparse(url or "").hostname not in ERLAUBTE_HOSTS:
        print("Nur mobile.de-URLs erlaubt", file=sys.stderr)
        return 2

    auszug = {
        "url": url,
        "user_agent": header.get("user-agent"),
        "sec_ch_ua": header.get("sec-ch-ua"),
        "hat_abck": "_abck=" in header.get("cookie", ""),
        "hat_bm_sz": "bm_sz=" in header.get("cookie", ""),
    }
    try:
        antwort = requests.get(url, headers=header, impersonate="chrome",
                               default_headers=False, timeout=30)
    except TypeError:
        antwort = requests.get(url, headers=header, impersonate="chrome", timeout=30)

    html = antwort.text
    OUT.mkdir(exist_ok=True)
    (OUT / "cookie-antwort.html").write_text(html, encoding="utf-8")
    sichtbar = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", html,
                      flags=re.S | re.I).lower()
    ids = set(re.findall(r"details\.html\?id=(\d+)", html))
    titel = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    sperre = ([m for m in BLOCK_MARKERS if m in sichtbar]
              + [m for m in CHALLENGE_MARKERS if m in html.lower()])
    ergebnis = {
        "anfrage": auszug,
        "status": antwort.status_code,
        "end_url": str(antwort.url),
        "titel": titel.group(1).strip() if titel else None,
        "bytes": len(html),
        "inserat_ids": len(ids),
        "sperrmerkmale": sperre,
        "treffer": antwort.status_code == 200 and bool(ids) and not sperre,
    }
    text = json.dumps({"stufe3": ergebnis}, indent=2, ensure_ascii=False)
    (OUT / "ergebnis.json").write_text(text, encoding="utf-8")
    print(text)
    return 0 if ergebnis["treffer"] else 1


if __name__ == "__main__":
    sys.exit(main())
