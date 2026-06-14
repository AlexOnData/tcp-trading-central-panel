/*
 * TCP — Trading Central Panel — Static Web App client script.
 *
 * Vanilla JS, no framework. Wraps the chat UI in an IIFE module so nothing
 * leaks to `window`. Runs on `DOMContentLoaded`.
 *
 * Endpoints (proxied by SWA `linked backend`; browser only ever hits the SWA
 * origin):
 *   GET  /.auth/me                       — SWA identity endpoint.
 *   GET  /.auth/login/aad                — SWA AAD sign-in redirect.
 *   GET  /.auth/logout                   — SWA sign-out.
 *   GET  /api/ping                       — anonymous; wakes the SQL serverless DB.
 *   POST /api/ask                        — authenticated; the AI-assistant call.
 *
 * Locale formatters use `ro-RO` per business requirements (NFR-LOC-01..05).
 *
 * `/api/ask` returns a unified envelope (Etapa-5 holistic review CR-02):
 *
 *   { status, answer, rows, row_count, source, latency_ms,
 *     anthropic, objects_referenced, error }
 *
 * `status` is the discriminator the UI branches on; the HTTP status code
 * still maps for caches/proxies but the body shape is constant. See
 * `swa/README.md` "Backend contract" for the full schema.
 */

(function () {
  "use strict";

  /* ---------- Locale formatters (ro-RO) ---------- */

  /** EUR formatter: `12.345,67 €` per NFR-LOC-01. */
  const eurFormatter = new Intl.NumberFormat("ro-RO", {
    style: "currency",
    currency: "EUR",
    currencyDisplay: "symbol",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  /** Plain number formatter with Romanian separators. */
  const numberFormatter = new Intl.NumberFormat("ro-RO", {
    maximumFractionDigits: 4,
  });

  /** Integer formatter for counts / row totals. */
  const integerFormatter = new Intl.NumberFormat("ro-RO", {
    maximumFractionDigits: 0,
  });

  /** Date formatter: `dd.MM.yyyy` per NFR-LOC-02. */
  const dateFormatter = new Intl.DateTimeFormat("ro-RO", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });

  /** Datetime formatter: `dd.MM.yyyy HH:mm`. */
  const dateTimeFormatter = new Intl.DateTimeFormat("ro-RO", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  /* ---------- Debug-mode toggle ---------- */

  /**
   * Enable `?debug=1` (or `localStorage.tcpDebug = '1'`) to surface the
   * Anthropic token-usage footer beneath each assistant turn. Documented in
   * `swa/README.md` "Backend contract".
   */
  function isDebugMode() {
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get("debug") === "1") return true;
      return window.localStorage.getItem("tcpDebug") === "1";
    } catch (err) {
      return false;
    }
  }

  /* ---------- DOM references (resolved on DOMContentLoaded) ---------- */
  let elements = null;

  function resolveElements() {
    elements = {
      userName: document.getElementById("user-name"),
      transcript: document.getElementById("transcript"),
      form: document.getElementById("ask-form"),
      input: document.getElementById("question"),
      askButton: document.getElementById("ask-button"),
      wakeButton: document.getElementById("wake-button"),
      toast: document.getElementById("toast"),
      toastMessage: document.getElementById("toast-message"),
      toastClose: document.getElementById("toast-close"),
      suggestedList: document.getElementById("suggested-list"),
    };
  }

  /* ---------- Cell rendering helpers ---------- */

  /**
   * Detect an ISO-8601 date string (YYYY-MM-DD or full timestamp). The backend
   * `v_*` views expose `trade_date_ro` as a SQL DATE — most likely serialised
   * as YYYY-MM-DD by the trigger's JSON encoder.
   */
  function isIsoDateString(value) {
    if (typeof value !== "string") return false;
    return /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$/.test(
      value
    );
  }

  /** Heuristic: column name suggests an EUR amount. */
  function looksLikeEurColumn(name) {
    if (typeof name !== "string") return false;
    const n = name.toLowerCase();
    return (
      n.includes("pnl") ||
      n.includes("eur") ||
      n.includes("capital") ||
      n.endsWith("_amount") ||
      n === "amount" ||
      n.includes("profit") ||
      n.includes("loss")
    );
  }

  /** Format a single cell value for display, honouring the column name. */
  function formatCell(value, columnName) {
    if (value === null || value === undefined) {
      return "—";
    }
    if (typeof value === "boolean") {
      return value ? "Yes" : "No";
    }
    if (typeof value === "number") {
      if (looksLikeEurColumn(columnName)) {
        return eurFormatter.format(value);
      }
      return Number.isInteger(value)
        ? integerFormatter.format(value)
        : numberFormatter.format(value);
    }
    if (isIsoDateString(value)) {
      const parsed = new Date(value);
      if (!Number.isNaN(parsed.getTime())) {
        return value.length <= 10
          ? dateFormatter.format(parsed)
          : dateTimeFormatter.format(parsed);
      }
    }
    return String(value);
  }

  /* ---------- DOM helpers ---------- */

  /** Create an element with optional attributes and text content. */
  function el(tag, attrs, textContent) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const key in attrs) {
        if (Object.prototype.hasOwnProperty.call(attrs, key)) {
          node.setAttribute(key, attrs[key]);
        }
      }
    }
    if (textContent !== undefined && textContent !== null) {
      node.textContent = textContent;
    }
    return node;
  }

  /** Append a user-authored message bubble to the transcript. */
  function appendUserMessage(text) {
    const wrapper = el("div", { class: "message user" });
    wrapper.appendChild(el("p", null, text));
    elements.transcript.appendChild(wrapper);
    scrollTranscriptToEnd();
  }

  /** Append (and return) a transient loading bubble. Caller removes it. */
  function appendLoadingIndicator() {
    const wrapper = el("div", {
      class: "message loading",
      "aria-label": "Waiting for assistant response",
    });
    wrapper.appendChild(el("span", { class: "spinner", "aria-hidden": "true" }));
    wrapper.appendChild(el("span", null, "Thinking…"));
    elements.transcript.appendChild(wrapper);
    scrollTranscriptToEnd();
    return wrapper;
  }

  function scrollTranscriptToEnd() {
    elements.transcript.scrollTop = elements.transcript.scrollHeight;
  }

  /* ---------- Suggested-question binding ---------- */

  /**
   * Wire one suggested-question button: clicking fills the input and submits
   * the form so the question flows through the same code path as a typed one.
   */
  function bindSuggestedQuestion(button) {
    const question = button.getAttribute("data-question");
    if (!question) return;
    button.addEventListener("click", function () {
      elements.input.value = question;
      // Submit via requestSubmit so the submit handler (with validation)
      // runs exactly as if the user clicked "Ask".
      elements.form.requestSubmit();
      elements.input.focus();
    });
  }

  /* ---------- Identity (SWA built-in auth) ---------- */

  /**
   * Read /.auth/me, populate the header user name, and redirect anonymous
   * users to the SWA AAD sign-in endpoint.
   */
  async function loadCurrentUser() {
    try {
      const response = await fetch("/.auth/me", {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error("Auth endpoint returned " + response.status);
      }
      const payload = await response.json();
      const principal = payload && payload.clientPrincipal;
      if (!principal) {
        // Anonymous user — redirect to AAD sign-in. The post_login_redirect_uri
        // returns the user to the current path after authentication.
        const here = window.location.pathname + window.location.search;
        window.location.assign(
          "/.auth/login/aad?post_login_redirect_uri=" + encodeURIComponent(here)
        );
        return null;
      }
      const displayName =
        principal.userDetails ||
        principal.userId ||
        "Authenticated user";
      elements.userName.textContent = displayName;
      elements.userName.setAttribute("title", "Signed in as " + displayName);
      return principal;
    } catch (err) {
      // Don't redirect on a transient network failure; show a toast instead so
      // the user can retry or report the issue.
      console.error("loadCurrentUser failed", err);
      elements.userName.textContent = "Sign-in pending";
      showToast(
        "Could not verify your sign-in. Refresh the page or try again shortly."
      );
      return null;
    }
  }

  /* ---------- Wake-up endpoint ---------- */

  /**
   * Call GET /api/ping to resume the auto-paused SQL serverless database.
   * Renders the resume latency back into the wake-up button label.
   */
  async function wakeDatabase() {
    const originalLabel = elements.wakeButton.textContent;
    elements.wakeButton.disabled = true;
    elements.wakeButton.textContent = "Waking up…";
    try {
      const response = await fetch("/api/ping", {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const payload = await safeReadJson(response);
      if (!response.ok) {
        renderHttpError(response.status, payload);
        elements.wakeButton.textContent = originalLabel;
        return;
      }
      const ms =
        payload && typeof payload.sql_resume_ms === "number"
          ? payload.sql_resume_ms
          : null;
      const status = (payload && payload.status) || "warm";
      if (ms !== null) {
        elements.wakeButton.textContent =
          "Database " + status + " (" + integerFormatter.format(ms) + " ms)";
      } else {
        elements.wakeButton.textContent = "Database " + status;
      }
    } catch (err) {
      console.error("wakeDatabase failed", err);
      showToast("Network error while waking the database. Check your connection.");
      elements.wakeButton.textContent = originalLabel;
    } finally {
      elements.wakeButton.disabled = false;
    }
  }

  /* ---------- Ask endpoint ---------- */

  /**
   * POST a question to /api/ask. SWA `linked backend` injects the
   * `x-ms-client-principal` header server-side; the browser does not.
   */
  async function askQuestion(question) {
    elements.askButton.disabled = true;
    elements.input.disabled = true;
    const loading = appendLoadingIndicator();
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        credentials: "same-origin",
        body: JSON.stringify({ question: question }),
      });

      loading.remove();

      const payload = await safeReadJson(response);
      // Unified envelope: branch on `status`, not on `response.ok`.
      const envelopeStatus = payload && payload.status;
      if (envelopeStatus === "ok") {
        renderAnswer(payload);
        return;
      }
      if (envelopeStatus === "refused" || envelopeStatus === "validation_error") {
        renderModelDecline(payload);
        return;
      }
      renderHttpError(response.status, payload);
    } catch (err) {
      loading.remove();
      console.error("askQuestion failed", err);
      showToast(
        "Network error. Check your connection and try again."
      );
    } finally {
      elements.askButton.disabled = false;
      elements.input.disabled = false;
      elements.input.focus();
    }
  }

  /**
   * Read the response body as JSON when possible, otherwise return null.
   * The unified envelope is always JSON; only a network error or a
   * deployment-time misconfiguration produces a non-JSON body.
   */
  async function safeReadJson(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.indexOf("application/json") === -1) return null;
    try {
      return await response.json();
    } catch (err) {
      return null;
    }
  }

  /* ---------- Answer rendering ---------- */

  /**
   * Render an `/api/ask` `status="ok"` envelope into a bot message bubble.
   */
  function renderAnswer(payload) {
    const wrapper = el("div", { class: "message bot" });

    const answerText =
      (payload && payload.answer) ||
      "The assistant returned no narrative text.";
    wrapper.appendChild(el("p", { class: "answer-text" }, answerText));

    const rows = payload && Array.isArray(payload.rows) ? payload.rows : [];
    const rowCount =
      typeof payload.row_count === "number" ? payload.row_count : rows.length;

    if (rowCount > 0 && rows.length > 0) {
      wrapper.appendChild(buildResultTable(rows));
    }

    wrapper.appendChild(buildAnswerMeta(payload));
    wrapper.appendChild(buildSourceCitation(payload));
    if (isDebugMode()) {
      const usage = payload && payload.anthropic;
      if (usage && typeof usage === "object") {
        wrapper.appendChild(buildClaudeBadge(usage));
      }
    }
    elements.transcript.appendChild(wrapper);
    scrollTranscriptToEnd();
  }

  /**
   * Render a refusal / validation-error envelope as a first-class bot bubble
   * so the Romanian refusal text stays in the transcript (CR-02). A toast is
   * still raised so the user gets a transient alert.
   */
  function renderModelDecline(payload) {
    const wrapper = el("div", { class: "message bot decline" });
    const error = payload && payload.error;
    const message =
      (error && error.message) ||
      "The assistant declined to answer this question.";
    wrapper.appendChild(el("p", { class: "answer-text" }, message));
    wrapper.appendChild(buildAnswerMeta(payload));
    if (isDebugMode()) {
      const usage = payload && payload.anthropic;
      if (usage && typeof usage === "object") {
        wrapper.appendChild(buildClaudeBadge(usage));
      }
    }
    elements.transcript.appendChild(wrapper);
    scrollTranscriptToEnd();
    showToast(message);
  }

  /** Build the "source + latency" footer row. */
  function buildAnswerMeta(payload) {
    const meta = el("div", { class: "answer-meta" });

    const left = el("span", { class: "answer-source" });
    if (payload && payload.source) {
      left.textContent = "Sursă: " + payload.source;
    } else {
      left.textContent = "Sursă: (n/a)";
    }
    meta.appendChild(left);

    const right = el("span", { class: "answer-latency" });
    if (payload && typeof payload.latency_ms === "number") {
      right.textContent = integerFormatter.format(payload.latency_ms) + " ms";
      right.setAttribute(
        "aria-label",
        "Latency " + payload.latency_ms + " milliseconds"
      );
    } else {
      right.textContent = "— ms";
    }
    meta.appendChild(right);

    return meta;
  }

  /**
   * Build the "Sources: v_..., dim_..." citation row using
   * `objects_referenced` (holistic CR-01). Empty when the envelope did not
   * include any sources (e.g., refusal path).
   */
  function buildSourceCitation(payload) {
    const objects =
      payload && Array.isArray(payload.objects_referenced)
        ? payload.objects_referenced
        : [];
    const row = el("div", { class: "answer-sources" });
    if (objects.length === 0) return row;
    const label = el("span", { class: "answer-sources-label" }, "Sources: ");
    row.appendChild(label);
    for (let i = 0; i < objects.length; i += 1) {
      if (i > 0) row.appendChild(document.createTextNode(", "));
      row.appendChild(
        el("code", { class: "answer-source-name" }, objects[i])
      );
    }
    return row;
  }

  /**
   * Build the optional debug-mode footer that surfaces cache-discount
   * telemetry. Only rendered when `isDebugMode()` is true so the production
   * UI stays clean (holistic CR-01).
   */
  function buildClaudeBadge(usage) {
    const badge = el("div", {
      class: "claude-badge",
      "aria-label": "Powered by Claude — token usage",
    });
    badge.appendChild(el("span", { class: "claude-badge-label" }, "Powered by Claude"));
    const cacheRead =
      typeof usage.cache_read_tokens === "number" ? usage.cache_read_tokens : 0;
    const inputTokens =
      typeof usage.input_tokens === "number" ? usage.input_tokens : 0;
    const outputTokens =
      typeof usage.output_tokens === "number" ? usage.output_tokens : 0;
    badge.appendChild(
      el(
        "span",
        { class: "claude-badge-counts" },
        "cache " +
          integerFormatter.format(cacheRead) +
          " · in " +
          integerFormatter.format(inputTokens) +
          " · out " +
          integerFormatter.format(outputTokens)
      )
    );
    return badge;
  }

  /** Build a <table> from a list of row objects with stable column order. */
  function buildResultTable(rows) {
    const columns = [];
    const seen = Object.create(null);
    for (let i = 0; i < rows.length; i += 1) {
      const row = rows[i] || {};
      for (const key in row) {
        if (Object.prototype.hasOwnProperty.call(row, key) && !seen[key]) {
          seen[key] = true;
          columns.push(key);
        }
      }
    }

    const table = el("table", {
      class: "answer-table",
      role: "table",
      "aria-label": "Query result rows",
    });
    const thead = el("thead");
    const headerRow = el("tr");
    for (let c = 0; c < columns.length; c += 1) {
      headerRow.appendChild(el("th", { scope: "col" }, columns[c]));
    }
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = el("tbody");
    for (let r = 0; r < rows.length; r += 1) {
      const tr = el("tr");
      for (let c = 0; c < columns.length; c += 1) {
        const column = columns[c];
        const value = rows[r] ? rows[r][column] : null;
        tr.appendChild(el("td", null, formatCell(value, column)));
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  /* ---------- Error rendering ---------- */

  /**
   * Map an HTTP error (401/403/404/429/500/...) to a user-facing message and
   * surface it as a toast. The unified envelope's `error.message` is read
   * directly (holistic MA-03 — no Pydantic-style fallbacks).
   */
  function renderHttpError(status, payload) {
    const detail =
      payload && payload.error && payload.error.message
        ? payload.error.message
        : null;
    let message;
    switch (status) {
      case 401:
        message = detail || "Sign in required.";
        break;
      case 403:
        message =
          detail ||
          "Forbidden. Your account does not have permission for this action.";
        break;
      case 404:
        message =
          detail ||
          "Your account is not registered. Contact your administrator.";
        break;
      case 413:
        message = detail || "Your question is too long.";
        break;
      case 429:
        message = detail || "Too many questions. Please slow down.";
        break;
      case 500:
        message = detail || "Unexpected error. Try again.";
        break;
      default:
        message =
          (detail || "Request failed") + " (HTTP " + status + ").";
    }
    showToast(message);

    // For 401 we re-trigger the sign-in check so the user gets pushed back
    // to AAD if their session has lapsed. 404 from /api/ask is the
    // "AAD-authenticated but not in dim_UserRoles" case — no redirect helps.
    if (status === 401) {
      window.setTimeout(function () {
        const here = window.location.pathname + window.location.search;
        window.location.assign(
          "/.auth/login/aad?post_login_redirect_uri=" + encodeURIComponent(here)
        );
      }, 1500);
    }
  }

  /* ---------- Toast ---------- */

  let toastTimer = null;

  function showToast(message) {
    elements.toastMessage.textContent = message;
    elements.toast.hidden = false;
    if (toastTimer) {
      window.clearTimeout(toastTimer);
    }
    toastTimer = window.setTimeout(hideToast, 8000);
  }

  function hideToast() {
    elements.toast.hidden = true;
    if (toastTimer) {
      window.clearTimeout(toastTimer);
      toastTimer = null;
    }
  }

  /* ---------- Form submit ---------- */

  function onSubmit(event) {
    event.preventDefault();
    const question = (elements.input.value || "").trim();
    if (!question) {
      elements.input.focus();
      return;
    }
    appendUserMessage(question);
    elements.input.value = "";
    askQuestion(question);
  }

  /* ---------- Bootstrap ---------- */

  function init() {
    resolveElements();

    elements.form.addEventListener("submit", onSubmit);
    elements.wakeButton.addEventListener("click", wakeDatabase);
    elements.toastClose.addEventListener("click", hideToast);

    const suggestedButtons = elements.suggestedList.querySelectorAll(
      "button.suggested"
    );
    for (let i = 0; i < suggestedButtons.length; i += 1) {
      bindSuggestedQuestion(suggestedButtons[i]);
    }

    loadCurrentUser();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
