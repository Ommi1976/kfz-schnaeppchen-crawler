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
  euro4: "Euro 4", euro5: "Euro 5", euro6: "Euro 6", euro6d: "Euro 6d", euro6e: "Euro 6e",
  allrad: "Allrad", front: "Front", heck: "Heck",
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
  for (const key of ["fuel", "transmission", "body_type", "seller", "doors", "emission_class", "drivetrain"]) {
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
  renderMobileBanner(s.mobile);
  document.getElementById("run").disabled = running;
}

// ---------- Suchen ----------
function chips(spec) {
  const c = [];
  if (spec.make) c.push(spec.make);
  if (spec.model) c.push(spec.model);
  if ((spec.exclude_makes || []).length) c.push(`− Hersteller: ${spec.exclude_makes.join(", ")}`);
  if ((spec.exclude_models || []).length) c.push(`− Modelle: ${spec.exclude_models.join(", ")}`);
  if (spec.fuel) c.push(label(spec.fuel));
  if (spec.transmission) c.push(label(spec.transmission));
  if (spec.body_type) c.push(label(spec.body_type));
  if (spec.price_from || spec.price_to) c.push(`${spec.price_from || 0}–${spec.price_to || "∞"} €`);
  if (spec.year_from || spec.year_to) c.push(`EZ ${spec.year_from || ""}–${spec.year_to || ""}`);
  if (spec.mileage_to) c.push(`≤${new Intl.NumberFormat("de-DE").format(spec.mileage_to)} km`);
  if (spec.power_from || spec.power_to) c.push(`${spec.power_from || 0}–${spec.power_to || "∞"} PS`);
  if (spec.seller) c.push(label(spec.seller));
  if (spec.zip_code) c.push(`📍 ${spec.zip_code}${spec.radius_km ? ` (+${spec.radius_km} km)` : ""}`);
  if (spec.ev_range_from) c.push(`≥${spec.ev_range_from} km Reichw.`);
  if (spec.battery_from_kwh) {
    const fallback = spec.ev_range_from ? ` oder Reichw. ≥${spec.ev_range_from} km` : "";
    c.push(`Akku ≥${spec.battery_from_kwh} kWh${fallback}`);
  }
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
    const mobileLink = statusCache?.portals_active?.includes("mobile_de")
      ? `<a class="btn small external" href="${mobileSearchUrl(s)}" target="_blank" rel="noopener">mobile.de ↗</a>`
      : "";
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
        ${mobileLink}
        <button class="btn small danger" data-del="${s.id}">Löschen</button>
      </div>
    </div>`;
  }).join("");
}
function chipsCount(s) {
  return ["make","model","fuel","transmission","body_type","seller","doors","zip_code","radius_km","year_from","year_to",
    "price_from","price_to","mileage_from","mileage_to","power_from","power_to","ev_range_from","battery_from_kwh"]
    .filter((k) => s[k]).length + (s.exclude_makes||[]).length + (s.exclude_models||[]).length
    + (s.keywords||[]).length + (s.exclude_terms||[]).length + (s.equipment||[]).length;
}

function mobileSearchUrl(s) {
  const params = new URLSearchParams({ isSearchRequest: "true", s: "Car", vc: "Car" });
  const span = (key, low, high) => {
    if (low != null || high != null) params.set(key, `${low ?? ""}:${high ?? ""}`);
  };
  span("p", s.price_from, s.price_to);
  span("fr", s.year_from, s.year_to);
  span("ml", s.mileage_from, s.mileage_to);
  if (s.zip_code) params.set("ambc", s.zip_code);
  if (s.radius_km) params.set("rad", String(s.radius_km));
  if (s.ev_range_from) params.set("re", String(Math.max(50, Math.floor(Number(s.ev_range_from) / 100) * 100)));
  if (s.battery_from_kwh) params.set("bc", String(Math.max(10, Math.floor(Number(s.battery_from_kwh) / 10) * 10)));
  if (s.make || s.model) params.set("q", [s.make, s.model].filter(Boolean).join(" "));
  return "https://suchen.mobile.de/fahrzeuge/search.html?" + params.toString();
}

function sohClass(soh) {
  if (soh == null) return "";
  if (soh >= 90) return "soh-good";
  if (soh >= 80) return "soh-mid";
  return "soh-low";
}

// ---------- Deals ----------
let currentPortalFilter = "";

async function loadDeals() {
  const sel = document.getElementById("searchFilter");
  const dealsOnly = document.getElementById("dealsOnly").checked;
  const params = [];
  if (sel.value) params.push(`search=${sel.value}`);
  if (dealsOnly) params.push("deals_only=true");
  if (currentPortalFilter) params.push(`portal=${encodeURIComponent(currentPortalFilter)}`);
  const data = await getJSON(`${API}/deals${params.length ? "?" + params.join("&") : ""}`);
  
  renderPortalFilters(data.portal_counts || {});

  const body = document.getElementById("deals-body");
  if (!data.deals.length) {
    body.innerHTML = `<tr><td colspan="10" class="empty">Noch keine Treffer. Lege eine Suche an und klick „Suchen".</td></tr>`;
  } else {
    body.innerHTML = data.deals.map((d) => {
      const disc = d.discount == null ? "" : `${d.discount < 0 ? "+" : "-"}${Math.abs(Math.round(d.discount * 100))} %`;
      let mark = "", rowcls = "";
      if (d.is_deal) { mark = `<span class="mark deal" title="Schnäppchen">★</span>`; rowcls = "row-deal"; }
      else if (d.is_suspicious) { mark = `<span class="mark susp" title="${escapeHtml(d.reasons || "verdächtig")}">⚠</span>`; rowcls = "row-susp"; }
      const pcls = "portal-" + (d.portal || "").toLowerCase().replace(/[^a-z0-9]/g, "");
      const sohBadge = d.battery_soh != null 
        ? `<span class="soh-badge ${sohClass(d.battery_soh)}" title="Batteriezustand (State of Health)">🔋 ${d.battery_soh} % SoH</span>`
        : "";
      const battInfo = d.battery_kwh != null
        ? `<span class="batt-badge" title="Akku-Kapazität">⚡ ${d.battery_kwh} kWh</span>`
        : "";
      const rangeInfo = d.ev_range_km != null
        ? `<span class="range-badge" title="Reichweite">🌐 ~${d.ev_range_km} km</span>`
        : "";
      const batteryCell = (battInfo || sohBadge || rangeInfo)
        ? `<div class="battery-cell">${battInfo}${sohBadge}${rangeInfo}</div>`
        : `<span class="muted">–</span>`;

      return `<tr class="${rowcls}">
        <td class="markcell">${mark}</td>
        <td><span class="portal-badge ${pcls}">${escapeHtml(d.portal || "")}</span></td>
        <td class="title">
          <div>${escapeHtml(d.title || "")}</div>
          ${d.is_suspicious ? `<div class="reason">${escapeHtml(d.reasons || "")}</div>` : ""}
        </td>
        <td class="battery-col">${batteryCell}</td>
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
    `${data.count} Treffer angezeigt${dealsOnly ? " (nur Schnäppchen)" : ""}${currentPortalFilter ? ` · Filter: ${currentPortalFilter}` : ""} · Auto-Aktualisierung alle 20 s`;
}

function renderPortalFilters(counts) {
  const box = document.getElementById("portal-filters");
  if (!box) return;
  const portals = ["mobile.de", "AutoScout24", "Kleinanzeigen"];
  let totalAll = 0;
  for (const k in counts) totalAll += counts[k];

  const items = [
    { id: "", label: "Alle Portale", count: totalAll }
  ];
  for (const p of portals) {
    items.push({ id: p, label: p, count: counts[p] || 0 });
  }

  box.innerHTML = items.map((it) => {
    const active = currentPortalFilter === it.id ? "active" : "";
    return `<button type="button" class="portal-pill ${active}" data-portal="${escapeHtml(it.id)}">
      <span class="p-name">${escapeHtml(it.label)}</span>
      <span class="p-count">${it.count}</span>
    </button>`;
  }).join("");

  box.querySelectorAll(".portal-pill").forEach((btn) => {
    btn.onclick = () => {
      currentPortalFilter = btn.dataset.portal;
      loadDeals();
    };
  });
}

// ---------- Formular ----------
const NUMS = ["year_from","year_to","price_from","price_to","mileage_from","mileage_to","radius_km","power_from","power_to","ev_range_from","battery_from_kwh"];
const SELS = ["make","model","fuel","transmission","body_type","seller","doors","emission_class","drivetrain"];

function openForm(spec) {
  document.getElementById("form-error").textContent = "";
  document.getElementById("modal-title").textContent = spec ? "Suche bearbeiten" : "Neue Suche";
  document.getElementById("f-id").value = spec ? spec.id : "";
  document.getElementById("f-name").value = spec ? spec.name : "";
  document.getElementById("f-active").checked = spec ? !!spec.active : true;
  document.getElementById("f-include_damaged").checked = spec ? !!spec.include_damaged : false;
  document.getElementById("f-zip_code").value = (spec && spec.zip_code) || "";
  SELS.forEach((k) => { document.getElementById("f-" + k).value = (spec && spec[k]) || ""; });
  NUMS.forEach((k) => { document.getElementById("f-" + k).value = (spec && spec[k] != null) ? spec[k] : ""; });
  ["exclude_makes", "exclude_models"].forEach((k) => {
    document.getElementById("f-" + k).value = spec && spec[k] ? spec[k].join(", ") : "";
  });
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
    exclude_makes: val("f-exclude_makes"), exclude_models: val("f-exclude_models"),
    fuel: val("f-fuel"), transmission: val("f-transmission"),
    body_type: val("f-body_type"), seller: val("f-seller"), doors: val("f-doors"),
    zip_code: val("f-zip_code"),
    emission_class: val("f-emission_class"), drivetrain: val("f-drivetrain"),
    include_damaged: document.getElementById("f-include_damaged").checked,
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
function renderMobileBanner(m) {
  const banner = document.getElementById("mobile-banner");
  if (!m || !m.active) { banner.classList.add("hidden"); return; }
  banner.classList.remove("hidden");
  const txt = document.getElementById("mobile-banner-text");
  banner.classList.remove("ok", "warn");
  if (m.state === "ok") {
    banner.classList.add("ok");
    txt.innerHTML = "✓ <b>mobile.de</b> autark aktiv (Playwright Firefox Headless – kein PC-Browser nötig).";
  } else if (m.state === "expired") {
    banner.classList.add("warn");
    txt.innerHTML = "⚠ <b>mobile.de</b>: Modus wird automatisch synchronisiert.";
  } else {
    banner.classList.add("ok");
    txt.innerHTML = "✓ <b>mobile.de</b> aktiv.";
  }
}

document.getElementById("mobile-open").addEventListener("click", () => {
  document.getElementById("mobile-result").textContent = "";
  document.getElementById("mobile-cookies").value = "";
  const tok = statusCache && statusCache.mobile && statusCache.mobile.ingest_token;
  const box = document.getElementById("mobile-token-box");
  if (tok) {
    document.getElementById("mobile-token").textContent = tok;
    box.style.display = "";
  } else {
    box.style.display = "none";
  }
  document.getElementById("mobile-modal").classList.remove("hidden");
});
document.getElementById("mobile-close").addEventListener("click", () =>
  document.getElementById("mobile-modal").classList.add("hidden"));
document.getElementById("mobile-modal").addEventListener("click", (e) => {
  if (e.target.id === "mobile-modal") e.currentTarget.classList.add("hidden");
});
document.getElementById("mobile-test").addEventListener("click", async () => {
  const btn = document.getElementById("mobile-test");
  const res = document.getElementById("mobile-result");
  const cookies = document.getElementById("mobile-cookies").value.trim();
  btn.disabled = true; res.textContent = "teste…"; res.className = "form-error";
  try {
    const tok = (statusCache && statusCache.mobile && statusCache.mobile.ingest_token) || "";
    const r = await fetch(`${API}/mobile-cookies`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-KFZ-Token": tok },
      body: JSON.stringify({ cookies }),
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      res.textContent = "✓ " + d.message; res.style.color = "#86efac";
      setTimeout(() => { document.getElementById("mobile-modal").classList.add("hidden"); refresh(); }, 1200);
    } else {
      res.style.color = ""; res.textContent = d.message || d.detail || "Fehler";
    }
  } catch (e) { res.textContent = String(e); }
  btn.disabled = false;
});

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
