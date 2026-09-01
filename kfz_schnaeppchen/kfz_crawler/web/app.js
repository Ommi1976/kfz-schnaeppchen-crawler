// Relative URLs, damit alles hinter dem Home-Assistant-Ingress-Pfad funktioniert.
const API = "api";
let META = null;
let statusCache = null;

const PORTAL_LABELS = {
  mobile_de: "mobile.de",
  autoscout24: "AutoScout24",
  kleinanzeigen: "Kleinanzeigen",
  autouncle: "AutoUncle",
};

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
  tolerant: "Unbekannte Werte zulassen", strict: "Nur vollständig belegte Treffer",
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

const COUNTRY_LABELS = {
  DE: "Deutschland (DE)",
  AT: "Österreich (AT)",
  CH: "Schweiz (CH)",
  FR: "Frankreich (FR)",
  NL: "Niederlande (NL)",
  BE: "Belgien (BE)",
  IT: "Italien (IT)",
  ES: "Spanien (ES)",
  PL: "Polen (PL)",
  LU: "Luxemburg (LU)",
  ALL: "Alle Länder (Europa)",
};

// ---------- Meta / Selects ----------
async function loadMeta() {
  META = await getJSON(`${API}/meta`);
  for (const key of ["fuel", "transmission", "body_type", "seller", "doors", "emission_class", "drivetrain", "unknown_policy"]) {
    const el = document.getElementById("f-" + key);
    if (el) el.innerHTML = META[key].map((v) => `<option value="${v}">${label(v)}</option>`).join("");
  }
  const cEl = document.getElementById("f-country");
  if (cEl && META.country) {
    cEl.innerHTML = META.country.map((c) => `<option value="${c}">${COUNTRY_LABELS[c] || c}</option>`).join("");
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
    { k: "Schnäppchen", v: s.total_deals ?? "–", id: "card-deals", cls: "card card-deals clickable" },
    { k: "Inserate gesamt", v: s.total_listings ?? "–", id: "card-all", cls: "card card-all clickable" },
    { k: "Suchen", v: (s.searches || []).length, cls: "card" },
    { k: "Letzter Lauf", v: fmtClock(s.last_finished_at || s.last_run_at), cls: "card" },
    { k: "Nächster Lauf", v: nextIn == null ? "–" : `in ${nextIn} min`, cls: "card" },
    { k: "Schwelle", v: Math.round((s.deal_threshold || 0) * 100) + " %", cls: "card" },
  ];
  document.getElementById("stats").innerHTML = cards
    .map((c) => `<div class="${c.cls || 'card'}" id="${c.id || ''}"><div class="k">${c.k}</div><div class="v">${c.v}</div></div>`).join("");

  const cDeals = document.getElementById("card-deals");
  if (cDeals) {
    cDeals.onclick = () => {
      document.getElementById("dealsOnly").checked = true;
      currentPortalFilter = "";
      loadDeals();
    };
  }
  const cAll = document.getElementById("card-all");
  if (cAll) {
    cAll.onclick = () => {
      document.getElementById("dealsOnly").checked = false;
      currentPortalFilter = "";
      loadDeals();
    };
  }

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
  if (spec.country && spec.country !== "ALL") c.push(`🏳️ ${spec.country}`);
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
  if (spec.battery_from_kwh) c.push(`Akku ≥${spec.battery_from_kwh} kWh`);
  if (spec.unknown_policy === "strict") c.push("nur belegte Werte");
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
  return ["country","make","model","fuel","transmission","body_type","seller","doors","zip_code","radius_km","year_from","year_to",
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
  if (s.country && s.country !== "ALL") params.set("cn", s.country);
  if (s.zip_code) params.set("ambc", s.zip_code);
  if (s.radius_km) params.set("rad", String(s.radius_km));
  if (s.ev_range_from) params.set("re", String(Math.max(50, Math.floor(Number(s.ev_range_from) / 100) * 100)));
  if (s.battery_from_kwh) params.set("bc", String(Math.max(10, Math.floor(Number(s.battery_from_kwh) / 10) * 10)));
  if (s.make || s.model) params.set("q", [s.make, s.model].filter(Boolean).join(" "));
  const EQ_TO_MOBILE = {
    4: "SUNROOF", 5: "MANUAL_CLIMATISATION", 6: "FULL_LEATHER", 11: "FOUR_WHEEL_DRIVE",
    13: "ELECTRIC_WINDOWS", 15: "ALLOY_WHEELS", 16: "ELECTRIC_ADJUSTABLE_SEATS",
    20: "TRAILER_COUPLING", 23: "NAVIGATION_SYSTEM", 27: "ROOF_RAILS",
    30: "AUTOMATIC_CLIMATISATION", 34: "ELECTRIC_HEATED_SEATS", 38: "CRUISE_CONTROL",
    39: "XENON_HEADLIGHTS", 40: "PARKING_ASSISTANTS", 50: "PANORAMIC_GLASS_ROOF",
    52: "AUXILIARY_HEATING", 114: "MULTIFUNCTIONAL_WHEEL", 122: "BLUETOOTH",
    123: "HEAD_UP_DISPLAY", 125: "ISOFIX", 130: "REAR_VIEW_CAM",
    133: "ADAPTIVE_CRUISE_CONTROL", 135: "HEATED_WINDSHIELD", 136: "HEATED_STEERING_WHEEL",
    138: "DAB_RADIO", 139: "ELECTRIC_TAILGATE", 140: "LED_HEADLIGHTS",
    145: "MASSAGE_SEATS", 153: "KEYLESS_ENTRY", 154: "VENTILATED_SEATS",
    155: "SOUND_SYSTEM", 157: "LANE_DEPARTURE_WARNING", 158: "BLIND_SPOT_MONITOR",
    187: "CAMERA_360", 221: "APPLE_CARPLAY", 222: "ANDROID_AUTO",
    223: "WIRELESS_CHARGING", 224: "DIGITAL_COCKPIT", 249: "HEAT_PUMP"
  };
  (s.equipment || []).forEach((eqId) => {
    if (EQ_TO_MOBILE[eqId]) params.append("fe", EQ_TO_MOBILE[eqId]);
  });
  return "https://suchen.mobile.de/fahrzeuge/search.html?" + params.toString();
}

function sohClass(soh) {
  if (soh == null) return "";
  if (soh >= 90) return "soh-good";
  if (soh >= 80) return "soh-mid";
  return "soh-low";
}

// ---------- Deals & Schnellsuche ----------
let currentPortalFilter = "";
let allLoadedDeals = [];
let sortState = { col: "discount", dir: "desc" };

function getCellValue(deal, col) {
  if (col === "price") return deal.price ?? 99999999;
  if (col === "market_price") return deal.market_price ?? 0;
  if (col === "discount") {
    if (deal.is_deal) return 1000 + (deal.discount ?? 0);
    return deal.discount ?? -999;
  }
  if (col === "year") return deal.year ?? 0;
  if (col === "mileage") return deal.mileage ?? 99999999;
  if (col === "battery_kwh") return deal.battery_kwh ?? deal.ev_range_km ?? 0;
  if (col === "first_seen") return deal.first_seen ?? 0;
  if (col === "portal") return (deal.portal || "").toLowerCase();
  if (col === "title") return (deal.title || "").toLowerCase();
  return 0;
}

function sortDealsList(list) {
  const { col, dir } = sortState;
  const mul = dir === "asc" ? 1 : -1;
  return [...list].sort((a, b) => {
    const va = getCellValue(a, col);
    const vb = getCellValue(b, col);
    if (typeof va === "string") return va.localeCompare(vb) * mul;
    return (va - vb) * mul;
  });
}

function updateSortHeaders() {
  document.querySelectorAll("th.sortable").forEach((th) => {
    const col = th.dataset.sort;
    const icon = th.querySelector(".sort-icon");
    if (!icon) return;
    if (col === sortState.col) {
      th.classList.add("active-sort");
      icon.textContent = sortState.dir === "asc" ? "▲" : "▼";
    } else {
      th.classList.remove("active-sort");
      icon.textContent = "⇅";
    }
  });
}

function renderDealsRows(deals) {
  const body = document.getElementById("deals-body");
  if (!deals.length) {
    body.innerHTML = `<tr><td colspan="11" class="empty">Keine Inserate entsprechen den aktuellen Filterkriterien.</td></tr>`;
    return;
  }
  body.innerHTML = deals.map((d) => {
    const discPct = d.discount == null ? null : Math.abs(Math.round(d.discount * 100));
    const discText = d.discount == null ? "" : `${d.discount < 0 ? "+" : "-"}${discPct} %`;
    let mark = "", rowcls = "", discBadge = "";

    if (d.is_deal) {
      mark = `<span class="mark deal" title="Schnäppchen (≥ 15 % unter Markt)">★</span>`;
      rowcls = "row-deal";
      discBadge = `<span class="deal-badge" title="Schnäppchen: ${discPct} % unter Marktwert!">🔥 -${discPct} %</span>`;
    } else if (d.is_suspicious) {
      mark = `<span class="mark susp" title="${escapeHtml(d.reasons || "verdächtig")}">⚠</span>`;
      rowcls = "row-susp";
      discBadge = `<span class="disc-normal ${discountClass(d.discount)}">${discText}</span>`;
    } else {
      discBadge = `<span class="disc-normal ${discountClass(d.discount)}">${discText}</span>`;
    }

    const pcls = "portal-" + (d.portal || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    const evidence = d.field_evidence || {};
    const sourceTitle = (field, fallback) => {
      const item = evidence[field];
      if (!item) return fallback;
      const confidence = item.confidence != null ? ` · ${Math.round(item.confidence * 100)} % sicher` : "";
      return `${fallback} · Quelle: ${item.source || "unbekannt"}${confidence}${item.evidence ? ` · ${item.evidence}` : ""}`;
    };
    const sohBadge = d.battery_soh != null 
      ? `<span class="soh-badge ${sohClass(d.battery_soh)}" title="${escapeHtml(sourceTitle("battery_soh", "Batteriezustand (State of Health)"))}">🔋 ${d.battery_soh} % SoH</span>`
      : "";
    const batteryText = d.battery_net_kwh != null && d.battery_gross_kwh != null && d.battery_net_kwh !== d.battery_gross_kwh
      ? `${d.battery_net_kwh} netto / ${d.battery_gross_kwh} brutto`
      : (d.battery_gross_kwh ?? d.battery_kwh);
    const battInfo = batteryText != null
      ? `<span class="batt-badge" title="${escapeHtml(sourceTitle("battery_kwh", "Akku-Kapazität"))}">⚡ ${batteryText} kWh</span>`
      : "";
    const rangeInfo = d.ev_range_km != null
      ? `<span class="range-badge" title="Reichweite">🌐 ~${d.ev_range_km} km</span>`
      : "";
    const batteryCell = (battInfo || sohBadge || rangeInfo)
      ? `<div class="battery-cell">${battInfo}${sohBadge}${rangeInfo}</div>`
      : `<span class="muted">–</span>`;

    const locBadge = (d.distance_km != null)
      ? `<span class="badge-loc" title="${escapeHtml(d.location || '')}">📍 ${d.distance_km} km</span>`
      : "";
    const warrantyBadge = d.warranty
      ? `<span class="badge-warranty" title="${escapeHtml(d.warranty)}">🛡️ ${escapeHtml(d.warranty)}</span>`
      : "";
    const subInfo = (locBadge || warrantyBadge)
      ? `<div class="sub-info">${locBadge}${warrantyBadge}</div>`
      : "";

    const staleBadge = d.is_stale
      ? `<span class="data-badge stale" title="Das Portal konnte diesen Treffer im letzten Lauf nicht bestätigen">veraltet</span>`
      : "";

    return `<tr class="${rowcls}${d.is_stale ? " row-stale" : ""}">
      <td class="markcell">${mark}</td>
      <td><span class="portal-badge ${pcls}">${escapeHtml(d.portal || "")}</span></td>
      <td class="title">
        <div class="t-main">${escapeHtml(d.title || "")}</div>
        ${subInfo}<div class="data-quality">${staleBadge}</div>
        ${d.is_suspicious ? `<div class="reason">${escapeHtml(d.reasons || "")}</div>` : ""}
      </td>
      <td class="battery-col">${batteryCell}</td>
      <td class="num font-bold">${euro(d.price)}</td>
      <td class="num muted">${euro(d.market_price)}</td>
      <td class="num discount-cell">${discBadge}</td>
      <td class="num">${d.year || "–"}</td>
      <td class="num">${km(d.mileage)}</td>
      <td>${timeAgo(d.first_seen)}</td>
      <td><a class="link" href="${d.url}" target="_blank" rel="noopener">öffnen ↗</a></td>
    </tr>`;
  }).join("");
}

function applyQuickFilters() {
  const qText = (document.getElementById("quickSearch")?.value || "").trim().toLowerCase();
  const maxPrice = Number(document.getElementById("qf-price-max")?.value) || null;
  const maxKm = Number(document.getElementById("qf-km-max")?.value) || null;
  const minRange = Number(document.getElementById("qf-range-min")?.value) || null;
  const minSoh = Number(document.getElementById("qf-soh-min")?.value) || null;

  const filtered = allLoadedDeals.filter((d) => {
    if (qText) {
      const hay = `${d.title || ""} ${d.portal || ""} ${d.body || ""} ${d.location || ""} ${d.location_city || ""} ${d.warranty || ""} ${d.reasons || ""}`.toLowerCase();
      if (!hay.includes(qText)) return false;
    }
    if (maxPrice != null && d.price != null && d.price > maxPrice) return false;
    if (maxKm != null && d.mileage != null && d.mileage > maxKm) return false;
    if (minRange != null && (d.ev_range_km == null || d.ev_range_km < minRange)) return false;
    if (minSoh != null && (d.battery_soh == null || d.battery_soh < minSoh)) return false;
    return true;
  });

  const sorted = sortDealsList(filtered);
  renderDealsRows(sorted);
  updateSortHeaders();

  const badge = document.getElementById("quickFilterCount");
  if (badge) {
    if (filtered.length === allLoadedDeals.length) {
      badge.textContent = `Zeige alle ${allLoadedDeals.length} Treffer`;
      badge.classList.remove("filtered");
    } else {
      badge.textContent = `${filtered.length} von ${allLoadedDeals.length} Treffern`;
      badge.classList.add("filtered");
    }
  }
}

async function loadDeals() {
  const sel = document.getElementById("searchFilter");
  const dealsOnly = document.getElementById("dealsOnly").checked;
  const params = [];
  if (sel.value) params.push(`search=${sel.value}`);
  if (dealsOnly) params.push("deals_only=true");
  // Auf dem Portal verschwundene Inserate sind standardmäßig ausgeblendet.
  const showStale = document.getElementById("showStale")?.checked;
  if (showStale) params.push("include_stale=true");
  if (currentPortalFilter) params.push(`portal=${encodeURIComponent(currentPortalFilter)}`);
  const data = await getJSON(`${API}/deals${params.length ? "?" + params.join("&") : ""}`);
  
  allLoadedDeals = data.deals || [];
  const apiDealCount = data.total_deals ?? allLoadedDeals.filter(x => x.is_deal).length;
  renderPortalFilters(data.portal_counts || {}, apiDealCount);

  // Kachel synchron halten
  const cDealsV = document.querySelector("#card-deals .v");
  if (cDealsV) cDealsV.textContent = apiDealCount;
  const cAllV = document.querySelector("#card-all .v");
  if (cAllV) cAllV.textContent = data.count;

  applyQuickFilters();

  document.getElementById("footer-info").textContent =
    `${data.count} Treffer im Speicher${data.stale_hidden ? ` · ${data.stale_hidden} veraltete ausgeblendet` : ""}${data.stale_count ? ` · ${data.stale_count} veraltet` : ""}${dealsOnly ? " (nur Schnäppchen)" : ""}${currentPortalFilter ? ` · Filter: ${currentPortalFilter}` : ""} · Auto-Aktualisierung alle 20 s`;
}

function renderPortalFilters(counts, dealCount) {
  const box = document.getElementById("portal-filters");
  if (!box) return;
  // Die Konfiguration liefert technische Schlüssel (z. B. "autouncle"),
  // die Trefferliste dagegen die Anzeigenamen. Beide Quellen zusammenführen,
  // damit aktive Portale auch bei 0 Treffern sichtbar bleiben.
  const activePortals = (statusCache?.portals_active || [])
    .map((p) => PORTAL_LABELS[p] || p)
    .filter(Boolean);
  const configured = activePortals.length
    ? activePortals
    : Object.values(PORTAL_LABELS);
  const portals = [...new Set([
    ...configured,
    ...Object.keys(counts || {}),
  ])];
  let totalAll = 0;
  for (const k in counts) totalAll += counts[k];

  const dealsOnly = document.getElementById("dealsOnly").checked;
  const dCount = dealCount ?? statusCache?.total_deals ?? 0;

  const items = [
    { id: "DEALS_ONLY", label: "🔥 Nur Schnäppchen", count: dCount, isDeal: true },
    { id: "", label: "Alle Portale", count: totalAll }
  ];
  for (const p of portals) {
    const healthRows = (statusCache?.portal_health || []).filter((h) => h.portal === p);
    const unhealthy = healthRows.some((h) => h.status && h.status !== "ok");
    const healthTitle = unhealthy
      ? healthRows.filter((h) => h.status !== "ok").map((h) => `${h.search_name}: ${h.status}${h.error ? ` – ${h.error}` : ""}`).join(" | ")
      : "Letzter Abruf erfolgreich";
    items.push({ id: p, label: p, count: counts[p] || 0, unhealthy, healthTitle });
  }

  box.innerHTML = items.map((it) => {
    let active = "";
    if (it.isDeal) {
      active = dealsOnly ? "active deal-active" : "";
    } else {
      active = (!dealsOnly && currentPortalFilter === it.id) ? "active" : "";
    }
    const extraCls = it.isDeal ? "pill-deals" : "";
    return `<button type="button" class="portal-pill ${extraCls} ${it.unhealthy ? "health-bad" : ""} ${active}" data-portal="${escapeHtml(it.id)}" title="${escapeHtml(it.healthTitle || "")}">
      <span class="p-name">${escapeHtml(it.label)}</span>
      <span class="p-count">${it.count}</span>
    </button>`;
  }).join("");

  box.querySelectorAll(".portal-pill").forEach((btn) => {
    btn.onclick = () => {
      const pid = btn.dataset.portal;
      if (pid === "DEALS_ONLY") {
        document.getElementById("dealsOnly").checked = true;
        currentPortalFilter = "";
      } else {
        document.getElementById("dealsOnly").checked = false;
        currentPortalFilter = pid;
      }
      loadDeals();
    };
  });
}

// ---------- Formular ----------
const NUMS = ["year_from","year_to","price_from","price_to","mileage_from","mileage_to","radius_km","power_from","power_to","ev_range_from","battery_from_kwh"];
const SELS = ["make","model","country","fuel","transmission","body_type","seller","doors","emission_class","drivetrain","unknown_policy"];

function openForm(spec) {
  document.getElementById("form-error").textContent = "";
  document.getElementById("modal-title").textContent = spec ? "Suche bearbeiten" : "Neue Suche";
  document.getElementById("f-id").value = spec ? spec.id : "";
  document.getElementById("f-name").value = spec ? spec.name : "";
  document.getElementById("f-active").checked = spec ? !!spec.active : true;
  document.getElementById("f-include_damaged").checked = spec ? !!spec.include_damaged : false;
  document.getElementById("f-zip_code").value = (spec && spec.zip_code) || "";
  document.getElementById("f-country").value = (spec && spec.country) || "DE";
  SELS.forEach((k) => { 
    const el = document.getElementById("f-" + k);
    if (el) el.value = (spec && spec[k]) || (k === "country" ? "DE" : k === "unknown_policy" ? "tolerant" : "");
  });
  NUMS.forEach((k) => { 
    const el = document.getElementById("f-" + k);
    if (el) el.value = (spec && spec[k] != null) ? spec[k] : ""; 
  });
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
  const val = (id) => (document.getElementById(id)?.value || "").trim();
  const num = (id) => { const v = val(id); return v === "" ? null : Number(v); };
  const spec = {
    id: val("f-id"),
    name: val("f-name"),
    active: document.getElementById("f-active").checked,
    make: val("f-make"), model: val("f-model"),
    exclude_makes: val("f-exclude_makes"), exclude_models: val("f-exclude_models"),
    country: val("f-country") || "DE",
    fuel: val("f-fuel"), transmission: val("f-transmission"),
    body_type: val("f-body_type"), seller: val("f-seller"), doors: val("f-doors"),
    zip_code: val("f-zip_code"),
    emission_class: val("f-emission_class"), drivetrain: val("f-drivetrain"),
    unknown_policy: val("f-unknown_policy") || "tolerant",
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
document.getElementById("new-search").addEventListener("click", () => openForm(null));
document.getElementById("modal-close").addEventListener("click", closeForm);
document.getElementById("modal-cancel").addEventListener("click", closeForm);
document.getElementById("search-form").addEventListener("submit", submitForm);
document.getElementById("equip-search").addEventListener("input", (e) => filterEquip(e.target.value));
document.getElementById("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeForm(); });

// Schnellsuche & Live-Filter Events
document.getElementById("quickSearch").addEventListener("input", applyQuickFilters);
document.getElementById("quickSearchClear").addEventListener("click", () => {
  document.getElementById("quickSearch").value = "";
  applyQuickFilters();
});
["qf-price-max", "qf-km-max", "qf-range-min", "qf-soh-min"].forEach((id) => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", applyQuickFilters);
});

// Sortier-Header Klicks
document.querySelectorAll("th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const col = th.dataset.sort;
    if (sortState.col === col) {
      sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
    } else {
      sortState.col = col;
      sortState.dir = (col === "price" || col === "mileage") ? "asc" : "desc";
    }
    applyQuickFilters();
  });
});

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
document.getElementById("showStale")?.addEventListener("change", loadDeals);
document.getElementById("clear").addEventListener("click", async () => {
  if (!confirm("Trefferliste wirklich leeren? (Duplikat-Filter bleibt erhalten)")) return;
  await fetch(`${API}/deals`, { method: "DELETE" });
  refresh();
});

async function refresh() {
  try {
    await loadStatus();
    await loadDeals();
  } catch (e) { console.error(e); }
}
function poll() {
  refresh().then(() => { if (statusCache && statusCache.running) setTimeout(poll, 2000); });
}

(async function init() {
  try { await loadMeta(); } catch (e) { console.error(e); }
  await refresh();
  setInterval(refresh, 20000);
})();
