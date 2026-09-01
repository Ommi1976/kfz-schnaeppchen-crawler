const status = document.getElementById("status");

document.getElementById("senden").addEventListener("click", async () => {
  status.textContent = "Sende…";
  status.className = "";
  const ergebnis = await chrome.runtime.sendMessage({ typ: "senden" });
  status.textContent = ergebnis.meldung;
  status.className = ergebnis.ok ? "ok" : "fehler";
});

document.getElementById("optionen").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

chrome.storage.local.get(["letzteUebertragung", "letzteAnzahl"]).then((d) => {
  if (d.letzteUebertragung) {
    const zeit = new Date(d.letzteUebertragung).toLocaleString("de-DE");
    status.textContent = `Zuletzt: ${d.letzteAnzahl} Cookies um ${zeit}`;
  }
});
