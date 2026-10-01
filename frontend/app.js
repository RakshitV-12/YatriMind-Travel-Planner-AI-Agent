(function () {
  "use strict";

  // Backend origin. Points to the deployed Render backend API.
  // When loaded directly from the Render deployment, relative paths are used;
  // otherwise, requests route directly to the production Render URL.
  const RENDER_BACKEND_URL = "https://yatrimind-travel-planner-ai-agent.onrender.com";
  const API_BASE = (window.location.origin === RENDER_BACKEND_URL || window.location.hostname.endsWith("onrender.com"))
    ? ""
    : RENDER_BACKEND_URL;

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

    try {
      const res = await fetch(`${API_BASE}/api/plan-trip`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

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
      window.location.href = "results.html";
    } catch (err) {
      showError(`Couldn't reach the planner at ${API_BASE}. Make sure the backend is running there, then try again.`);
      stopLoading();
      submitBtn.disabled = false;
    }
  });
})();