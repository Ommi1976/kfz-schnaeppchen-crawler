/* Independent account UI. Relative API paths also work behind HA Ingress. */
(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const panel = byId("accounts-panel");
  const dialog = byId("accounts-session");
  const screen = byId("accounts-screen");
  const textInput = byId("accounts-text");
  const tokenInput = byId("accounts-token");
  const portals = [
    { key: "mobile_de", label: "mobile.de", monogram: "m" },
    { key: "autoscout24", label: "AutoScout24", monogram: "24" },
    { key: "kleinanzeigen", label: "Kleinanzeigen", monogram: "kl" },
    { key: "autouncle", label: "AutoUncle", monogram: "AU" },
  ];
  const authStates = {
    authenticated: ["Angemeldet bestätigt", "good"],
    verified: ["Angemeldet bestätigt", "good"],
    logged_in: ["Angemeldet bestätigt", "good"],
    unauthenticated: ["Nicht angemeldet", "warn"],
    logged_out: ["Nicht angemeldet", "warn"],
    anonymous: ["Nicht angemeldet", "warn"],
    disconnected: ["Nicht verbunden", "neutral"],
    not_connected: ["Nicht verbunden", "neutral"],
    login_required: ["Anmeldung erforderlich", "warn"],
    expired: ["Anmeldung abgelaufen", "warn"],
    challenge: ["Bestätigung erforderlich", "warn"],
    verification_required: ["Bestätigung erforderlich", "warn"],
    captcha: ["Captcha erforderlich", "warn"],
    blocked: ["Zugriff blockiert", "bad"],
    error: ["Prüfung fehlgeschlagen", "bad"],
    unknown: ["Anmeldung ungeprüft", "neutral"],
    unverified: ["Anmeldung ungeprüft", "neutral"],
    checking: ["Wird geprüft", "neutral"],
    unsupported: ["Nicht verfügbar", "neutral"],
  };
  const searchStates = {
    ok: ["Erfolgreich", "good"],
    success: ["Erfolgreich", "good"],
    running: ["Suche läuft", "neutral"],
    pending: ["Suche ausstehend", "neutral"],
    idle: ["Bereit", "neutral"],
    partial: ["Nur teilweise erfolgreich", "warn"],
    blocked: ["Suche blockiert", "bad"],
    deferred: ["Suche zurückgestellt", "warn"],
    cooldown: ["Wartezeit aktiv", "warn"],
    error: ["Suche fehlgeschlagen", "bad"],
    failed: ["Suche fehlgeschlagen", "bad"],
    disabled: ["Suche deaktiviert", "neutral"],
    never: ["Noch keine Suche", "neutral"],
    unknown: ["Noch kein Suchstatus", "neutral"],
    untested: ["Noch keine Suche bestätigt", "neutral"],
    incremental: ["Aktuelle Angebote geprüft", "good"],
  };
  const cards = new Map();
  const requests = new Set();
  let accessToken = ""; // Never persisted, placed in URLs, or logged.
  let accountsBusy = false;
  let current = null;
  let pageLeaving = false;

  function safeText(value, extraSecrets = []) {
    let text = typeof value === "string" ? value : "";
    for (const secret of [accessToken, tokenInput.value, ...extraSecrets]) {
      if (secret) text = text.split(secret).join("[ausgeblendet]");
    }
    return text.replace(/(?:bearer\s+|(?:x-kfz-token|token|password|passwort)\s*[:=]\s*)[^\s,;]+/gi, "[ausgeblendet]")
      .replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 260);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function notice(id, message = "", tone = "neutral") {
    const node = byId(id);
    node.textContent = safeText(message);
    node.dataset.tone = tone;
    node.hidden = !node.textContent;
  }

  function timestamp(value) {
    if (value === null || value === undefined || value === "") return null;
    const numeric = typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value));
    const time = numeric ? Number(value) * (Number(value) < 1e12 ? 1000 : 1) : Date.parse(value);
    return Number.isFinite(time) && time > 0 && time <= 8.64e15 ? time : null;
  }

  function dateLabel(value, fallback = "Noch nicht bestätigt") {
    const time = timestamp(value);
    return time === null ? fallback : new Date(time).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
  }

  function badge(node, value, states, fallback) {
    const known = Object.hasOwn(states, value) ? states[value] : null;
    node.textContent = known ? known[0] : fallback;
    node.dataset.tone = known ? known[1] : "neutral";
  }

  class AccountError extends Error {
    constructor(message, status = 0) {
      super(message);
      this.status = status;
    }
  }

  async function request(path = "", { body, timeout = 15000, privateInput = false, keepalive = false } = {}) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    const sentToken = accessToken;
    const headers = { Accept: "application/json" };
    if (sentToken) headers["X-KFZ-Token"] = sentToken;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    requests.add(controller);
    try {
      const response = await fetch(`api/accounts${path}`, {
        method: body === undefined ? "GET" : "POST",
        credentials: "same-origin", cache: "no-store", redirect: "error",
        headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal, keepalive,
      });
      const data = response.status === 204 ? {} : await response.json().catch(() => null);
      if (!response.ok) {
        const reasons = {
          401: "Zugriff nicht autorisiert. Bitte Zugriffstoken prüfen.",
          403: "Zugriff verweigert. Bitte Zugriffsberechtigung prüfen.",
          404: "Konto-API oder Sitzung nicht verfügbar.",
          409: "Portal ist gerade belegt. Bitte erneut versuchen.",
          410: "Die interaktive Sitzung ist abgelaufen.",
          429: "Zu viele Anfragen. Bitte kurz warten.",
        };
        // Input errors may echo credentials: never display their response body.
        const detail = privateInput ? "" : safeText(data?.detail?.message || data?.detail || data?.message, [sentToken]);
        const reason = reasons[response.status] || `Anfrage fehlgeschlagen (HTTP ${response.status}).`;
        throw new AccountError(detail ? `${reason} ${detail}` : reason, response.status);
      }
      if (!data || typeof data !== "object" || Array.isArray(data)) {
        throw new AccountError("Die Konto-API hat keine gültige Antwort geliefert.");
      }
      return data;
    } catch (error) {
      if (error instanceof AccountError) throw error;
      if (controller.signal.aborted) {
        throw new AccountError("Zeitlimit erreicht. Der Serverstatus ist unklar; bitte vor einem erneuten Senden das Bild aktualisieren.");
      }
      throw new AccountError("Verbindung zur Konto-API fehlgeschlagen. Bitte erneut versuchen.");
    } finally {
      window.clearTimeout(timer);
      requests.delete(controller);
    }
  }

  function portalPath(key, suffix) {
    return `/${encodeURIComponent(key)}/${suffix}`;
  }

  function createCards() {
    for (const portal of portals) {
      const article = element("article", "accounts-card");
      article.dataset.portal = portal.key;
      const heading = element("div", "accounts-card-heading");
      const mark = element("span", "accounts-monogram", portal.monogram);
      mark.setAttribute("aria-hidden", "true");
      const titleGroup = element("div");
      const title = element("h3", "", portal.label);
      const enabled = element("div", "accounts-enabled", "Konfiguration wird geladen");
      titleGroup.append(title, enabled);
      heading.append(mark, titleGroup);
      const details = element("dl");
      const fields = {};
      for (const [name, label] of [
        ["auth", "Kontostatus"], ["search", "Suchstatus"],
        ["success", "Letzte erfolgreiche Suche"], ["checked", "Konto zuletzt geprüft"],
        ["authenticated", "Anmeldung zuletzt bestätigt"], ["blocked", "Suchpause bis"],
      ]) {
        const term = element("dt", "", label);
        const value = element("dd");
        const content = element("span", ["auth", "search"].includes(name) ? "accounts-badge" : "", "Noch nicht geladen");
        value.append(content);
        details.append(term, value);
        fields[name] = { term, value, content };
      }
      const message = element("p", "accounts-card-message");
      const actions = element("div", "accounts-card-actions");
      const connect = element("button", "btn primary small", "Verbinden");
      const check = element("button", "btn small", "Prüfen");
      const disconnect = element("button", "btn danger small", "Trennen");
      connect.type = check.type = disconnect.type = "button";
      connect.addEventListener("click", () => openSession(portal.key));
      disconnect.addEventListener("click", () => disconnectPortal(portal.key));
      check.addEventListener("click", () => checkPortal(portal.key));
      actions.append(connect, check, disconnect);
      article.append(heading, details, message, actions);
      byId("accounts-grid").append(article);
      cards.set(portal.key, { portal, article, title, enabled, fields, message, connect, check, disconnect, data: null });
    }
    renderCards();
  }

  function renderCards() {
    for (const card of cards.values()) {
      const data = card.data;
      const label = safeText(data?.label) || card.portal.label;
      card.title.textContent = label;
      card.enabled.textContent = !data ? "Status nicht verfügbar" : data.enabled === true ? "Für Suche aktiviert" : data.enabled === false ? "Suche deaktiviert" : "Suchkonfiguration unbekannt";
      badge(card.fields.auth.content, data?.auth_state, authStates, "Anmeldung ungeprüft");
      badge(card.fields.search.content, data?.search_status, searchStates, "Noch kein Suchstatus");
      card.fields.success.content.textContent = dateLabel(data?.last_search_success, "Noch kein Erfolg gemeldet");
      card.fields.checked.content.textContent = dateLabel(data?.checked_at, "Noch nicht geprüft");
      card.fields.authenticated.content.textContent = dateLabel(data?.last_authenticated_at);
      card.fields.blocked.content.textContent = dateLabel(data?.blocked_until, "Keine gemeldete Suchpause");
      card.fields.blocked.term.hidden = card.fields.blocked.value.hidden = timestamp(data?.blocked_until) === null;
      card.message.textContent = safeText(data?.message) || (data?.session_active ? "Eine interaktive Browsersitzung ist geöffnet." : "Anmeldung direkt im Portal durchführen.");
      card.connect.textContent = data?.session_active ? "Sitzung öffnen" : "Verbinden";
      card.connect.setAttribute("aria-label", `${label}: ${card.connect.textContent}`);
      card.disconnect.setAttribute("aria-label", `${label}: lokale Kontozuordnung trennen`);
      card.connect.disabled = accountsBusy || !!current || !data;
      card.disconnect.disabled = card.check.disabled = accountsBusy || !!current || !data?.connected;
    }
    byId("accounts-refresh").disabled = accountsBusy || !!current;
    for (const control of byId("accounts-token-form").elements) control.disabled = accountsBusy || !!current;
    byId("accounts-grid").setAttribute("aria-busy", String(accountsBusy));
  }

  async function loadAccounts({ clearNotice = true } = {}) {
    if (accountsBusy || pageLeaving) return;
    accountsBusy = true;
    renderCards();
    if (clearNotice) notice("accounts-notice");
    try {
      const data = await request();
      if (!Array.isArray(data.portals)) throw new AccountError("Die Kontoübersicht enthält keine gültige Portalliste.");
      for (const [key, card] of cards) {
        card.data = data.portals.find((entry) => entry && entry.key === key) || null;
      }
      notice("accounts-access-note", data.access?.message);
      byId("accounts-updated").textContent = `Stand: ${dateLabel(Date.now())}`;
    } catch (error) {
      notice("accounts-notice", error.message, "bad");
      byId("accounts-updated").textContent = "Aktualisierung fehlgeschlagen. Bereits angezeigte Werte können veraltet sein.";
    } finally {
      accountsBusy = false;
      renderCards();
    }
  }

  async function disconnectPortal(key) {
    if (accountsBusy || current) return;
    const card = cards.get(key);
    if (!window.confirm(`${card.title.textContent}: Konto wirklich trennen? Die lokale Kontozuordnung und das gespeicherte Portalprofil werden entfernt. Eine erneute Anmeldung ist anschließend erforderlich.`)) return;
    accountsBusy = true;
    renderCards();
    let succeeded = false;
    try {
      await request(portalPath(key, "disconnect"), { body: {} });
      succeeded = true;
      notice("accounts-notice", `${card.title.textContent}: Lokale Kontozuordnung getrennt.`);
    } catch (error) {
      notice("accounts-notice", error.message, "bad");
    } finally {
      accountsBusy = false;
      renderCards();
    }
    if (succeeded) await loadAccounts({ clearNotice: false });
  }

  async function checkPortal(key) {
    if (accountsBusy || current) return;
    accountsBusy = true;
    renderCards();
    try {
      const data = await request(portalPath(key, "check"), { body: {}, timeout: 45000 });
      cards.get(key).data = { ...cards.get(key).data, ...data };
      notice("accounts-notice", data.message || "Anmeldestatus geprüft.");
    } catch (error) {
      notice("accounts-notice", error.message, "bad");
    } finally {
      accountsBusy = false;
      renderCards();
    }
  }

  function visible(session) {
    return current === session && dialog.open && !document.hidden && !session.closing && !session.closed;
  }

  function stopPoll(session) {
    window.clearTimeout(session.pollTimer);
    session.pollTimer = null;
  }

  function clearImage(session) {
    screen.hidden = true;
    screen.removeAttribute("src");
    if (session.blobUrl) URL.revokeObjectURL(session.blobUrl);
    session.blobUrl = null;
    session.hasImage = false;
  }

  function updateControls(session) {
    if (current !== session) return;
    const live = !!session.id && !session.expired && !session.closing;
    const canInput = live && session.hasImage && !session.paused && !session.busy && !document.hidden;
    textInput.disabled = !canInput;
    byId("accounts-text-send").disabled = !canInput;
    for (const button of byId("accounts-remote-controls").querySelectorAll("button")) button.disabled = !canInput;
    byId("accounts-session-refresh").disabled = !live || session.busy || document.hidden;
    byId("accounts-session-check").disabled = !canInput;
    byId("accounts-session-close").disabled = session.closing;
    screen.setAttribute("aria-disabled", String(!canInput));
    byId("accounts-screen-wrap").dataset.stale = String(session.paused && session.hasImage);
    byId("accounts-screen-wrap").setAttribute("aria-busy", String(session.busy));
    byId("accounts-session-work").textContent = session.closing ? "Sitzung wird geschlossen…" : session.busy ? "Portal wird aktualisiert…" : "";
  }

  function expire(session) {
    if (current !== session || session.closed || session.closing) return;
    session.expired = true;
    session.paused = true;
    stopPoll(session);
    window.clearTimeout(session.expiryTimer);
    textInput.value = "";
    clearImage(session);
    byId("accounts-screen-placeholder").textContent = "Die interaktive Sitzung ist abgelaufen.";
    byId("accounts-screen-placeholder").hidden = false;
    byId("accounts-session-expiry").textContent = "Sitzung abgelaufen";
    notice("accounts-session-notice", "Sitzung abgelaufen. Dialog schließen und das Portal erneut verbinden. Das gespeicherte Profil bleibt erhalten.", "bad");
    updateControls(session);
  }

  function setExpiry(session, value) {
    const deadline = timestamp(value);
    if (deadline === null) throw new AccountError("Die Sitzung enthält keine gültige Ablaufzeit. Bitte schließen und erneut verbinden.");
    session.expiresAt = deadline;
    window.clearTimeout(session.expiryTimer);
    byId("accounts-session-expiry").textContent = `Sitzung gültig bis ${dateLabel(deadline)}`;
    if (deadline <= Date.now()) expire(session);
    else session.expiryTimer = window.setTimeout(() => {
      if (Date.now() >= session.expiresAt) expire(session);
      else setExpiry(session, session.expiresAt);
    }, Math.min(deadline - Date.now(), 2147483647));
  }

  function schedulePoll(session) {
    stopPoll(session);
    if (!visible(session) || !session.id || session.expired || session.paused || session.busy) return;
    session.pollTimer = window.setTimeout(() => {
      if (visible(session)) enqueue(session, () => refreshImage(session));
    }, 2000);
  }

  function handleSessionError(session, error) {
    if (current !== session || session.closing || session.closed) return;
    if (session.id && [404, 410].includes(error.status)) {
      expire(session);
      return;
    }
    session.paused = true;
    stopPoll(session);
    textInput.value = "";
    notice("accounts-session-notice", `${error.message} ${session.id ? "Bild aktualisieren, um fortzufahren." : "Dialog schließen und erneut verbinden."}`, "bad");
  }

  // All interactive operations, including refresh and close, share one queue.
  function enqueue(session, operation) {
    if (!visible(session) || session.expired || session.busy) return Promise.resolve();
    stopPoll(session);
    session.busy = true;
    updateControls(session);
    session.queue = session.queue.then(async () => {
      if (!visible(session) || session.expired) return;
      if (session.expiresAt && session.expiresAt <= Date.now()) { expire(session); return; }
      await operation();
    }).catch((error) => handleSessionError(session, error)).finally(() => {
      session.busy = false;
      updateControls(session);
      schedulePoll(session);
    });
    return session.queue;
  }

  async function refreshImage(session) {
    if (!visible(session) || !session.id || session.expired) return;
    const data = await request(portalPath(session.key, `session?session_id=${encodeURIComponent(session.id)}`));
    if (!visible(session) || session.expired) return;
    if (data.expires_at !== undefined) setExpiry(session, data.expires_at);
    if (session.expired) return;
    badge(byId("accounts-session-auth"), data.auth_state, authStates, "Anmeldung ungeprüft");
    byId("accounts-session-host").textContent = safeText(data.host) || "Nicht gemeldet";
    const width = Number(data.width);
    const height = Number(data.height);
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width > 10000 || height > 10000 || typeof data.image !== "string" || data.image.length > 12000000) {
      throw new AccountError("Das Portalbild ist ungültig. Bitte erneut aktualisieren.");
    }
    let bytes;
    try {
      const base64 = data.image.replace(/^data:image\/jpeg;base64,/, "");
      bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
      if (bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new Error();
    } catch {
      throw new AccountError("Das Portalbild konnte nicht gelesen werden.");
    }
    const blobUrl = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
    try {
      const preview = new Image();
      preview.src = blobUrl;
      await preview.decode();
      if (preview.naturalWidth !== width || preview.naturalHeight !== height) throw new Error();
    } catch {
      URL.revokeObjectURL(blobUrl);
      throw new AccountError("Das Portalbild konnte nicht angezeigt werden.");
    }
    if (!visible(session) || session.expired) { URL.revokeObjectURL(blobUrl); return; }
    const oldUrl = session.blobUrl;
    session.blobUrl = blobUrl;
    session.width = width;
    session.height = height;
    session.hasImage = true;
    session.paused = false;
    screen.width = width;
    screen.height = height;
    screen.src = blobUrl;
    screen.hidden = false;
    if (oldUrl) URL.revokeObjectURL(oldUrl);
    byId("accounts-screen-placeholder").hidden = true;
    notice("accounts-session-notice", data.message);
  }

  function openSession(key) {
    if (current || accountsBusy) return;
    const card = cards.get(key);
    const session = {
      key, id: null, expiresAt: null, pollTimer: null, expiryTimer: null,
      blobUrl: null, hasImage: false, busy: false, paused: false,
      expired: false, closing: false, closed: false, queue: Promise.resolve(),
      opener: document.activeElement,
    };
    current = session;
    textInput.value = "";
    notice("accounts-session-notice");
    byId("accounts-session-title").textContent = `${card.title.textContent} verbinden`;
    byId("accounts-session-host").textContent = "Wird ermittelt…";
    byId("accounts-session-expiry").textContent = "Sitzung wird gestartet…";
    badge(byId("accounts-session-auth"), "unknown", authStates, "Anmeldung ungeprüft");
    byId("accounts-screen-placeholder").textContent = "Das Portal wird geöffnet…";
    byId("accounts-screen-placeholder").hidden = false;
    dialog.showModal();
    renderCards();
    enqueue(session, async () => {
      const data = await request(portalPath(key, "connect"), { body: {}, timeout: 45000 });
      if (typeof data.session_id !== "string" || !data.session_id || data.session_id.length > 512) {
        throw new AccountError("Es wurde keine gültige Sitzung zurückgegeben.");
      }
      session.id = data.session_id;
      if (session.closed || pageLeaving) {
        await request(portalPath(key, "close"), { body: { session_id: session.id }, keepalive: true });
        return;
      }
      if (session.closing) return; // close is already queued behind connect.
      setExpiry(session, data.expires_at);
      await refreshImage(session);
    });
  }

  function canSend(session) {
    return session && visible(session) && !!session.id && session.hasImage && !session.busy && !session.paused && !session.expired;
  }

  function sendInput(payload) {
    const session = current;
    if (!canSend(session)) return;
    enqueue(session, async () => {
      try {
        await request(portalPath(session.key, "input"), {
          body: { session_id: session.id, ...payload }, privateInput: true,
        });
      } finally {
        if (Object.hasOwn(payload, "text")) payload.text = "";
      }
      await refreshImage(session);
    });
  }

  function finishSession(session) {
    stopPoll(session);
    window.clearTimeout(session.expiryTimer);
    session.closed = true;
    clearImage(session);
    textInput.value = "";
    current = null;
    if (dialog.open) dialog.close();
    renderCards();
    if (!pageLeaving) session.opener?.focus();
  }

  function closeSession() {
    const session = current;
    if (!session || session.closing) return;
    session.closing = true;
    stopPoll(session);
    window.clearTimeout(session.expiryTimer);
    textInput.value = "";
    clearImage(session);
    byId("accounts-screen-placeholder").textContent = "Sitzung wird geschlossen…";
    byId("accounts-screen-placeholder").hidden = false;
    updateControls(session);
    session.queue = session.queue.then(async () => {
      try {
        if (session.id) await request(portalPath(session.key, "close"), { body: { session_id: session.id }, timeout: 12000 });
      } catch (error) {
        if (![404, 410].includes(error.status)) {
          notice("accounts-notice", `Dialog geschlossen. Schließen auf dem Server nicht bestätigt; die Sitzung kann bis zur Ablaufzeit offen bleiben. ${error.message}`, "bad");
        }
      } finally {
        if (current === session) {
          finishSession(session);
          await loadAccounts({ clearNotice: false });
        }
      }
    });
  }

  byId("accounts-toggle").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    byId("accounts-toggle").setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden) {
      byId("accounts-title").focus();
      loadAccounts();
    } else tokenInput.value = "";
  });
  byId("accounts-refresh").addEventListener("click", () => loadAccounts());
  byId("accounts-token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (accountsBusy || current) return;
    accessToken = tokenInput.value.trim();
    tokenInput.value = "";
    byId("accounts-token-state").textContent = accessToken ? "für diese Seite gesetzt" : "optional";
    loadAccounts();
  });
  byId("accounts-token-clear").addEventListener("click", () => {
    if (accountsBusy || current) return;
    accessToken = "";
    tokenInput.value = "";
    byId("accounts-token-state").textContent = "optional";
    loadAccounts();
  });
  byId("accounts-session-close").addEventListener("click", closeSession);
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); closeSession(); });
  dialog.addEventListener("close", () => { if (current && !current.closing) closeSession(); });
  byId("accounts-session-refresh").addEventListener("click", () => {
    if (current) enqueue(current, () => refreshImage(current));
  });
  byId("accounts-session-check").addEventListener("click", () => {
    const session = current;
    if (!canSend(session)) return;
    enqueue(session, async () => {
      const data = await request(portalPath(session.key, "check"), { body: { session_id: session.id } });
      if (!visible(session) || session.expired) return;
      const card = cards.get(session.key);
      card.data = { ...card.data, ...data, key: session.key };
      renderCards();
      await refreshImage(session);
      if (!visible(session) || session.expired) return;
      badge(byId("accounts-session-auth"), data.auth_state, authStates, "Anmeldung ungeprüft");
      notice("accounts-session-notice", safeText(data.message) || "Aktuelle Portalseite geprüft. Das Ergebnis steht unter Kontostatus.");
    });
  });
  byId("accounts-text-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = textInput.value;
    textInput.value = ""; // Clear before any asynchronous work, even on failure.
    if (text && canSend(current)) sendInput({ action: "text", text });
  });
  byId("accounts-remote-controls").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.dataset.accountsKey) sendInput({ action: "key", key: button.dataset.accountsKey });
    else if (button.dataset.accountsScroll) sendInput({ action: "scroll", delta: Number(button.dataset.accountsScroll) });
  });
  screen.addEventListener("click", (event) => {
    const session = current;
    if (!canSend(session)) return;
    const rect = screen.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = Math.max(0, Math.min(session.width - 1, Math.floor((event.clientX - rect.left) * session.width / rect.width)));
    const y = Math.max(0, Math.min(session.height - 1, Math.floor((event.clientY - rect.top) * session.height / rect.height)));
    sendInput({ action: "click", x, y });
  });
  // Tab keeps its native dialog focus behavior; remote Tab has its own button.
  screen.addEventListener("keydown", (event) => {
    if (["Enter", "Backspace", "ArrowLeft", "ArrowUp", "ArrowDown", "ArrowRight"].includes(event.key) && !event.ctrlKey && !event.altKey && !event.metaKey) {
      event.preventDefault();
      if (!event.repeat) sendInput({ action: "key", key: event.key });
    }
  });
  document.addEventListener("visibilitychange", () => {
    const session = current;
    if (!session) return;
    stopPoll(session);
    if (document.hidden) {
      textInput.value = "";
      tokenInput.value = "";
    } else if (session.expiresAt && session.expiresAt <= Date.now()) expire(session);
    else if (!session.paused) enqueue(session, () => refreshImage(session));
    updateControls(session);
  });
  window.addEventListener("pagehide", () => {
    pageLeaving = true;
    const session = current;
    for (const controller of requests) controller.abort();
    if (session) {
      if (session.id) request(portalPath(session.key, "close"), { body: { session_id: session.id }, keepalive: true }).catch(() => {});
      finishSession(session);
    }
    accessToken = "";
    tokenInput.value = textInput.value = "";
    byId("accounts-token-state").textContent = "optional";
  });
  window.addEventListener("pageshow", () => { pageLeaving = false; });

  createCards();
})();
