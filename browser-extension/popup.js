// Liest mobile.de-Cookies über die native cookies-API und exportiert sie:
// entweder in die Zwischenablage ("Kopieren") oder direkt ans Add-on ("Senden").

const statusEl = document.getElementById("status");
const urlEl = document.getElementById("url");
const tokenEl = document.getElementById("token");

// Gespeicherte Add-on-Verbindung laden.
chrome.storage.local.get(["url", "token"], (cfg) => {
  if (cfg.url) urlEl.value = cfg.url;
  if (cfg.token) tokenEl.value = cfg.token;
  if (!cfg.url || !cfg.token) document.getElementById("cfg").open = true;
});
function saveCfg() {
  chrome.storage.local.set({ url: urlEl.value.trim(), token: tokenEl.value.trim() });
}
urlEl.addEventListener("change", saveCfg);
tokenEl.addEventListener("change", saveCfg);

async function collectCookies() {
  const urls = ["https://www.mobile.de/", "https://suchen.mobile.de/", "https://m.mobile.de/"];
  const map = {};
  for (const url of urls) {
    const cs = await chrome.cookies.getAll({ url });
    for (const c of cs) map[c.name] = c.value;
  }
  return map;
}

async function getCookieString() {
  const map = await collectCookies();
  const names = Object.keys(map);
  if (!names.length) throw new Error("Keine mobile.de-Cookies gefunden. Bist du eingeloggt?");
  if (!names.includes("_abck")) throw new Error("_abck fehlt – mobile.de neu laden und erneut versuchen.");
  return { str: names.map((k) => `${k}=${map[k]}`).join("; "), count: names.length };
}

function setStatus(html, ok) {
  statusEl.innerHTML = `<span class="${ok ? "ok" : "err"}">${html}</span>`;
}

document.getElementById("copy").addEventListener("click", async () => {
  try {
    const { str, count } = await getCookieString();
    await navigator.clipboard.writeText(str);
    setStatus(`✓ ${count} Cookies kopiert. Jetzt im Add-on einfügen.`, true);
  } catch (e) { setStatus(e.message, false); }
});

document.getElementById("send").addEventListener("click", async () => {
  const btn = document.getElementById("send");
  const url = urlEl.value.trim().replace(/\/+$/, "");
  const token = tokenEl.value.trim();
  if (!url || !token) {
    document.getElementById("cfg").open = true;
    setStatus("Bitte Add-on-URL und Token eintragen.", false);
    return;
  }
  saveCfg();
  btn.disabled = true; setStatus("sende…", true);
  try {
    const origin = new URL(url).origin + "/*";
    const granted = await chrome.permissions.request({ origins: [origin] }).catch(() => false);
    if (!granted) throw new Error("Berechtigung für die Add-on-URL abgelehnt.");
    const { str, count } = await getCookieString();
    const r = await fetch(`${url}/api/mobile-cookies`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-KFZ-Token": token },
      body: JSON.stringify({ cookies: str }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) setStatus(`✓ Gesendet – ${d.message || count + " Cookies"}`, true);
    else setStatus(d.message || d.detail || `Fehler ${r.status}`, false);
  } catch (e) { setStatus(e.message, false); }
  btn.disabled = false;
});
