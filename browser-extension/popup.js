// Liest die mobile.de-Cookies über die native cookies-API (keine Entschlüsselung
// nötig) und kopiert sie als Cookie-String in die Zwischenablage.

const statusEl = document.getElementById("status");

async function collectCookies() {
  const urls = [
    "https://www.mobile.de/",
    "https://suchen.mobile.de/",
    "https://m.mobile.de/",
  ];
  const map = {};
  for (const url of urls) {
    const cs = await chrome.cookies.getAll({ url });
    for (const c of cs) map[c.name] = c.value;
  }
  return map;
}

document.getElementById("copy").addEventListener("click", async () => {
  const btn = document.getElementById("copy");
  btn.disabled = true;
  statusEl.textContent = "";
  try {
    const map = await collectCookies();
    const names = Object.keys(map);
    if (names.length === 0) {
      statusEl.innerHTML = '<span class="err">Keine mobile.de-Cookies gefunden. Bist du eingeloggt?</span>';
      btn.disabled = false;
      return;
    }
    const str = names.map((k) => `${k}=${map[k]}`).join("; ");
    await navigator.clipboard.writeText(str);
    const hasAbck = names.includes("_abck");
    statusEl.innerHTML = hasAbck
      ? `<span class="ok">✓ ${names.length} Cookies kopiert (inkl. _abck). Jetzt im Add-on einfügen.</span>`
      : `<span class="err">${names.length} Cookies kopiert, aber _abck fehlt – bitte mobile.de neu laden und erneut versuchen.</span>`;
  } catch (e) {
    statusEl.innerHTML = `<span class="err">Fehler: ${e.message}</span>`;
  }
  btn.disabled = false;
});
