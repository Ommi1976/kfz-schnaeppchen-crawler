// Relative URLs, damit alles hinter dem Home-Assistant-Ingress-Pfad funktioniert.
const API = "api";

const euro = (n) =>
  n == null ? "–" : new Intl.NumberFormat("de-DE").format(n) + " €";
const km = (n) =>
  n == null ? "–" : new Intl.NumberFormat("de-DE").format(n) + " km";

function timeAgo(epochSeconds) {
  if (!epochSeconds) return "–";
  const s = Math.floor(Date.now() / 1000 - epochSeconds);
  if (s < 60) return "gerade eben";
  if (s < 3600) return Math.floor(s / 60) + " min";
  if (s < 86400) return Math.floor(s / 3600) + " h";
  return Math.floor(s / 86400) + " d";
}

function fmtClock(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

async function getJSON(path) {
  const r = await fetch(path, { headers: { "Accept": "application/json" } });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

function discountClass(d) {
  if (d == null) return "d-lo";
  if (d >= 0.25) return "d-hi";
  if (d >= 0.15) return "d-mid";
  return "d-lo";
}

let statusCache = null;

async function loadStatus() {
  const s = await getJSON(`${API}/status`);
  statusCache = s;

  const running = s.running;
  document.getElementById("subline").innerHTML =
    `<span class="dot ${running ? "on" : "idle"}"></span>` +
    (running ? "Suche läuft…" : "bereit") +
    ` · v${s.version} · Portale: ${(s.portals_active || []).join(", ") || "–"}`;

  const nextIn = s.next_run_at
    ? Math.max(0, Math.round((s.next_run_at - Date.now() / 1000) / 60))
    : null;

  const cards = [
    { k: "Schnäppchen gesamt", v: s.total_deals ?? "–" },
    { k: "Suchen", v: (s.searches || []).length },
    { k: "Letzter Lauf", v: fmtClock(s.last_finished_at || s.last_run_at) },
    { k: "Nächster Lauf", v: nextIn == null ? "–" : `in ${nextIn} min` },
    { k: "Schwelle", v: Math.round((s.deal_threshold || 0) * 100) + " %" },
  ];
  document.getElementById("stats").innerHTML = cards
    .map((c) => `<div class="card"><div class="k">${c.k}</div><div class="v">${c.v}</div></div>`)
    .join("");

  const sel = document.getElementById("searchFilter");
  const current = sel.value;
  const opts = ['<option value="">Alle Suchen</option>']
    .concat((s.searches || []).map((x) => {
      const cnt = x.count == null ? "" : ` (${x.count})`;
      return `<option value="${encodeURIComponent(x.name)}">${x.name}${cnt}</option>`;
    }));
  sel.innerHTML = opts.join("");
  sel.value = current;

  document.getElementById("run").disabled = running;
}

async function loadDeals() {
  const sel = document.getElementById("searchFilter");
  const q = sel.value ? `?search=${sel.value}` : "";
  const data = await getJSON(`${API}/deals${q}`);
  const body = document.getElementById("deals-body");
  if (!data.deals.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty">Noch keine Schnäppchen gefunden. Klick „Jetzt suchen".</td></tr>`;
  } else {
    body.innerHTML = data.deals.map((d) => {
      const disc = d.discount == null ? "" : `-${Math.round(d.discount * 100)} %`;
      return `<tr>
        <td><span class="portal-badge">${d.portal || ""}</span></td>
        <td class="title">${escapeHtml(d.title || "")}</td>
        <td class="num">${euro(d.price)}</td>
        <td class="num">${euro(d.market_price)}</td>
        <td class="num discount ${discountClass(d.discount)}">${disc}</td>
        <td class="num">${d.year || "–"}</td>
        <td class="num">${km(d.mileage)}</td>
        <td>${timeAgo(d.first_seen)}</td>
        <td><a class="link" href="${d.url}" target="_blank" rel="noopener">öffnen ↗</a></td>
      </tr>`;
    }).join("");
  }
  document.getElementById("footer-info").textContent =
    `${data.count} Treffer angezeigt · Auto-Aktualisierung alle 20 s`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function refresh() {
  try { await loadStatus(); await loadDeals(); }
  catch (e) { console.error(e); }
}

document.getElementById("run").addEventListener("click", async () => {
  const btn = document.getElementById("run");
  btn.disabled = true; btn.textContent = "läuft…";
  try { await fetch(`${API}/run`, { method: "POST" }); }
  catch (e) { console.error(e); }
  setTimeout(() => { btn.textContent = "Jetzt suchen"; poll(); }, 1500);
});

document.getElementById("refresh").addEventListener("click", refresh);
document.getElementById("searchFilter").addEventListener("change", loadDeals);
document.getElementById("clear").addEventListener("click", async () => {
  if (!confirm("Trefferliste wirklich leeren? (Duplikat-Filter bleibt erhalten)")) return;
  await fetch(`${API}/deals`, { method: "DELETE" });
  refresh();
});

// Während eine Suche läuft, häufiger pollen, bis sie fertig ist.
function poll() {
  refresh().then(() => {
    if (statusCache && statusCache.running) setTimeout(poll, 2000);
  });
}

refresh();
setInterval(refresh, 20000);
