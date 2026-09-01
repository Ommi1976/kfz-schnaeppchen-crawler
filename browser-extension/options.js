const felder = ["endpoint", "token", "autoMinuten"];

async function laden() {
  const daten = await chrome.storage.local.get([...felder, "auto"]);
  felder.forEach((f) => { if (daten[f] !== undefined) document.getElementById(f).value = daten[f]; });
  document.getElementById("auto").checked = Boolean(daten.auto);
}

document.getElementById("speichern").addEventListener("click", async () => {
  const status = document.getElementById("status");
  const endpoint = document.getElementById("endpoint").value.trim();
  const token = document.getElementById("token").value.trim();
  const auto = document.getElementById("auto").checked;
  const autoMinuten = Math.max(15, Number(document.getElementById("autoMinuten").value) || 30);

  if (!endpoint || !token) { status.textContent = "Adresse und Token werden beide gebraucht."; return; }

  // Zugriff auf genau diese Adresse anfragen – nicht pauschal auf alle Seiten.
  let muster;
  try { muster = new URL(endpoint).origin + "/*"; }
  catch { status.textContent = "Die Adresse ist keine gültige URL."; return; }

  const erlaubt = await chrome.permissions.request({ origins: [muster] });
  if (!erlaubt) { status.textContent = "Ohne Zugriffsrecht auf diese Adresse geht es nicht."; return; }

  await chrome.storage.local.set({ endpoint, token, auto, autoMinuten });
  status.textContent = "Gespeichert.";
});

laden();
