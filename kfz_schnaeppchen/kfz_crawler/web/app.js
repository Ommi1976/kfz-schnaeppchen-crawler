// Relative URLs, damit alles hinter dem Home-Assistant-Ingress-Pfad funktioniert.
const API = "api";
let META = null;
let statusCache = null;

const LABELS = {
  "": "— egal —",
  benzin: "Benzin", diesel: "Diesel", elektro: "Elektro", hybrid: "Hybrid",
  lpg: "Autogas (LPG)", cng: "Erdgas (CNG)",
  schaltgetriebe: "Schaltgetriebe", automatik: "Automatik",
  limousine: "Limousine", kombi: "Kombi", suv: "SUV/Geländewagen",
  cabrio: "Cabrio", coupe: "Coupé", van: "Van/Bus", kleinwagen: "Kleinwagen",
  haendler: "Händler", privat: "Privat", "2/3": "2/3 Türen", "4/5": "4/5 Türen",
};
const label = (v) => LABELS[v] ?? v;

const euro = (n) => (n == null ? "–" : new Intl.NumberFormat("de-DE").format(n) + " €");
const km = (n) => (n == null ? "–" : new Intl.NumberFormat("de-DE").format(n) + " km");

function timeAgo(s) {
  if (!s) return "–";
  const d = Math.floor(Date.now() / 1000 - s);
  if (d < 60) return "gerade eben";
  if (d < 3600) return Math.floor(d / 60) + " min";
  if (d < 86400) return Math.floor(d / 3600) + " h";
  return Math.floor(d / 86400) + " d";
}
function fmtClock(iso) {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function getJSON(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}
function discountClass(d) {
  if (d == null) return "d-lo";
  if (d >= 0.25) return "d-hi";
  if (d >= 0.15) return "d-mid";
  return "d-lo";
}

// ---------- Meta / Selects ----------
async function loadMeta() {
  META = await getJSON(`${API}/meta`);
  for (const key of ["fuel", "transmission", "body_type", "seller", "doors"]) {
    const el = document.getElementById("f-" + key);
    el.innerHTML = META[key].map((v) => `<option value="${v}">${label(v)}</option>`).join("");
  }
  renderEquipment(META.equipment_groups || []);
}

function renderEquipment(groups) {
  const box = document.getElementById("equip-groups");
  box.innerHTML = groups.map((g) => `
    <div class="equip-group">
      <h4>${escapeHtml(g.group)}</h4>
      <div class="equip-items">
        ${g.items.map((it) => `<label data-lbl="${escapeHtml(it.label.toLowerCase())}">
          <input type="checkbox" class="eq-box" value="${it.id}"> ${escapeHtml(it.label)}
        </label>`).join("")}
      </div>
    </div>`).join("");
  box.addEventListener("change", updateEquipCount);
}

function updateEquipCount() {
  const n = document.querySelectorAll(".eq-box:checked").length;
  document.getElementById("equip-count").textContent = n + " gewählt";
}
function setEquipment(ids) {
  const set = new Set((ids || []).map(Number));
  document.querySelectorAll(".eq-box").forEach((b) => { b.checked = set.has(Number(b.value)); });
  updateEquipCount();
}
function getEquipment() {
  return Array.from(document.querySelectorAll(".eq-box:checked")).map((b) => Number(b.value));
}
function filterEquip(term) {
  const t = (term || "").trim().toLowerCase();
  document.querySelectorAll("#equip-groups .equip-items label").forEach((lb) => {
    lb.classList.toggle("hide", t && !lb.dataset.lbl.includes(t));
  });
}

// ---------- Status / Stats ----------
async function loadStatus() {
  const s = await getJSON(`${API}/status`);
  statusCache = s;
  const running = s.running;
  document.getElementById("subline").innerHTML =
    `<span class="dot ${running ? "on" : "idle"}"></span>` +
    (running ? "Suche läuft…" : "bereit") +
    ` · v${s.version} · Portale: ${(s.portals_active || []).join(", ") || "–"}`;

  const nextIn = s.next_run_at ? Math.max(0, Math.round((s.next_run_at - Date.now() / 1000) / 60)) : null;
  const cards = [
    { k: "Schnäppchen", v: s.total_deals ?? "–" },
    { k: "Inserate gesamt", v: s.total_listings ?? "–" },
    { k: "Suchen", v: (s.searches || []).length },
    { k: "Letzter Lauf", v: fmtClock(s.last_finished_at || s.last_run_at) },
    { k: "Nächster Lauf", v: nextIn == null ? "–" : `in ${nextIn} min` },
    { k: "Schwelle", v: Math.round((s.deal_threshold || 0) * 100) + " %" },
  ];
  document.getElementById("stats").innerHTML = cards
    .map((c) => `<div class="card"><div class="k">${c.k}</div><div class="v">${c.v}</div></div>`).join("");

  const sel = document.getElementById("searchFilter");
  const cur = sel.value;
  sel.innerHTML = ['<option value="">Alle Suchen</option>']
    .concat((s.searches || []).map((x) => `<option value="${encodeURIComponent(x.name)}">${escapeHtml(x.name)}</option>`)).join("");
  sel.value = cur;

  renderSearches(s.searches || []);
  document.getElementById("run").disabled = running;
}

// ---------- Suchen ----------
function chips(spec) {
  const c = [];
  if (spec.make) c.push(spec.make);
  if (spec.model) c.push(spec.model);
  if (spec.fuel) c.push(label(spec.fuel));
  if (spec.transmission) c.push(label(spec.transmission));
  if (spec.body_type) c.push(label(spec.body_type));
  if (spec.price_from || spec.price_to) c.push(`${spec.price_from || 0}–${spec.price_to || "∞"} €`);
  if (spec.year_from || spec.year_to) c.push(`EZ ${spec.year_from || ""}–${spec.year_to || ""}`);
  if (spec.mileage_to) c.push(`≤${new Intl.NumberFormat("de-DE").format(spec.mileage_to)} km`);
  if (spec.power_from || spec.power_to) c.push(`${spec.power_from || 0}–${spec.power_to || "∞"} PS`);
  if (spec.seller) c.push(label(spec.seller));
  if (spec.ev_range_from) c.push(`≥${spec.ev_range_from} km Reichw.`);
  if ((spec.equipment || []).length) c.push(`🔧 ${spec.equipment.length} Ausstattung`);
  (spec.keywords || []).forEach((k) => c.push("＋" + k));
  (spec.exclude_terms || []).forEach((k) => c.push("－" + k));
  return c.map((x) => `<span class="chip">${escapeHtml(String(x))}</span>`).join("");
}

function renderSearches(searches) {
  const box = document.getElementById("search-list");
  if (!searches.length) {
    box.innerHTML = `<div class="empty">Noch keine Suche. Klick „＋ Neue Suche".</div>`;
    return;
  }
  box.innerHTML = searches.map((s) => {
    const cnt = s.count == null ? "" : ` · ${s.count} neu`;
    return `<div class="search-card ${s.active ? "" : "inactive"}">
      <div class="name">${escapeHtml(s.name)}
        <span class="badge ${s.active ? "on" : ""}">${s.active ? "aktiv" : "aus"}</span>
        <span class="badge">${chipsCount(s)} Filter${cnt}</span>
      </div>
      <div class="params">${chips(s) || "<span class='chip'>alle Fahrzeuge</span>"}</div>
      <div class="row">
        <button class="btn small" data-run="${s.id}">Suchen</button>
        <button class="btn small" data-toggle="${s.id}">${s.active ? "Deaktivieren" : "Aktivieren"}</button>
        <button class="btn small" data-edit="${s.id}">Bearbeiten</button>
        <button class="btn small danger" data-del="${s.id}">Löschen</button>
      </div>
    </div>`;
  }).join("");
}
function chipsCount(s) {
  return ["make","model","fuel","transmission","body_type","seller","doors","year_from","year_to",
    "price_from","price_to","mileage_from","mileage_to","power_from","power_to","ev_range_from","battery_from_kwh"]
    .filter((k) => s[k]).length + (s.keywords||[]).length + (s.exclude_terms||[]).length + (s.equipment||[]).length;
}

// ---------- Deals ----------
async function loadDeals() {
  const sel = document.getElementById("searchFilter");
  const dealsOnly = document.getElementById("dealsOnly").checked;
  const params = [];
  if (sel.value) params.push(`search=${sel.value}`);
  if (dealsOnly) params.push("deals_only=true");
  const data = await getJSON(`${API}/deals${params.length ? "?" + params.join("&") : ""}`);
  const body = document.getElementById("deals-body");
  if (!data.deals.length) {
    body.innerHTML = `<tr><td colspan="10" class="empty">Noch keine Treffer. Lege eine Suche an und klick „Suchen".</td></tr>`;
  } else {
    body.innerHTML = data.deals.map((d) => {
      const disc = d.discount == null ? "" : `${d.discount < 0 ? "+" : "-"}${Math.abs(Math.round(d.discount * 100))} %`;
      let mark = "", rowcls = "";
      if (d.is_deal) { mark = `<span class="mark deal" title="Schnäppchen">★</span>`; rowcls = "row-deal"; }
      else if (d.is_suspicious) { mark = `<span class="mark susp" title="${escapeHtml(d.reasons || "verdächtig")}">⚠</span>`; rowcls = "row-susp"; }
      return `<tr class="${rowcls}">
        <td class="markcell">${mark}</td>
        <td><span class="portal-badge">${escapeHtml(d.portal || "")}</span></td>
        <td class="title">${escapeHtml(d.title || "")}${d.is_suspicious ? `<div class="reason">${escapeHtml(d.reasons || "")}</div>` : ""}</td>
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
    `${data.count} Treffer angezeigt${dealsOnly ? " (nur Schnäppchen)" : ""} · Auto-Aktualisierung alle 20 s`;
}

// ---------- Formular ----------
const NUMS = ["year_from","year_to","price_from","price_to","mileage_from","mileage_to","power_from","power_to","ev_range_from","battery_from_kwh"];
const SELS = ["make","model","fuel","transmission","body_type","seller","doors"];

function openForm(spec) {
  document.getElementById("form-error").textContent = "";
  document.getElementById("modal-title").textContent = spec ? "Suche bearbeiten" : "Neue Suche";
  document.getElementById("f-id").value = spec ? spec.id : "";
  document.getElementById("f-name").value = spec ? spec.name : "";
  document.getElementById("f-active").checked = spec ? !!spec.active : true;
  SELS.forEach((k) => { document.getElementById("f-" + k).value = (spec && spec[k]) || ""; });
  NUMS.forEach((k) => { document.getElementById("f-" + k).value = (spec && spec[k] != null) ? spec[k] : ""; });
  document.getElementById("f-keywords").value = spec && spec.keywords ? spec.keywords.join(", ") : "";
  document.getElementById("f-exclude_terms").value = spec && spec.exclude_terms ? spec.exclude_terms.join(", ") : "";
  setEquipment(spec ? spec.equipment : []);
  document.getElementById("equip-search").value = "";
  filterEquip("");
  document.getElementById("modal").classList.remove("hidden");
}
function closeForm() { document.getElementById("modal").classList.add("hidden"); }

function collectForm() {
  const val = (id) => document.getElementById(id).value.trim();
  const num = (id) => { const v = val(id); return v === "" ? null : Number(v); };
  const spec = {
    id: val("f-id"),
    name: val("f-name"),
    active: document.getElementById("f-active").checked,
    make: val("f-make"), model: val("f-model"),
    fuel: val("f-fuel"), transmission: val("f-transmission"),
    body_type: val("f-body_type"), seller: val("f-seller"), doors: val("f-doors"),
    keywords: val("f-keywords"), exclude_terms: val("f-exclude_terms"),
    equipment: getEquipment(),
  };
  NUMS.forEach((k) => { spec[k] = num("f-" + k); });
  return spec;
}

async function submitForm(ev) {
  ev.preventDefault();
  const spec = collectForm();
  const id = spec.id;
  try {
    const r = await fetch(id ? `${API}/searches/${id}` : `${API}/searches`, {
      method: id ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      document.getElementById("form-error").textContent = e.detail || ("Fehler " + r.status);
      return;
    }
    closeForm();
    refresh();
  } catch (e) {
    document.getElementById("form-error").textContent = String(e);
  }
}

// ---------- Events ----------
document.getElementById("new-search").addEventListener("click", () => openForm(null));
document.getElementById("modal-close").addEventListener("click", closeForm);
document.getElementById("modal-cancel").addEventListener("click", closeForm);
document.getElementById("search-form").addEventListener("submit", submitForm);
document.getElementById("equip-search").addEventListener("input", (e) => filterEquip(e.target.value));
document.getElementById("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeForm(); });

document.getElementById("search-list").addEventListener("click", async (e) => {
  const t = e.target;
  if (t.dataset.edit) {
    const s = (statusCache.searches || []).find((x) => x.id === t.dataset.edit);
    openForm(s);
  } else if (t.dataset.del) {
    if (!confirm("Diese Suche löschen?")) return;
    await fetch(`${API}/searches/${t.dataset.del}`, { method: "DELETE" });
    refresh();
  } else if (t.dataset.toggle) {
    const s = (statusCache.searches || []).find((x) => x.id === t.dataset.toggle);
    if (!s) return;
    const spec = { ...s, active: !s.active };
    await fetch(`${API}/searches/${s.id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(spec),
    });
    refresh();
  } else if (t.dataset.run) {
    t.disabled = true; t.textContent = "läuft…";
    await fetch(`${API}/searches/${t.dataset.run}/run`, { method: "POST" });
    setTimeout(() => { poll(); }, 1200);
  }
});

document.getElementById("run").addEventListener("click", async () => {
  const btn = document.getElementById("run");
  btn.disabled = true; btn.textContent = "läuft…";
  await fetch(`${API}/run`, { method: "POST" });
  setTimeout(() => { btn.textContent = "Alle jetzt suchen"; poll(); }, 1500);
});
document.getElementById("refresh").addEventListener("click", refresh);
document.getElementById("searchFilter").addEventListener("change", loadDeals);
document.getElementById("dealsOnly").addEventListener("change", loadDeals);
document.getElementById("clear").addEventListener("click", async () => {
  if (!confirm("Trefferliste wirklich leeren? (Duplikat-Filter bleibt erhalten)")) return;
  await fetch(`${API}/deals`, { method: "DELETE" });
  refresh();
});

async function refresh() {
  try { await loadStatus(); await loadDeals(); } catch (e) { console.error(e); }
}
function poll() {
  refresh().then(() => { if (statusCache && statusCache.running) setTimeout(poll, 2000); });
}

(async function init() {
  try { await loadMeta(); } catch (e) { console.error(e); }
  await refresh();
  setInterval(refresh, 20000);
})();
