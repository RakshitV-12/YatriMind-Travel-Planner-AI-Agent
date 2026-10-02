(function () {
  "use strict";

  // Backend origin. Points to the deployed Render backend API.
  const RENDER_BACKEND_URL = "https://yatrimind-travel-planner-ai-agent.onrender.com";
  let activeApiBase = null;

  function getApiBase() {
    if (activeApiBase !== null) return activeApiBase;

    // 1. If currently on Render or matches RENDER_BACKEND_URL, use relative origin
    if (window.location.origin === RENDER_BACKEND_URL || window.location.hostname.endsWith(".onrender.com")) {
      activeApiBase = "";
      return "";
    }

    // 2. If running directly on port 8000 (FastAPI), use relative origin
    if (window.location.port === "8000") {
      activeApiBase = "";
      return "";
    }

    // 3. If running locally on another port (e.g. Live Server on 5500, Vite, file://)
    if (window.location.hostname === "localhost") {
      return "http://localhost:8000";
    }
    if (window.location.hostname === "127.0.0.1" || !window.location.protocol.startsWith("http")) {
      return "http://127.0.0.1:8000";
    }

    // 4. Default for external deployed frontend (e.g. Vercel, Netlify)
    return RENDER_BACKEND_URL;
  }

  function getAuthToken() {
    if (window.YatraSession) return window.YatraSession.getToken();
    return localStorage.getItem("yatramind_token") || sessionStorage.getItem("yatramind_token") || "";
  }

  function getCurrentUser() {
    if (window.YatraSession) return window.YatraSession.getUser();
    try {
      const raw = localStorage.getItem("yatramind_user") || sessionStorage.getItem("yatramind_user");
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function getAuthHeaders(extraHeaders = {}) {
    if (window.YatraSession) return window.YatraSession.getAuthHeaders(extraHeaders);
    const headers = { "Content-Type": "application/json", ...extraHeaders };
    const token = getAuthToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  const destinationEl = document.getElementById("destination");
  const originEl = document.getElementById("origin");
  const startDateEl = document.getElementById("start_date");
  const endDateEl = document.getElementById("end_date");
  const durationNoteEl = document.getElementById("duration-note");
  const travelersEl = document.getElementById("travelers");
  const currencyEl = document.getElementById("currency");
  const budgetSymbolEl = document.getElementById("budget-symbol");
  const budgetDisplayEl = document.getElementById("budget_display");
  const budgetWordsEl = document.getElementById("budget-words");
  const formEl = document.getElementById("trip-form");
  const errorEl = document.getElementById("form-error");
  const submitBtn = document.getElementById("submit-btn");
  const loadingOverlay = document.getElementById("loading-overlay");
  const loadingStatusEl = document.getElementById("loading-status");

  const CURRENCY_SYMBOLS = { INR: "₹", USD: "$", EUR: "€", GBP: "£", AED: "AED " };

  // ---------- Popular destination quick-picks ----------
  document.querySelectorAll(".dest-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      destinationEl.value = pill.dataset.dest;
      document.querySelectorAll(".dest-pill").forEach((p) => p.classList.toggle("is-selected", p === pill));
      hideError();
      destinationEl.focus();
    });
  });
  destinationEl.addEventListener("input", () => {
    document.querySelectorAll(".dest-pill").forEach((p) => {
      p.classList.toggle("is-selected", p.dataset.dest.toLowerCase() === destinationEl.value.trim().toLowerCase());
    });
  });

  // ---------- Dates ----------
  function isoDate(d) { return d.toISOString().slice(0, 10); }

  const today = new Date();
  const defaultReturn = new Date(today);
  defaultReturn.setDate(defaultReturn.getDate() + 3);

  startDateEl.min = isoDate(today);
  startDateEl.value = isoDate(today);
  endDateEl.value = isoDate(defaultReturn);
  endDateEl.min = isoDate(defaultReturn);

  function updateDurationNote() {
    const start = new Date(startDateEl.value);
    const end = new Date(endDateEl.value);
    if (isNaN(start) || isNaN(end) || end <= start) {
      durationNoteEl.textContent = "";
      return;
    }
    const days = Math.round((end - start) / 86400000);
    durationNoteEl.textContent = `${days} day${days === 1 ? "" : "s"}, ${days - 1} night${days - 1 === 1 ? "" : "s"}`;
  }

  startDateEl.addEventListener("change", () => {
    const start = new Date(startDateEl.value);
    if (!isNaN(start)) {
      const minEnd = new Date(start);
      minEnd.setDate(minEnd.getDate() + 1);
      endDateEl.min = isoDate(minEnd);
      if (new Date(endDateEl.value) <= start) {
        endDateEl.value = isoDate(minEnd);
      }
    }
    updateDurationNote();
  });
  endDateEl.addEventListener("change", updateDurationNote);
  updateDurationNote();

  // ---------- Budget formatting ----------
  function digitsOnly(str) { return (str || "").replace(/[^\d]/g, ""); }

  // Indian digit grouping: last 3 digits, then groups of 2 (e.g. 12,34,567)
  function formatIndian(numStr) {
    if (!numStr) return "";
    const last3 = numStr.slice(-3);
    const rest = numStr.slice(0, -3);
    const grouped = rest ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + last3 : last3;
    return grouped;
  }

  function formatStandard(numStr) {
    if (!numStr) return "";
    return numStr.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function formatForCurrency(numStr, currency) {
    return currency === "INR" ? formatIndian(numStr) : formatStandard(numStr);
  }

  function lakhCroreWords(n) {
    if (!n || n < 100000) return "";
    if (n >= 10000000) {
      const cr = n / 10000000;
      return `≈ ${trimNum(cr)} crore`;
    }
    const lakh = n / 100000;
    return `≈ ${trimNum(lakh)} lakh`;
  }

  function trimNum(n) {
    return n % 1 === 0 ? n.toFixed(0) : n.toFixed(1);
  }

  function refreshBudgetWords() {
    const raw = digitsOnly(budgetDisplayEl.value);
    const n = parseInt(raw || "0", 10);
    if (currencyEl.value === "INR") {
      budgetWordsEl.textContent = lakhCroreWords(n);
    } else {
      budgetWordsEl.textContent = "";
    }
  }

  budgetDisplayEl.addEventListener("input", () => {
    const caretFromEnd = budgetDisplayEl.value.length - budgetDisplayEl.selectionStart;
    const raw = digitsOnly(budgetDisplayEl.value);
    budgetDisplayEl.value = formatForCurrency(raw, currencyEl.value);
    const pos = Math.max(0, budgetDisplayEl.value.length - caretFromEnd);
    budgetDisplayEl.setSelectionRange(pos, pos);
    refreshBudgetWords();
  });

  currencyEl.addEventListener("change", () => {
    budgetSymbolEl.textContent = CURRENCY_SYMBOLS[currencyEl.value] || currencyEl.value;
    const raw = digitsOnly(budgetDisplayEl.value);
    budgetDisplayEl.value = formatForCurrency(raw, currencyEl.value);
    refreshBudgetWords();
  });

  refreshBudgetWords();

  // ---------- Loading overlay ----------
  const LOADING_MESSAGES = [
    "Searching verified places…",
    "Checking hotel pricing against your budget…",
    "Mapping the route between stops…",
    "Writing up each day…",
    "Double-checking ratings are real…",
  ];
  let loadingTimer = null;

  function startLoading() {
    loadingOverlay.hidden = false;
    let i = 0;
    loadingStatusEl.textContent = LOADING_MESSAGES[0];
    loadingTimer = setInterval(() => {
      i = (i + 1) % LOADING_MESSAGES.length;
      loadingStatusEl.textContent = LOADING_MESSAGES[i];
    }, 4200);
  }

  function stopLoading() {
    loadingOverlay.hidden = true;
    if (loadingTimer) clearInterval(loadingTimer);
    loadingTimer = null;
  }

  // ---------- Errors ----------
  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.hidden = false;
  }
  function hideError() {
    errorEl.hidden = true;
    errorEl.textContent = "";
  }

  function extractErrorMessage(status, body) {
    if (status === 429) {
      return "You've hit the hourly limit for trip requests — please try again in a little while.";
    }
    if (body && body.detail) {
      if (typeof body.detail === "string") return body.detail;
      if (Array.isArray(body.detail)) return "Please check your trip details and try again.";
      if (typeof body.detail === "object" && body.detail.message) return body.detail.message;
    }
    if (body && body.message) return body.message;
    return "Something went wrong while planning the trip. Please try again.";
  }

  // ---------- Submit ----------
  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideError();

    const destination = destinationEl.value.trim();
    const startDate = startDateEl.value;
    const endDate = endDateEl.value;
    const travelers = parseInt(travelersEl.value, 10) || 2;
    const currency = currencyEl.value;
    const budget = parseFloat(digitsOnly(budgetDisplayEl.value) || "0");
    const tripTier = formEl.querySelector('input[name="trip_tier"]:checked').value;
    const interests = Array.from(
      document.querySelectorAll('#interests-group input[type="checkbox"]:checked')
    ).map((el) => el.value);

    if (destination.length < 2) {
      showError("Enter a destination to plan the trip.");
      return;
    }
    if (!startDate || !endDate || new Date(endDate) <= new Date(startDate)) {
      showError("Pick a return date after your departure date.");
      return;
    }
    if (!budget || budget <= 0) {
      showError("Enter a budget greater than zero.");
      return;
    }

    const payload = {
      destination,
      origin: originEl.value.trim() || null,
      start_date: startDate,
      end_date: endDate,
      travelers,
      budget,
      currency,
      interests,
      trip_tier: tripTier,
    };

    submitBtn.disabled = true;
    startLoading();

    let apiBase = getApiBase();
    let res;
    try {
      try {
        res = await fetch(`${apiBase}/api/plan-trip`, {
          method: "POST",
          headers: getAuthHeaders(),
          credentials: "include",
          body: JSON.stringify(payload),
        });
      } catch (fetchErr) {
        // If local 8000 was unreachable and not already using Render, auto-fallback to deployed Render backend!
        if (apiBase && apiBase !== RENDER_BACKEND_URL && !window.location.hostname.endsWith(".onrender.com")) {
          console.warn("[TRIPMATE] Local backend unreachable, trying deployed Render backend...");
          apiBase = RENDER_BACKEND_URL;
          activeApiBase = RENDER_BACKEND_URL;
          res = await fetch(`${apiBase}/api/plan-trip`, {
            method: "POST",
            headers: getAuthHeaders(),
            credentials: "include",
            body: JSON.stringify(payload),
          });
        } else {
          throw fetchErr;
        }
      }

      if (res.status === 401) {
        // Save form payload so the traveler's inputs are preserved!
        sessionStorage.setItem("pendingTripPayload", JSON.stringify(payload));
        window.location.href = "login.html?redirect=index.html&autoPlan=1";
        return;
      }

      let body = null;
      try { body = await res.json(); } catch (_) { /* no body */ }

      if (!res.ok) {
        showError(extractErrorMessage(res.status, body));
        stopLoading();
        submitBtn.disabled = false;
        return;
      }

      sessionStorage.setItem("tripItinerary", JSON.stringify(body));
      sessionStorage.setItem("tripRequestPayload", JSON.stringify(payload));
      const currentUser = getCurrentUser();
      if (currentUser) {
        sessionStorage.setItem("tripUser", JSON.stringify(currentUser));
      }
      window.location.href = "results.html";
    } catch (err) {
      showError(`Couldn't reach the planner at ${apiBase || 'server'}. Make sure the backend is running, then try again.`);
      stopLoading();
      submitBtn.disabled = false;
    }
  });

  // ---------- User Session & Profile Rendering ----------
  function renderUserNav(user) {
    const slot = document.getElementById("nav-auth-slot");
    const greetingBanner = document.getElementById("user-greeting-banner");
    const greetingName = document.getElementById("greeting-user-name");

    if (!slot) return;
    if (user && (user.name || user.email)) {
      const displayName = user.name || user.email.split("@")[0];
      const initial = (displayName[0] || "U").toUpperCase();
      slot.innerHTML = `
        <div class="nav-user-pill">
          <span class="nav-user-avatar">${user.picture ? `<img src="${user.picture}" alt="${displayName}">` : initial}</span>
          <span class="nav-user-name">${displayName}</span>
          <button type="button" class="nav-signout-btn" id="nav-signout-btn" title="Sign out">Log out</button>
        </div>
      `;
      if (greetingBanner && greetingName) {
        greetingName.textContent = displayName;
        greetingBanner.style.display = "flex";
      }
      document.getElementById("nav-signout-btn")?.addEventListener("click", () => {
        if (window.YatraSession) {
          window.YatraSession.logout({ reason: "manual" });
        } else {
          fetch(`${getApiBase()}/api/auth/logout`, { method: "POST", headers: getAuthHeaders(), credentials: "include" }).catch(() => {});
          localStorage.removeItem("yatramind_user");
          localStorage.removeItem("yatramind_token");
          sessionStorage.removeItem("yatramind_user");
          sessionStorage.removeItem("yatramind_token");
          sessionStorage.removeItem("tripUser");
          renderUserNav(null);
          if (greetingBanner) greetingBanner.style.display = "none";
          window.location.href = "login.html?logout=1";
        }
      });
    } else {
      slot.innerHTML = `<a href="login.html" class="nav-auth-btn" id="nav-login-btn">Sign in</a>`;
      if (greetingBanner) greetingBanner.style.display = "none";
    }
  }

  function initUserSession() {
    // 0. Verify if existing session has expired from inactivity
    if (window.YatraSession && window.YatraSession.isSessionExpired()) {
      window.YatraSession.logout({ reason: "timeout" });
      return;
    }

    // 1. Immediately render local user from storage (zero UI delay)
    const localUser = getCurrentUser();
    if (localUser) {
      renderUserNav(localUser);
      if (window.YatraSession) window.YatraSession.recordActivity(true);
    } else {
      renderUserNav(null);
    }

    // 2. Validate in background with the backend
    fetch(`${getApiBase()}/api/auth/me`, { headers: getAuthHeaders(), credentials: "include" })
      .then(res => {
        if (!res.ok) throw new Error("Unauthenticated");
        return res.json();
      })
      .then(data => {
        if (data.user) {
          if (window.YatraSession) {
            window.YatraSession.setSession(data.user, getAuthToken());
          } else {
            const userJson = JSON.stringify(data.user);
            localStorage.setItem("yatramind_user", userJson);
            sessionStorage.setItem("yatramind_user", userJson);
          }
          renderUserNav(data.user);
        }
      })
      .catch(() => {
        // If unauthenticated by backend, clear stale credentials
        if (window.YatraSession) {
          window.YatraSession.clearSession();
        } else {
          localStorage.removeItem("yatramind_user");
          localStorage.removeItem("yatramind_token");
        }
        renderUserNav(null);
      });
  }

  // ---------- Restore Pending Trip & Auto-Plan Support ----------
  function checkPendingTrip() {
    const raw = sessionStorage.getItem("pendingTripPayload");
    if (!raw) return;

    try {
      const p = JSON.parse(raw);
      if (p.destination) destinationEl.value = p.destination;
      if (p.origin) originEl.value = p.origin;
      if (p.start_date) startDateEl.value = p.start_date;
      if (p.end_date) endDateEl.value = p.end_date;
      if (p.travelers) travelersEl.value = p.travelers;
      if (p.currency) {
        currencyEl.value = p.currency;
        budgetSymbolEl.textContent = CURRENCY_SYMBOLS[p.currency] || p.currency;
      }
      if (p.budget) {
        budgetDisplayEl.value = formatForCurrency(String(p.budget), p.currency || "INR");
      }
      if (p.trip_tier) {
        const radio = formEl.querySelector(`input[name="trip_tier"][value="${p.trip_tier}"]`);
        if (radio) radio.checked = true;
      }
      if (Array.isArray(p.interests)) {
        document.querySelectorAll('#interests-group input[type="checkbox"]').forEach(cb => {
          cb.checked = p.interests.includes(cb.value);
        });
      }
      updateDurationNote();
      refreshBudgetWords();

      const params = new URLSearchParams(window.location.search);
      if (params.get("autoPlan") === "1") {
        window.history.replaceState({}, document.title, window.location.pathname);
        sessionStorage.removeItem("pendingTripPayload");

        if (getCurrentUser() || getAuthToken()) {
          setTimeout(() => {
            submitBtn.click();
          }, 450);
        }
      }
    } catch (e) {
      console.warn("[TRIPMATE] Failed restoring pending trip:", e);
    }
  }

  initUserSession();
  checkPendingTrip();
})();