/* Assessment Platform – shared JS utilities */
(function () {
  "use strict";

  /** HTML-escape any value before inserting it into markup (XSS protection). */
  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /** Turn an API error payload (string, FastAPI/Pydantic list, or object) into readable text. */
  function errorMessage(payload, status) {
    var detail = payload && payload.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map(function (d) {
        var field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : "";
        var label = field && field !== "body" ? String(field).replace(/_/g, " ") + ": " : "";
        return label + (d.msg || "Invalid value");
      }).join("\n");
    }
    if (status === 429) return "Too many requests. Please wait a minute and try again.";
    if (status >= 500) return "Something went wrong on our side. Please try again.";
    return "Request failed (" + status + ")";
  }

  /** fetch wrapper: same-origin cookies, JSON in/out (or FormData), throws Error with .status. */
  async function api(path, options) {
    options = options || {};
    var init = { method: options.method || "GET", credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (options.body instanceof FormData) {
      init.body = options.body;
    } else if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    var res;
    try { res = await fetch(path, init); }
    catch (e) { var err = new Error("Network error. Check your connection and try again."); err.status = 0; throw err; }
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      var error = new Error(errorMessage(data, res.status));
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function showAlert(el, message, type) {
    if (!el) return;
    el.className = "alert " + (type || "info");
    el.style.whiteSpace = "pre-line";
    el.textContent = message;
    el.classList.remove("hidden");
    if (el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function hideAlert(el) { if (el) el.classList.add("hidden"); }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) { btn.dataset.label = btn.textContent; btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> ' + esc(label || "Please wait…"); }
    else { btn.disabled = false; btn.textContent = btn.dataset.label || label || btn.textContent; }
  }

  function fmtDate(value, withTime) {
    if (!value) return "—";
    var d = new Date(value);
    if (isNaN(d)) return esc(value);
    return withTime ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
                    : d.toLocaleDateString(undefined, { dateStyle: "medium" });
  }

  function fmtMoney(amount, currency) {
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "INR", maximumFractionDigits: 2 }).format(amount); }
    catch (e) { return (currency || "") + " " + amount; }
  }

  var PAYMENT_BADGES = { unpaid: ["Not paid", ""], pending: ["Awaiting verification", "yellow"], verified: ["Verified", "green"], rejected: ["Rejected – resubmit", "red"] };
  var STATUS_BADGES = { draft: ["Draft", ""], under_review: ["Under review", "blue"], shortlisted: ["Shortlisted", "green"], accepted: ["Accepted", "green"], rejected: ["Not selected", "red"] };
  function badge(map, key) { var b = map[key] || [String(key || "—").replace(/_/g, " "), ""]; return '<span class="badge ' + b[1] + '">' + esc(b[0]) + "</span>"; }
  function paymentBadge(s) { return badge(PAYMENT_BADGES, s); }
  function statusBadge(s) { return badge(STATUS_BADGES, s); }

  /** Returns the signed-in user, or null when logged out. */
  async function currentUser() {
    var res = await api("/api/auth/session");
    return res.user || null;
  }

  /** Page guard: redirect to /login when logged out (or to /dashboard if admin required but not admin). */
  async function requireAuth(opts) {
    opts = opts || {};
    var user = await currentUser();
    if (!user) { window.location.href = "/login"; return null; }
    if (opts.admin && user.role !== "admin") { window.location.href = "/dashboard"; return null; }
    return user;
  }

  async function logout() {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { /* ignore */ }
    window.location.href = "/login";
  }

  /** Fill the header nav depending on login state. */
  async function renderNav() {
    var nav = document.getElementById("nav");
    if (!nav) return;
    var user = null;
    try { user = await currentUser(); } catch (e) { /* offline */ }
    if (user) {
      var links = user.role === "admin"
        ? '<a href="/admin">Admin</a>'
        : '<a href="/dashboard">Dashboard</a>';
      nav.innerHTML = links + '<button type="button" class="linklike" id="logout-btn">Log out</button>';
      document.getElementById("logout-btn").addEventListener("click", logout);
    } else {
      nav.innerHTML = '<a href="/login">Log in</a><a href="/register">Register</a>';
    }
  }

  function copyText(text, btn) {
    var done = function () { if (btn) { var o = btn.textContent; btn.textContent = "Copied!"; setTimeout(function () { btn.textContent = o; }, 1500); } };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done);
    else { var t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); done(); } catch (e) {} document.body.removeChild(t); }
  }

  window.App = { esc: esc, api: api, showAlert: showAlert, hideAlert: hideAlert, setBusy: setBusy, fmtDate: fmtDate,
    fmtMoney: fmtMoney, paymentBadge: paymentBadge, statusBadge: statusBadge, currentUser: currentUser,
    requireAuth: requireAuth, logout: logout, copyText: copyText };
  window.esc = esc;

  document.addEventListener("DOMContentLoaded", function () {
    renderNav();
    var y = document.getElementById("year"); if (y) y.textContent = new Date().getFullYear();
  });
})();
