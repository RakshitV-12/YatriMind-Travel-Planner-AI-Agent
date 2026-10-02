(function (global) {
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

  // Configuration: Inactivity Session Timeout
  // Default: 15 minutes of inactivity before automatic logout
  // Pre-timeout warning modal displays in the final 60 seconds of inactivity
  const DEFAULT_TIMEOUT_MINUTES = 15;
  const DEFAULT_WARNING_SECONDS = 60;

  function getTimeoutMs() {
    const customMinutes = Number(global.YATRAMIND_TIMEOUT_MINUTES);
    const minutes = (!isNaN(customMinutes) && customMinutes > 0) ? customMinutes : DEFAULT_TIMEOUT_MINUTES;
    return minutes * 60 * 1000;
  }

  function getWarningMs() {
    const customWarningSec = Number(global.YATRAMIND_WARNING_SECONDS);
    const seconds = (!isNaN(customWarningSec) && customWarningSec > 0) ? customWarningSec : DEFAULT_WARNING_SECONDS;
    return seconds * 1000;
  }

  const STORAGE_KEYS = {
    USER: "yatramind_user",
    TOKEN: "yatramind_token",
    LAST_ACTIVITY: "yatramind_last_activity",
    LOGIN_TIME: "yatramind_login_time",
    TRIP_USER: "tripUser",
    SESSION_ID: "yatramind_session_id"
  };

  const THROTTLE_ACTIVITY_MS = 2500; // Update activity timestamp at most once every 2.5s
  const CHECK_INTERVAL_MS = 3000;     // Inactivity check every 3s
  let lastRecordedActivity = 0;
  let checkIntervalId = null;
  let modalCountdownIntervalId = null;

  /* ---------- Storage Helpers ---------- */
  function getUser() {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.USER) ||
                  sessionStorage.getItem(STORAGE_KEYS.USER) ||
                  sessionStorage.getItem(STORAGE_KEYS.TRIP_USER);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function getToken() {
    return localStorage.getItem(STORAGE_KEYS.TOKEN) ||
           sessionStorage.getItem(STORAGE_KEYS.TOKEN) ||
           "";
  }

  function isLoggedIn() {
    return Boolean(getUser() || getToken());
  }

  function setSession(user, token) {
    if (!user) user = { name: "Traveler", email: "traveler@yatramind.ai" };
    try {
      const userJson = JSON.stringify(user);
      const now = Date.now().toString();

      localStorage.setItem(STORAGE_KEYS.USER, userJson);
      localStorage.setItem(STORAGE_KEYS.LAST_ACTIVITY, now);
      localStorage.setItem(STORAGE_KEYS.LOGIN_TIME, now);

      sessionStorage.setItem(STORAGE_KEYS.USER, userJson);
      sessionStorage.setItem(STORAGE_KEYS.TRIP_USER, userJson);

      if (token) {
        localStorage.setItem(STORAGE_KEYS.TOKEN, token);
        sessionStorage.setItem(STORAGE_KEYS.TOKEN, token);
      }
      lastRecordedActivity = Date.now();
    } catch (e) {
      console.warn("[YatraSession] Failed to persist session:", e);
    }
  }

  function clearSession() {
    try {
      localStorage.removeItem(STORAGE_KEYS.USER);
      localStorage.removeItem(STORAGE_KEYS.TOKEN);
      localStorage.removeItem(STORAGE_KEYS.LAST_ACTIVITY);
      localStorage.removeItem(STORAGE_KEYS.LOGIN_TIME);
      localStorage.removeItem(STORAGE_KEYS.SESSION_ID);

      sessionStorage.removeItem(STORAGE_KEYS.USER);
      sessionStorage.removeItem(STORAGE_KEYS.TOKEN);
      sessionStorage.removeItem(STORAGE_KEYS.TRIP_USER);
      sessionStorage.removeItem(STORAGE_KEYS.SESSION_ID);
    } catch (e) {
      console.warn("[YatraSession] Failed to clear session:", e);
    }
  }

  function getLastActivity() {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.LAST_ACTIVITY);
      const ts = raw ? parseInt(raw, 10) : 0;
      return !isNaN(ts) ? ts : 0;
    } catch (_) {
      return 0;
    }
  }

  function getIdleTime() {
    const last = getLastActivity();
    if (!last) return 0;
    return Math.max(0, Date.now() - last);
  }

  function isSessionExpired() {
    if (!isLoggedIn()) return false;
    const last = getLastActivity();
    // If user is logged in but no activity timestamp is found, seed it now
    if (!last) {
      recordActivity(true);
      return false;
    }
    return (Date.now() - last) >= getTimeoutMs();
  }

  function recordActivity(force = false) {
    if (!isLoggedIn()) return;
    const now = Date.now();
    if (!force && (now - lastRecordedActivity < THROTTLE_ACTIVITY_MS)) {
      return;
    }
    lastRecordedActivity = now;
    try {
      localStorage.setItem(STORAGE_KEYS.LAST_ACTIVITY, now.toString());
    } catch (_) {}

    // Dismiss warning modal if it was open
    hideWarningModal();
  }

  function getAuthHeaders(extraHeaders = {}) {
    const headers = { "Content-Type": "application/json", ...extraHeaders };
    const token = getToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  /* ---------- Warning Modal UI ---------- */
  function ensureModalInDom() {
    let overlay = document.getElementById("yatramind-session-timeout-modal");
    if (overlay) return overlay;

    overlay = document.createElement("div");
    overlay.id = "yatramind-session-timeout-modal";
    overlay.className = "session-timeout-overlay";
    overlay.style.display = "none";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-labelledby", "session-timeout-heading");

    overlay.innerHTML = `
      <div class="session-timeout-card" id="session-timeout-card">
        <div class="session-timeout-icon" aria-hidden="true">⏱️</div>
        <h3 id="session-timeout-heading" class="session-timeout-title">Session Inactivity Warning</h3>
        <p class="session-timeout-text">
          You have been inactive for a while. For your privacy and security, you will be automatically signed out in:
        </p>
        <div class="session-timeout-countdown-badge" id="session-timeout-countdown">60s</div>
        <div class="session-timeout-actions">
          <button type="button" class="session-timeout-btn-stay" id="session-timeout-stay-btn">
            Keep Me Signed In
          </button>
          <button type="button" class="session-timeout-btn-logout" id="session-timeout-logout-btn">
            Sign Out Now
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);

    const stayBtn = document.getElementById("session-timeout-stay-btn");
    const logoutBtn = document.getElementById("session-timeout-logout-btn");

    if (stayBtn) {
      stayBtn.addEventListener("click", () => {
        recordActivity(true);
      });
    }

    if (logoutBtn) {
      logoutBtn.addEventListener("click", () => {
        logout({ reason: "manual" });
      });
    }

    // Close and reset on Escape key
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        recordActivity(true);
      }
    });

    return overlay;
  }

  function showWarningModal(remainingSeconds) {
    // Only show warning if user is logged in and not on login page
    if (!isLoggedIn() || window.location.pathname.endsWith("login.html") || window.location.pathname.endsWith("/login")) {
      return;
    }

    const overlay = ensureModalInDom();
    const countdownEl = document.getElementById("session-timeout-countdown");
    if (countdownEl) {
      countdownEl.textContent = `${Math.max(1, remainingSeconds)}s`;
    }

    if (overlay.style.display !== "flex") {
      overlay.style.display = "flex";
      const stayBtn = document.getElementById("session-timeout-stay-btn");
      if (stayBtn) stayBtn.focus();
    }
  }

  function hideWarningModal() {
    const overlay = document.getElementById("yatramind-session-timeout-modal");
    if (overlay && overlay.style.display !== "none") {
      overlay.style.display = "none";
    }
    if (modalCountdownIntervalId) {
      clearInterval(modalCountdownIntervalId);
      modalCountdownIntervalId = null;
    }
  }

  /* ---------- Logout Handling ---------- */
  async function logout(options = {}) {
    const reason = options.reason || "manual";
    hideWarningModal();

    // 1. Notify backend to clear session cookie
    try {
      const base = getApiBase();
      await fetch(`${base}/api/auth/logout`, {
        method: "POST",
        headers: getAuthHeaders(),
        credentials: "include"
      });
    } catch (_) {
      // Backend may be unreachable or offline; continue clearing client session
    }

    // 2. Clear local storage & session state
    clearSession();

    // 3. Redirect to login with appropriate query parameter
    const loginUrl = (reason === "timeout") ? "login.html?timeout=1" : "login.html?logout=1";
    if (!window.location.pathname.endsWith("login.html") && !window.location.pathname.endsWith("/login")) {
      window.location.href = loginUrl;
    }
  }

  /* ---------- Inactivity Checking Loop ---------- */
  function checkSessionInactivity() {
    if (!isLoggedIn()) {
      hideWarningModal();
      return;
    }

    const timeoutMs = getTimeoutMs();
    const warningMs = getWarningMs();
    const idleTime = getIdleTime();

    // Condition 1: Timeout threshold exceeded -> log out immediately
    if (idleTime >= timeoutMs) {
      console.warn(`[YatraSession] Session timed out after ${Math.round(idleTime / 1000)}s of inactivity.`);
      logout({ reason: "timeout" });
      return;
    }

    // Condition 2: Approaching timeout within warning window -> show warning modal
    if (idleTime >= (timeoutMs - warningMs)) {
      const remainingSeconds = Math.max(1, Math.ceil((timeoutMs - idleTime) / 1000));
      showWarningModal(remainingSeconds);
    } else {
      hideWarningModal();
    }
  }

  /* ---------- User Activity Event Listeners ---------- */
  function setupActivityListeners() {
    const events = ["mousemove", "mousedown", "keydown", "touchstart", "scroll", "click", "focus"];
    const onUserActivity = () => {
      recordActivity(false);
    };

    events.forEach(eventName => {
      window.addEventListener(eventName, onUserActivity, { passive: true });
    });

    // Cross-tab synchronization via storage event
    window.addEventListener("storage", (e) => {
      if (e.key === STORAGE_KEYS.LAST_ACTIVITY) {
        // Activity detected in another tab! Dismiss any warning here.
        hideWarningModal();
      } else if (e.key === STORAGE_KEYS.USER || e.key === STORAGE_KEYS.TOKEN) {
        if (!e.newValue && isLoggedIn()) {
          // Logged out in another tab
          logout({ reason: "manual" });
        }
      }
    });

    // Check immediately when user switches back to this tab
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        if (isSessionExpired()) {
          logout({ reason: "timeout" });
        } else {
          recordActivity(true);
        }
      }
    });
  }

  /* ---------- Initialization ---------- */
  function init() {
    const isLoginPage = window.location.pathname.endsWith("login.html") || window.location.pathname.endsWith("/login");

    // If on a content page (e.g. index.html, results.html)
    if (!isLoginPage) {
      // 1. Enforce authentication: if not logged in, redirect to login page
      if (!isLoggedIn()) {
        console.log("[YatraSession] Unauthenticated. Redirecting to login.html...");
        const currentPath = window.location.pathname.split("/").pop() || "index.html";
        const redirectParam = (currentPath !== "index.html" && currentPath !== "")
          ? `?redirect=${encodeURIComponent(currentPath + window.location.search)}`
          : "";
        window.location.replace(`login.html${redirectParam}`);
        return;
      }

      // 2. Immediately check if existing session has expired from prior inactivity
      if (isSessionExpired()) {
        console.warn("[YatraSession] Existing session expired during idle period. Redirecting to login...");
        logout({ reason: "timeout" });
        return;
      }

      // 3. If logged in and valid, refresh activity timestamp so page load counts as active
      recordActivity(true);

      // 4. Start listeners & periodic check loop
      setupActivityListeners();
      if (checkIntervalId) clearInterval(checkIntervalId);
      checkIntervalId = setInterval(checkSessionInactivity, CHECK_INTERVAL_MS);
    }
  }

  // Auto-init on script load
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Public API exposed on window.YatraSession
  global.YatraSession = {
    getUser,
    getToken,
    isLoggedIn,
    setSession,
    clearSession,
    getLastActivity,
    getIdleTime,
    isSessionExpired,
    recordActivity,
    logout,
    getAuthHeaders,
    getApiBase,
    getTimeoutMinutes: () => Math.round(getTimeoutMs() / 60000),
    setTimeoutMinutes: (min) => {
      global.YATRAMIND_TIMEOUT_MINUTES = min;
    }
  };

})(typeof window !== "undefined" ? window : this);
