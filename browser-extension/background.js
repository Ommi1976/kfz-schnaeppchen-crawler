/* Hintergrunddienst: hält die mobile.de-Sitzung im Add-on frisch.
 *
 * Gelesen werden ausschließlich Cookies der Domain mobile.de, gesendet wird
 * ausschließlich an die in den Optionen hinterlegte Add-on-Adresse. Es werden
 * keine Zugangsdaten gespeichert und nichts an Dritte übertragen.
 */

const ALARM = "kfz-cookie-refresh";

async function ladeEinstellungen() {
  const { endpoint = "", token = "", autoMinuten = 30, auto = false } =
    await chrome.storage.local.get(["endpoint", "token", "autoMinuten", "auto"]);
  return { endpoint, token, autoMinuten, auto };
}

/** Liest die mobile.de-Cookies als "name=wert; …"-Zeichenkette. */
async function leseCookies() {
  const cookies = await chrome.cookies.getAll({ domain: "mobile.de" });
  if (!cookies.length) return "";
  return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
}

/** Kurzer Fingerabdruck, um unveränderte Cookies nicht erneut zu senden. */
async function fingerabdruck(text) {
  const daten = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-256", daten);
  return [...new Uint8Array(hash)].slice(0, 8).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function senden({ erzwingen = false } = {}) {
  const { endpoint, token } = await ladeEinstellungen();
  if (!endpoint || !token) {
    return { ok: false, meldung: "Adresse oder Token fehlt – bitte in den Optionen eintragen." };
  }

  const cookies = await leseCookies();
  if (!cookies) {
    return { ok: false, meldung: "Keine mobile.de-Cookies gefunden. Bist du dort angemeldet?" };
  }
  if (!cookies.includes("_abck")) {
    return { ok: false, meldung: "Sitzung unvollständig (_abck fehlt). Lade mobile.de einmal neu." };
  }

  const fp = await fingerabdruck(cookies);
  const { letzterFingerabdruck } = await chrome.storage.local.get("letzterFingerabdruck");
  if (!erzwingen && fp === letzterFingerabdruck) {
    return { ok: true, meldung: "Unverändert – nichts zu senden.", unveraendert: true };
  }

  try {
    const antwort = await fetch(endpoint.replace(/\/+$/, "") + "/api/mobile-cookies", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-KFZ-Token": token },
      body: JSON.stringify({ cookies }),
    });
    if (!antwort.ok) {
      const text = await antwort.text();
      return { ok: false, meldung: `Add-on antwortete ${antwort.status}: ${text.slice(0, 120)}` };
    }
    const daten = await antwort.json();
    await chrome.storage.local.set({
      letzterFingerabdruck: fp,
      letzteUebertragung: Date.now(),
      letzteAnzahl: daten.saved_count || 0,
    });
    return { ok: true, meldung: `${daten.saved_count} Cookies übertragen.` };
  } catch (e) {
    return { ok: false, meldung: `Add-on nicht erreichbar: ${e.message}` };
  }
}

async function planen() {
  const { auto, autoMinuten } = await ladeEinstellungen();
  await chrome.alarms.clear(ALARM);
  if (auto) {
    // Untergrenze 15 Minuten: häufiger bringt nichts, die Sitzung ändert sich
    // nur beim Surfen auf mobile.de.
    chrome.alarms.create(ALARM, { periodInMinutes: Math.max(15, Number(autoMinuten) || 30) });
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) senden();
});

chrome.runtime.onStartup.addListener(planen);
chrome.runtime.onInstalled.addListener(planen);
chrome.storage.onChanged.addListener((aenderungen) => {
  if (aenderungen.auto || aenderungen.autoMinuten) planen();
});

chrome.runtime.onMessage.addListener((nachricht, _absender, antworten) => {
  if (nachricht?.typ === "senden") {
    senden({ erzwingen: true }).then(antworten);
    return true; // asynchrone Antwort
  }
});
