(function () {
  "use strict";

  const CURRENCY_SYMBOLS = { INR: "₹", USD: "$", EUR: "€", GBP: "£", AED: "AED " };

  function formatIndian(numStr) {
    if (!numStr) return "0";
    const last3 = numStr.slice(-3);
    const rest = numStr.slice(0, -3);
    return rest ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + last3 : last3;
  }
  function formatStandard(numStr) {
    return numStr.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  function formatMoney(amount, currency) {
    if (amount === null || amount === undefined || isNaN(amount)) return "—";
    const n = Math.round(Number(amount));
    const digits = String(Math.abs(n));
    const grouped = currency === "INR" ? formatIndian(digits) : formatStandard(digits);
    const symbol = CURRENCY_SYMBOLS[currency] || (currency ? currency + " " : "");
    return `${n < 0 ? "-" : ""}${symbol}${grouped}`;
  }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function prettyLabel(key) {
    const s = String(key).replace(/_/g, " ").trim();
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  // Builds a Google Maps *directions* deep-link (not just a search pin) so the
  // button takes the traveler straight into turn-by-turn navigation. Uses
  // place_id when the backend provided one (more accurate than lat/lng alone
  // when two venues sit close together), and falls back to lat/lng, then to
  // a text address/name if neither is available.
  function directionsLink(lat, lng, placeId, fallbackQuery) {
    const params = new URLSearchParams();
    params.set("api", "1");
    if (typeof lat === "number" && typeof lng === "number") {
      params.set("destination", `${lat},${lng}`);
    } else if (fallbackQuery) {
      params.set("destination", fallbackQuery);
    } else {
      return null;
    }
    if (placeId) {
      params.set("destination_place_id", placeId);
    }
    return `https://www.google.com/maps/dir/?${params.toString()}`;
  }

  // A compact "navigation arrow" glyph — signals the button routes into
  // turn-by-turn directions, not a generic outbound link.
  function mapQuery(name, address) {
    // Combine both rather than picking one: the name alone is often too
    // generic to geocode precisely ("Zabeel Park" exists in many cities),
    // and the address alone is often just a neighborhood/area, which
    // collapses multiple distinct places in the same area onto the same
    // generic pin. Together, Google Maps resolves to the specific place.
    return [name, address].filter(Boolean).join(", ") || null;
  }

  // Safe address formatting: cleans up duplicate or missing segments cleanly,
  // e.g. "Trade Centre, Dubai" or simply "Dubai" if district is absent,
  // preventing "null, Dubai" or messy comma chains.
  function formatAddress(address, fallbackCity) {
    if (!address || typeof address !== "string") {
      return fallbackCity || "";
    }
    const trimmed = address.trim();
    if (!trimmed) return fallbackCity || "";

    const parts = trimmed.split(",").map((p) => p.trim()).filter(Boolean);
    const valid = [];
    const lowerSeen = new Set();

    for (const part of parts) {
      const lower = part.toLowerCase();
      if (
        lower === "null" ||
        lower === "undefined" ||
        lower === "none" ||
        lower === "n/a" ||
        lower === "unknown" ||
        lower === "not available"
      ) {
        continue;
      }
      if (!lowerSeen.has(lower)) {
        lowerSeen.add(lower);
        valid.push(part);
      }
    }

    if (!valid.length) {
      return fallbackCity || "";
    }
    return valid.join(", ");
  }

  const DIRECTIONS_ICON =
    '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<path d="M12 2 4.5 20.3l.9.7L12 18l6.6 3 .9-.7Z"/></svg>';

  function el(tag, className, html) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (html !== undefined) node.innerHTML = html;
    return node;
  }

  // ---------- Load data ----------
  const raw = sessionStorage.getItem("tripItinerary");
  const rawPayload = sessionStorage.getItem("tripRequestPayload");
  const emptyState = document.getElementById("empty-state");
  const content = document.getElementById("results-content");

  if (!raw) {
    emptyState.hidden = false;
    return;
  }

  let data, payload;
  try {
    data = JSON.parse(raw);
    payload = rawPayload ? JSON.parse(rawPayload) : {};
  } catch (e) {
    emptyState.hidden = false;
    return;
  }

  content.hidden = false;

  const currency = data.currency || payload.currency || "INR";

  // ---------- Top-level section tabs (Itinerary / Flights / Hotels / Budget) ----------
  // This is the main structural change: previously every section rendered
  // stacked on one long page. A dashboard reads as a dashboard when you can
  // focus on one part of the trip at a time instead of scrolling past
  // everything else to get there.
  (function initSectionTabs() {
    const tabBtns = Array.from(document.querySelectorAll(".section-tab-btn"));
    const panels = Array.from(document.querySelectorAll(".section-tab-panel"));
    tabBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const target = btn.dataset.tab;
        tabBtns.forEach((b) => b.classList.toggle("is-active", b === btn));
        panels.forEach((p) => p.classList.toggle("is-active", p.dataset.tabPanel === target));
      });
    });
  })();

  // ---------- Header ----------
  document.getElementById("ticket-destination").textContent = data.destination || payload.destination || "Your trip";
  document.getElementById("ticket-duration").textContent = data.duration ?? (data.days ? data.days.length : "—");
  document.getElementById("ticket-travelers").textContent = payload.travelers ?? "—";
  document.getElementById("ticket-budget").textContent = formatMoney(data.estimated_budget, currency);

  const tierLabel = payload.trip_tier ? prettyLabel(payload.trip_tier) : null;
  document.getElementById("ticket-tier").textContent = tierLabel ? `${tierLabel} trip` : "Your itinerary";

  const datesEl = document.getElementById("ticket-dates");
  if (payload.start_date && payload.end_date) {
    datesEl.textContent = `${formatDate(payload.start_date)} – ${formatDate(payload.end_date)}`;
  } else {
    datesEl.textContent = "";
  }

  // ---------- Day tabs + panels ----------
  const tabsEl = document.getElementById("day-tabs");
  const panelsEl = document.getElementById("day-panels");
  const days = Array.isArray(data.days) ? data.days : [];

  // ---------- Trip summary quick-stats card ----------
  // Every number here is derived directly from data already on the page
  // (day count, activity count, dining count) -- nothing new is fetched or
  // invented to populate this.
  (function renderTripSummary() {
    const wrap = document.getElementById("trip-summary-card");
    if (!wrap) return;
    const activityCount = days.reduce((sum, d) => sum + (Array.isArray(d.activities) ? d.activities.length : 0), 0);
    const diningCount = days.reduce((sum, d) => sum + (Array.isArray(d.dining) ? d.dining.length : 0), 0);

    const stats = [
      { value: data.duration || `${days.length} day${days.length === 1 ? "" : "s"}`, label: "trip length" },
      { value: formatMoney(data.estimated_budget, currency), label: "estimated total" },
      { value: String(activityCount), label: "planned activities" },
    ];
    if (diningCount) stats.push({ value: String(diningCount), label: "dining picks" });

    stats.forEach((s) => {
      const card = el("div", "trip-stat");
      card.appendChild(el("span", "trip-stat-value", escapeHtml(s.value)));
      card.appendChild(el("span", "trip-stat-label", escapeHtml(s.label)));
      wrap.appendChild(card);
    });
  })();

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str == null ? "" : String(str);
    return d.innerHTML;
  }

  function escapeXml(str) {
    return String(str).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;",
    })[char]);
  }

  const photoCache = new Map();
  const photoObserver = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        photoObserver.unobserve(entry.target);
        entry.target.loadPhoto();
      });
    }, { rootMargin: "240px" })
    : null;

  function imageUrlFrom(record) {
    if (!record || typeof record !== "object") return "";
    const candidates = [
      record.image_url, record.imageUrl, record.photo_url, record.photoUrl, record.image, record.photo,
      ...(Array.isArray(record.photos) ? record.photos.map((photo) => (
        typeof photo === "string" ? photo : photo && (photo.url || photo.image_url || photo.imageUrl)
      )) : []),
    ];
    for (const candidate of candidates) {
      if (typeof candidate !== "string") continue;
      if (!/^(https?:\/\/|\/\/|\/|\.\/)/i.test(candidate)) continue;
      try {
        const url = new URL(candidate, window.location.href);
        if (url.protocol === "http:" || url.protocol === "https:") return url.href;
      } catch (_) {
        // Ignore malformed optional image fields and fall back to a search.
      }
    }
    return "";
  }

  function generatedPhoto(label) {
    const escapedLabel = escapeXml(label.length > 42 ? `${label.slice(0, 39)}…` : label);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 480"><defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="#ded4c2"/><stop offset="1" stop-color="#a9b8b1"/></linearGradient><linearGradient id="land" x2="1" y2="1"><stop stop-color="#b7a68b"/><stop offset="1" stop-color="#73877d"/></linearGradient></defs><rect width="960" height="480" fill="url(#sky)"/><circle cx="760" cy="132" r="62" fill="#f6e7c8" opacity=".8"/><path d="M0 340 170 230l145 96 175-150 194 168 126-92 150 95v133H0Z" fill="#89988d" opacity=".72"/><path d="M0 390 210 305l175 90 198-127 177 111 120-48 80 49v100H0Z" fill="url(#land)"/><rect y="397" width="960" height="83" fill="#536e69" opacity=".32"/><text x="54" y="433" fill="#fffaf0" font-family="Arial,sans-serif" font-size="27" font-weight="600">${escapedLabel}</text></svg>`;
    return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
  }

  async function searchWikimediaPhoto(query, width) {
    if (!query) return null;
    const params = new URLSearchParams({
      action: "query",
      generator: "search",
      gsrsearch: query,
      gsrnamespace: "6",
      gsrlimit: "6",
      prop: "imageinfo",
      iiprop: "url|mime",
      iiurlwidth: String(width),
      format: "json",
      origin: "*",
    });
    try {
      const res = await fetch(`https://commons.wikimedia.org/w/api.php?${params}`);
      if (!res.ok) return null;
      const json = await res.json();
      const pages = Object.values((json.query && json.query.pages) || {});
      const page = pages.find((candidate) => {
        const info = candidate.imageinfo && candidate.imageinfo[0];
        return info && info.thumburl && info.mime && info.mime.startsWith("image/");
      });
      return page ? page.imageinfo[0].thumburl : null;
    } catch (_) {
      return null;
    }
  }

  async function searchWikipediaPhoto(query, width) {
    if (!query) return null;
    const params = new URLSearchParams({
      action: "query",
      generator: "search",
      gsrsearch: query,
      gsrlimit: "4",
      prop: "pageimages",
      pithumbsize: String(width),
      format: "json",
      origin: "*",
    });
    try {
      const res = await fetch(`https://en.wikipedia.org/w/api.php?${params}`);
      if (!res.ok) return null;
      const json = await res.json();
      const pages = Object.values((json.query && json.query.pages) || {});
      for (const p of pages) {
        if (p.thumbnail && p.thumbnail.source) {
          return p.thumbnail.source;
        }
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  function findPhoto(subject, destination, width, record = {}) {
    const rawDest = (destination || data.destination || payload.destination || "").trim();
    const cleanSubj = (subject || "").trim();
    if (!cleanSubj && !rawDest) return Promise.resolve(null);

    const cacheKey = `${cleanSubj.toLowerCase()}|${rawDest.toLowerCase()}|${width}`;
    if (photoCache.has(cacheKey)) return photoCache.get(cacheKey);

    const lookup = (async () => {
      const queries = [];

      // 1. Direct query: [subject, destination]
      if (cleanSubj && rawDest) {
        queries.push(`${cleanSubj} ${rawDest}`);
      } else if (cleanSubj) {
        queries.push(cleanSubj);
      }

      // 2. Extract area/neighborhood or landmark from address (e.g. "Fateh Sagar" from "Fateh Sagar, Udaipur")
      if (record && record.address) {
        const parts = String(record.address).split(",").map((s) => s.trim()).filter(Boolean);
        const area = parts[0] || "";
        if (area && area.toLowerCase() !== rawDest.toLowerCase() && area.toLowerCase() !== cleanSubj.toLowerCase()) {
          queries.push(`${area} ${rawDest}`.trim());
        }
      }

      // 3. Extract landmark from reason
      if (record && record.reason) {
        const text = String(record.reason);
        const m = text.match(/\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b/);
        if (m && m[1]) {
          const landmark = m[1].trim();
          if (landmark.toLowerCase() !== cleanSubj.toLowerCase() && landmark.toLowerCase() !== rawDest.toLowerCase()) {
            queries.push(`${landmark} ${rawDest}`.trim());
          }
        }
      }

      // 4. Strip generic travel noise words from subject
      const stripped = cleanSubj
        .replace(/\b(lakeside|promenade|walkway|walk|stroll|sightseeing|viewpoint|overlook|tour|visit|boating|boat ride|ride|cruise)\b/gi, "")
        .replace(/\s+/g, " ")
        .trim();
      if (stripped.length >= 3 && stripped.toLowerCase() !== cleanSubj.toLowerCase()) {
        queries.push(`${stripped} ${rawDest}`.trim());
      }

      // 5. Water / waterfront / lake context if mentioned
      if (/\b(lake|lakeside|promenade|waterfront|beach|harbour|harbor|river|bay)\b/i.test(cleanSubj) && rawDest) {
        queries.push(`${rawDest} lake`);
        queries.push(`${rawDest} waterfront`);
      }

      // 6. Broad destination landmarks and destination scenery fallback
      if (rawDest) {
        queries.push(`${rawDest} landmarks`);
        queries.push(rawDest);
      }

      for (const q of queries) {
        const cleanQ = q.trim();
        if (!cleanQ) continue;

        // Try Wikimedia Commons
        const wmUrl = await searchWikimediaPhoto(cleanQ, width);
        if (wmUrl) return wmUrl;

        // Try Wikipedia PageImages
        const wikiUrl = await searchWikipediaPhoto(cleanQ, width);
        if (wikiUrl) return wikiUrl;
      }

      return null;
    })();

    photoCache.set(cacheKey, lookup);
    return lookup;
  }

  function createPhoto(className, options) {
    const { subject, destination, alt, record = {}, caption, width = 960 } = options;
    const photo = el("div", className);
    const destName = destination || data.destination || payload.destination || "";
    const label = [subject, destName].filter(Boolean).join(" · ") || "Your destination";
    const fallback = generatedPhoto(label);
    const image = el("img", "travel-photo-image");
    image.alt = alt || label;
    image.loading = "lazy";
    image.decoding = "async";
    image.sizes = "(max-width: 640px) 100vw, 900px";
    image.src = fallback;

    let hasTriedFallback = false;
    image.addEventListener("error", () => {
      if (!hasTriedFallback && destName) {
        hasTriedFallback = true;
        findPhoto(destName, "", width).then((destSrc) => {
          if (destSrc && image.src !== destSrc) {
            image.src = destSrc;
            photo.classList.remove("is-fallback");
            return;
          }
          if (image.src !== fallback) image.src = fallback;
          photo.classList.add("is-fallback");
        });
        return;
      }
      if (image.src !== fallback) image.src = fallback;
      photo.classList.add("is-fallback");
    });

    photo.appendChild(image);
    if (caption) {
      const overlay = el("span", "photo-caption");
      overlay.textContent = caption;
      photo.appendChild(overlay);
    }

    photo.loadPhoto = async () => {
      const providedUrl = imageUrlFrom(record);
      const source = providedUrl || await findPhoto(subject, destName, width, record);
      if (source) {
        image.src = source;
        photo.classList.remove("is-fallback");
      } else {
        photo.classList.add("is-fallback");
      }
    };

    if (photoObserver) photoObserver.observe(photo);
    else photo.loadPhoto();
    return photo;
  }

  (function setHeaderPhoto() {
    const destination = data.destination || payload.destination || "";
    const header = document.querySelector(".ticket-header");
    if (!destination || !header) return;
    header.style.setProperty("--dest-photo", `url("${generatedPhoto(destination)}")`);
    findPhoto(destination, "", 1600).then((source) => {
      if (source) header.style.setProperty("--dest-photo", `url("${source}")`);
    });
  })();

  function shortTheme(theme) {
    if (!theme) return "";
    return theme.length > 22 ? theme.slice(0, 20) + "…" : theme;
  }

  function activateDay(idx) {
    tabsEl.querySelectorAll(".day-tab").forEach((t, i) => t.classList.toggle("is-active", i === idx));
    panelsEl.querySelectorAll(".day-panel").forEach((p, i) => p.classList.toggle("is-active", i === idx));
  }

  const MEAL_ICONS = { breakfast: "☕", cafe: "☕", coffee: "☕", brunch: "☕", lunch: "🍴", dinner: "🌙" };

  function renderStop(place) {
    const item = el("li", "stop-item");
    const placeName = place.name || "Untitled stop";
    item.appendChild(createPhoto("stop-photo", {
      subject: placeName,
      destination: data.destination || payload.destination,
      alt: `${placeName} in ${data.destination || payload.destination || "your destination"}`,
      record: place,
      caption: placeName,
    }));
    const body = el("div", "stop-body");
    if (place.time) body.appendChild(el("div", "stop-time", escapeHtml(place.time)));
    body.appendChild(el("div", "stop-name", escapeHtml(placeName)));

    const metaParts = [];
    if (place.duration) metaParts.push(place.duration);
    const cleanAddr = formatAddress(place.address, data.destination);
    if (cleanAddr) metaParts.push(cleanAddr);
    if (metaParts.length) body.appendChild(el("div", "stop-meta", escapeHtml(metaParts.join(" · "))));

    const reasonText = place.reason || (Array.isArray(place.reasons) ? place.reasons.join(" ") : "");
    if (reasonText) body.appendChild(el("div", "stop-reason", escapeHtml(reasonText)));

    const tagsRow = el("div", "stop-tags");
    if (place.is_free) tagsRow.appendChild(el("span", "tag-free", "Free"));
    const link = directionsLink(place.latitude, place.longitude, place.place_id, mapQuery(place.name, cleanAddr || place.address));
    if (link) {
      const a = el("a", "directions-btn", `${DIRECTIONS_ICON}<span>Directions</span>`);
      a.href = link;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      tagsRow.appendChild(a);
    }
    if (tagsRow.children.length) body.appendChild(tagsRow);

    item.appendChild(body);
    return item;
  }

  function renderDining(diningItems) {
    if (!Array.isArray(diningItems)) {
      diningItems = [diningItems];
    }
    const validItems = diningItems.filter((d) => d && typeof d === "object" && d.name);
    if (!validItems.length) return null;

    const wrap = el("div", "dining-block");
    wrap.appendChild(el("h3", null, "Dining & Cafés"));

    validItems.forEach((d) => {
      const entry = el("div", "dining-entry");
      const meal = d.meal || "Dining";
      const icon = MEAL_ICONS[String(meal).toLowerCase().trim()] || "🍴";
      entry.appendChild(el("span", "meal-tag", `${icon ? icon + " " : ""}${escapeHtml(meal)}`));
      entry.appendChild(el("span", "place-name", escapeHtml(d.name)));

      const cleanAddr = formatAddress(d.address, data.destination);
      if (cleanAddr) {
        entry.appendChild(el("span", "place-meta", escapeHtml(cleanAddr)));
      }

      const link = directionsLink(d.latitude, d.longitude, null, mapQuery(d.name, cleanAddr || d.address));
      if (link) {
        const a = el("a", "directions-btn", `${DIRECTIONS_ICON}<span>Directions</span>`);
        a.href = link;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        entry.appendChild(a);
      }

      if (d.reason) {
        entry.appendChild(el("div", "stop-reason", escapeHtml(d.reason)));
      }

      wrap.appendChild(entry);
    });

    return wrap;
  }

  const seenGlobalPlaces = new Set();
  function cleanPlaceKey(name) {
    return (name || "").toLowerCase().replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
  }

  days.forEach((day, idx) => {
    const tab = el("button", "day-tab" + (idx === 0 ? " is-active" : ""));
    tab.type = "button";
    tab.textContent = `Day ${day.day ?? idx + 1}`;
    tab.title = day.theme || "";
    tab.addEventListener("click", () => activateDay(idx));
    tabsEl.appendChild(tab);

    const panel = el("section", "day-panel" + (idx === 0 ? " is-active" : ""));
    panel.id = `day-panel-${idx}`;

    panel.appendChild(el("h2", null, escapeHtml(day.theme || `Day ${day.day ?? idx + 1}`)));

    if (day.summary) {
      panel.appendChild(el("p", "day-summary", escapeHtml(day.summary)));
    }
    if (day.fill_reason) {
      panel.appendChild(el("span", "fill-note", escapeHtml(day.fill_reason)));
    }

    const activities = Array.isArray(day.activities) ? day.activities : [];
    if (activities.length) {
      const list = el("ol", "stop-list");
      const seenDayStops = new Set();
      activities.forEach((place) => {
        if (!place || typeof place !== "object" || !place.name) return;
        const key = cleanPlaceKey(place.name);
        if (key && (seenDayStops.has(key) || seenGlobalPlaces.has(key))) {
          console.warn("[FRONTEND DEDUP] Skipped duplicate stop once suggested:", place.name);
          return;
        }
        if (key) {
          seenDayStops.add(key);
          seenGlobalPlaces.add(key);
        }
        list.appendChild(renderStop(place));
      });
      if (list.children.length > 0) {
        panel.appendChild(list);
      }
    }

    const diningItems = Array.isArray(day.dining) ? day.dining.filter((d) => d && typeof d === "object" && d.name) : [];
    const uniqueDining = [];
    diningItems.forEach((d) => {
      const key = cleanPlaceKey(d.name);
      if (key && seenGlobalPlaces.has(key)) {
        console.warn("[FRONTEND DEDUP] Skipped duplicate dining once suggested:", d.name);
        return;
      }
      if (key) seenGlobalPlaces.add(key);
      uniqueDining.push(d);
    });
    if (uniqueDining.length) {
      const diningNode = renderDining(uniqueDining);
      if (diningNode) panel.appendChild(diningNode);
    } else {
      panel.appendChild(el("p", "no-dining-note", "No verified dining recommendations found for this day."));
    }

    panelsEl.appendChild(panel);
  });

  if (!days.length) {
    panelsEl.appendChild(el("p", "day-summary", "No day-by-day plan was returned for this trip."));
  }

  // ---------- Flights ----------
  // ---------- Flights ----------
  const FLIGHT_STATUS_LABELS = {
    scheduled: "Scheduled",
    active: "In the air",
    landed: "Landed",
    cancelled: "Cancelled",
    incident: "Incident",
    diverted: "Diverted",
  };

  function formatFlightTime(iso) {
    if (!iso) return { time: "—", date: "" };
    const d = new Date(iso);
    if (isNaN(d)) return { time: iso, date: "" };
    return {
      time: d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }),
      date: d.toLocaleDateString(undefined, { day: "numeric", month: "short" }),
    };
  }

  function renderFlightEndpoint(label, endpoint) {
    const wrap = el("div", "flight-endpoint");
    const t = formatFlightTime(endpoint.scheduled);
    wrap.appendChild(el("span", "flight-endpoint-label", label));
    wrap.appendChild(el("span", "flight-endpoint-time", escapeHtml(t.time)));
    if (t.date) wrap.appendChild(el("span", "flight-endpoint-date", escapeHtml(t.date)));
    const metaParts = [];
    if (endpoint.iata) metaParts.push(endpoint.iata);
    if (endpoint.terminal) metaParts.push(`Terminal ${endpoint.terminal}`);
    if (endpoint.gate) metaParts.push(`Gate ${endpoint.gate}`);
    if (metaParts.length) wrap.appendChild(el("span", "flight-endpoint-meta", escapeHtml(metaParts.join(" · "))));
    if (typeof endpoint.delay_minutes === "number" && endpoint.delay_minutes > 0) {
      wrap.appendChild(el("span", "flight-delay", `+${endpoint.delay_minutes} min delay`));
    }
    return wrap;
  }

  function renderFlightCard(f) {
    const card = el("div", "flight-card");

    const header = el("div", "flight-card-header");
    header.appendChild(el("span", "flight-airline", escapeHtml(f.airline || "Unknown airline")));
    if (f.flight_number) header.appendChild(el("span", "flight-number", escapeHtml(f.flight_number)));
    const statusLabel = FLIGHT_STATUS_LABELS[f.status] || f.status || "Unknown";
    header.appendChild(el("span", `flight-status flight-status--${escapeHtml(f.status || "unknown")}`, escapeHtml(statusLabel)));
    const flightDestination = data.destination || payload.destination || "";
    const airlineName = f.airline && f.airline !== "Unknown airline" ? f.airline : "Aircraft";
    header.appendChild(createPhoto("flight-airline-photo", {
      subject: `${airlineName} aircraft`,
      destination: flightDestination,
      alt: `${airlineName} aircraft serving ${flightDestination || "your destination"}`,
      record: f,
      width: 320,
    }));
    card.appendChild(header);

    const route = el("div", "flight-route");
    route.appendChild(renderFlightEndpoint("Departs", f.departure || {}));
    route.appendChild(el("span", "flight-route-arrow", "→"));
    route.appendChild(renderFlightEndpoint("Arrives", f.arrival || {}));
    card.appendChild(route);

    // Direct redirection buttons for Google Flights and Skyscanner
    const actions = el("div", "flight-card-actions");

    let gUrl = f.google_flights_url;
    if (!gUrl) {
      const depIata = (f.departure && f.departure.iata) || "";
      const arrIata = (f.arrival && f.arrival.iata) || "";
      const airline = f.airline && f.airline !== "Unknown airline" ? f.airline : "";
      const flightNo = f.flight_number || "";
      const queryParts = ["flights"];
      if (depIata && arrIata) queryParts.push("from", depIata, "to", arrIata);
      else if (arrIata) queryParts.push("to", arrIata);
      if (airline) queryParts.push(airline);
      if (flightNo) queryParts.push(flightNo);
      gUrl = `https://www.google.com/travel/flights?q=${encodeURIComponent(queryParts.join(" "))}`;
    }

    const gBtn = el("a", "flight-action-btn flight-action-btn--google", "Check prices on Google Flights ↗");
    gBtn.href = gUrl;
    gBtn.target = "_blank";
    gBtn.rel = "noopener noreferrer";
    actions.appendChild(gBtn);

    let sUrl = f.skyscanner_url;
    if (!sUrl && f.departure && f.departure.iata && f.arrival && f.arrival.iata) {
      sUrl = `https://www.skyscanner.com/transport/flights/${encodeURIComponent(f.departure.iata.toLowerCase())}/${encodeURIComponent(f.arrival.iata.toLowerCase())}/`;
    }
    if (sUrl) {
      const sBtn = el("a", "flight-action-btn flight-action-btn--secondary", "Skyscanner ↗");
      sBtn.href = sUrl;
      sBtn.target = "_blank";
      sBtn.rel = "noopener noreferrer";
      actions.appendChild(sBtn);
    }

    card.appendChild(actions);

    return card;
  }

  // Renders live flight tracking status if available and always provides
  // clean, direct outbound search links to Google Flights and Skyscanner.
  const flightData = data.flights;
  const flightsSection = document.getElementById("flights-section");
  const flightsNoteEl = document.getElementById("flights-note");
  const flightsBodyEl = document.getElementById("flights-body");

  if (flightData && flightsSection) {
    flightsSection.hidden = false;
    flightsBodyEl.innerHTML = "";

    // Always show the flight note if provided
    if (flightData.note) {
      flightsNoteEl.textContent = flightData.note;
      flightsNoteEl.hidden = false;
    } else {
      flightsNoteEl.hidden = true;
    }

    // Prominent nearest departure hub alert badge when flight is routed from a nearby hub
    if (flightData.is_rerouted_hub && flightData.suggested_hub) {
      const hub = flightData.suggested_hub;
      const hubCard = el("div", "flights-hub-alert");

      const hubTitle = el("div", "flights-hub-alert-title");
      hubTitle.innerHTML = `<span>✈️</span> <strong>Nearest Departure Hub: ${escapeHtml(hub.city || "")} (${escapeHtml(hub.iata || "")})</strong>`;
      hubCard.appendChild(hubTitle);

      const hubDesc = el("div", "flights-hub-alert-desc");
      const origName = flightData.original_origin || payload.origin || "your departure city";
      const destName = data.destination || payload.destination || "";
      hubDesc.innerHTML = `No direct flights found from <strong>${escapeHtml(origName)}</strong> to <strong>${escapeHtml(destName)}</strong>. We verified your source first, and recommend departing from <strong>${escapeHtml(hub.city || "")}</strong> ${escapeHtml(hub.distance_note ? "— " + hub.distance_note : "")}.`;
      hubCard.appendChild(hubDesc);

      flightsBodyEl.appendChild(hubCard);
    }

    const list = Array.isArray(flightData.flights) ? flightData.flights : [];
    if (list.length) {
      list.forEach((f) => flightsBodyEl.appendChild(renderFlightCard(f)));
    } else {
      // Helpful empty state when no direct live flight tracking records are available
      const emptyCard = el("div", "flights-empty");
      if (flightData.is_rerouted_hub && flightData.suggested_hub) {
        emptyCard.textContent = `No scheduled live flights currently in tracking for ${flightData.suggested_hub.city} (${flightData.suggested_hub.iata}) → ${data.destination || payload.destination}. Check live fares and schedule options directly via Google Flights or Skyscanner below.`;
      } else if (!flightData.original_origin) {
        emptyCard.textContent = `Please specify a departure city above to view direct flights or nearest hub recommendations.`;
      } else {
        emptyCard.textContent = `No live direct flights currently tracked for ${flightData.original_origin} → ${data.destination || payload.destination}. Check real-time fares and connecting flight options below.`;
      }
      flightsBodyEl.appendChild(emptyCard);
    }

    // Outbound search card so travelers can check prices and book
    const dest = data.destination || payload.destination || "your destination";
    const origin = flightData.original_origin || payload.origin || "";
    const outboundCard = el("div", "flights-outbound-card");
    outboundCard.appendChild(createPhoto("flight-destination-photo", {
      subject: dest,
      destination: "",
      alt: `Destination view of ${dest}`,
      width: 420,
    }));
    const outboundInfo = el("div", "flights-outbound-info");

    let outboundTitleText = "";
    if (flightData.is_rerouted_hub && flightData.suggested_hub) {
      outboundTitleText = `Search flights from ${escapeHtml(flightData.suggested_hub.city)} or ${escapeHtml(origin)} to ${escapeHtml(dest)}`;
    } else if (origin) {
      outboundTitleText = `Search flights from ${escapeHtml(origin)} to ${escapeHtml(dest)}`;
    } else {
      outboundTitleText = `Search flights to ${escapeHtml(dest)}`;
    }

    outboundInfo.appendChild(el("div", "flights-outbound-title", outboundTitleText));
    outboundInfo.appendChild(
      el(
        "div",
        "flights-outbound-sub",
        "Compare real-time airfares, airlines, and schedules across major flight search platforms:"
      )
    );
    outboundCard.appendChild(outboundInfo);

    const actions = el("div", "flights-outbound-actions");
    const gUrl = flightData.google_flights_url || (origin ? `https://www.google.com/travel/flights?q=flights+from+${encodeURIComponent(origin)}+to+${encodeURIComponent(dest)}` : `https://www.google.com/travel/flights?q=flights+to+${encodeURIComponent(dest)}`);
    const sUrl = flightData.skyscanner_url || (origin ? `https://www.skyscanner.com/transport/flights/${encodeURIComponent(origin)}/${encodeURIComponent(dest)}/` : `https://www.skyscanner.com/transport/flights//to-${encodeURIComponent(dest)}/`);

    const gLabel = (flightData.is_rerouted_hub && flightData.suggested_hub) ? `Google Flights (${flightData.suggested_hub.city}) ↗` : "Google Flights ↗";
    const gBtn = el("a", "flights-btn flights-btn--google", gLabel);
    gBtn.href = gUrl;
    gBtn.target = "_blank";
    gBtn.rel = "noopener noreferrer";
    actions.appendChild(gBtn);

    const sLabel = (flightData.is_rerouted_hub && flightData.suggested_hub) ? `Skyscanner (${flightData.suggested_hub.city}) ↗` : "Skyscanner ↗";
    const sBtn = el("a", "flights-btn flights-btn--secondary", sLabel);
    sBtn.href = sUrl;
    sBtn.target = "_blank";
    sBtn.rel = "noopener noreferrer";
    actions.appendChild(sBtn);

    // If rerouted from a hub, also provide a button to search connecting flights from the original source city!
    if (flightData.is_rerouted_hub && flightData.original_origin && flightData.original_google_flights_url) {
      const origBtn = el("a", "flights-btn flights-btn--secondary", `Search from ${escapeHtml(flightData.original_origin)} ↗`);
      origBtn.href = flightData.original_google_flights_url;
      origBtn.target = "_blank";
      origBtn.rel = "noopener noreferrer";
      origBtn.title = `Search connecting flights from ${flightData.original_origin} on Google Flights`;
      actions.appendChild(origBtn);
    }

    outboundCard.appendChild(actions);
    flightsBodyEl.appendChild(outboundCard);
  }

  // ---------- Hotels ----------
  const hotelsSection = document.getElementById("hotels-section");
  const hotelEnforcedEl = document.getElementById("hotel-enforced");
  const hotelListEl = document.getElementById("hotel-list");

  function renderHotelCard(hotel) {
    const card = el("div", "hotel-card");

    const tierKey = hotel.tier ? String(hotel.tier).toLowerCase() : "moderate";
    card.appendChild(createPhoto(`hotel-photo hotel-photo--${tierKey}`, {
      subject: hotel.name || "Hotel",
      destination: data.destination || payload.destination,
      alt: `${hotel.name || "Hotel"} in ${data.destination || payload.destination || "your destination"}`,
      record: hotel,
      width: 900,
    }));

    const body = el("div", "hotel-card-body");

    const header = el("div", "hotel-card-header");
    if (hotel.tier) {
      const tierClean = String(hotel.tier).toLowerCase();
      header.appendChild(el("span", `hotel-tier-badge hotel-tier--${tierClean}`, `${escapeHtml(hotel.tier)} Stay`));
    }
    const scoreRating = typeof hotel.rating === "number" ? `★ ${hotel.rating.toFixed(1)}` : (typeof hotel.score === "number" ? `★ ${(hotel.score > 5 ? hotel.score / 2 : hotel.score).toFixed(1)}` : "");
    if (scoreRating) {
      header.appendChild(el("span", "hotel-rating", scoreRating));
    }
    if (header.children.length) body.appendChild(header);

    body.appendChild(el("div", "stop-name", escapeHtml(hotel.name || "Hotel")));

    const metaParts = [];
    if (hotel.price_per_night) metaParts.push(hotel.price_per_night);
    const cleanAddr = formatAddress(hotel.address, data.destination);
    if (cleanAddr) metaParts.push(cleanAddr);
    if (metaParts.length) body.appendChild(el("div", "stop-meta", escapeHtml(metaParts.join(" · "))));

    // If the model wrote best_for as several short clauses ("Central
    // location, modern amenities"), that reads better as tag chips like the
    // rest of the dashboard's badges. A single long sentence stays as prose
    // instead of being chopped into fake-looking fragments.
    if (hotel.best_for) {
      const clauses = String(hotel.best_for).split(",").map((c) => c.trim()).filter(Boolean);
      if (clauses.length >= 2 && clauses.every((c) => c.length <= 28)) {
        const tagsWrap = el("div", "hotel-tags");
        clauses.forEach((c) => tagsWrap.appendChild(el("span", "hotel-tag", escapeHtml(c))));
        body.appendChild(tagsWrap);
      } else {
        body.appendChild(el("div", "hotel-best-for", escapeHtml(hotel.best_for)));
      }
    }

    const bfText = (hotel.best_for || "").toLowerCase();
    if (!bfText.includes("breakfast")) {
      body.appendChild(el("div", "hotel-breakfast-note", "☕ Morning breakfast options available nearby or at hotel"));
    }

    const tagsRow = el("div", "stop-tags");
    const link = directionsLink(hotel.latitude, hotel.longitude, hotel.place_id, mapQuery(hotel.name, cleanAddr || hotel.address));
    if (link) {
      const a = el("a", "directions-btn", `${DIRECTIONS_ICON}<span>Directions</span>`);
      a.href = link; a.target = "_blank"; a.rel = "noopener noreferrer";
      tagsRow.appendChild(a);
    }
    if (hotel.website) {
      const a = el("a", "website-link", "Website");
      a.href = hotel.website; a.target = "_blank"; a.rel = "noopener noreferrer";
      tagsRow.appendChild(a);
    }
    if (tagsRow.children.length) body.appendChild(tagsRow);

    card.appendChild(body);
    return card;
  }

  const hotels = Array.isArray(data.hotel_recommendations) ? data.hotel_recommendations : [];
  if (data.budget_enforced_hotel && typeof data.budget_enforced_hotel === "object") {
    hotelEnforcedEl.hidden = false;
    hotelEnforcedEl.appendChild(el("span", "hotel-badge", "Selected to fit your budget"));
    hotelEnforcedEl.appendChild(renderHotelCard(data.budget_enforced_hotel));
  }
  if (hotels.length) {
    hotels.forEach((h) => hotelListEl.appendChild(renderHotelCard(h)));
  } else if (!data.budget_enforced_hotel) {
    hotelListEl.appendChild(el("p", "no-dining-note", "No verified hotel recommendations found for this search."));
  }
  hotelsSection.hidden = false;

  // ---------- Budget breakdown ----------
  const budgetSection = document.getElementById("budget-section");
  const budgetTableEl = document.getElementById("budget-table");
  const budgetDonutWrap = document.getElementById("budget-donut-wrap");
  const budgetDonutEl = document.getElementById("budget-donut");
  const budgetDonutCenterEl = document.getElementById("budget-donut-center");
  const budgetLegendEl = document.getElementById("budget-legend");

  // Fixed palette cycled per category, reusing the page's existing accent
  // colors first so the donut matches the rest of the UI rather than
  // introducing an unrelated color scheme.
  const DONUT_COLORS = ["#3B5FE0", "#D69A4E", "#35786E", "#B85138", "#8B5CF6", "#2E7D32"];

  function isMoneyKey(key) {
    return /cost|price|budget|total|spend|amount/i.test(key);
  }

  function budgetCategoryIcon(key) {
    const normalized = String(key).toLowerCase().replace(/[_-]+/g, " ");
    let path;
    if (/accommodation|hotel|lodging/.test(normalized)) {
      path = '<path d="M3 18v-7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v7M3 14h18M6 9V6a2 2 0 0 1 2-2h3v5M3 20v-2m18 2v-2"/>';
    } else if (/flight|airfare/.test(normalized)) {
      path = '<path d="m3 11 18-5-7 7-2 7-3-5-6-1 5-2-5-1Z"/>';
    } else if (/food|dining|meal/.test(normalized)) {
      path = '<path d="M4 3v7a3 3 0 0 0 6 0V3M7 3v18m9-18v18m0-18c3 2 4 5 4 8h-4"/>';
    } else if (/activit|attraction|excursion|experience/.test(normalized)) {
      path = '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2.2 4.8-4.8 2.2 2.2-4.8 4.8-2.2Z"/>';
    } else {
      return null;
    }

    const icon = el("span", "budget-category-icon");
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
    return icon;
  }

  function renderKeyValueTable(obj) {
    Object.entries(obj).forEach(([key, value]) => {
      if (value === null || value === undefined) return;
      if (typeof value === "object") return; // skip nested structures, keep the table flat and legible
      const row = el("div", "budget-row");
      const label = el("span", "label");
      const categoryIcon = budgetCategoryIcon(key);
      if (categoryIcon) label.appendChild(categoryIcon);
      label.appendChild(el("span", null, escapeHtml(prettyLabel(key))));
      row.appendChild(label);
      const displayValue = typeof value === "number" && isMoneyKey(key)
        ? formatMoney(value, currency)
        : escapeHtml(String(value));
      row.appendChild(el("span", "value", displayValue));
      budgetTableEl.appendChild(row);
    });
  }

  // Renders the donut purely from numeric entries in the same breakdown
  // object already used for the table below -- no separate data source, so
  // the chart and the table can never disagree with each other.
  function renderBudgetDonut(obj) {
    const entries = Object.entries(obj).filter(([, v]) => typeof v === "number" && v > 0);
    if (entries.length < 2) return; // a donut of one slice isn't informative

    const total = entries.reduce((sum, [, v]) => sum + v, 0);
    if (!total) return;

    let cumulativeDeg = 0;
    const gradientStops = [];
    entries.forEach(([, value], i) => {
      const color = DONUT_COLORS[i % DONUT_COLORS.length];
      const deg = (value / total) * 360;
      gradientStops.push(`${color} ${cumulativeDeg}deg ${cumulativeDeg + deg}deg`);
      cumulativeDeg += deg;
    });
    budgetDonutEl.style.background = `conic-gradient(${gradientStops.join(", ")})`;
    budgetDonutCenterEl.innerHTML = "";
    budgetDonutCenterEl.appendChild(el("span", "donut-center-value", formatMoney(total, currency)));
    budgetDonutCenterEl.appendChild(el("span", "donut-center-label", "Total cost"));

    entries.forEach(([key, value], i) => {
      const pct = Math.round((value / total) * 100);
      const row = el("li", "budget-legend-row");
      row.appendChild(el("span", "legend-dot")).style.background = DONUT_COLORS[i % DONUT_COLORS.length];
      row.appendChild(el("span", "legend-label", escapeHtml(prettyLabel(key))));
      row.appendChild(el("span", "legend-pct", `${pct}%`));
      row.appendChild(el("span", "legend-amount", formatMoney(value, currency)));
      budgetLegendEl.appendChild(row);
    });

    budgetDonutWrap.hidden = false;
  }

  const breakdownSource = { ...(data.cost_summary || {}), ...(data.budget_breakdown || {}) };
  if (Object.keys(breakdownSource).length) {
    renderBudgetDonut(breakdownSource);
    renderKeyValueTable(breakdownSource);
    budgetSection.hidden = false;
  }

  // ---------- COMPASS: chat companion (/api/chat) ----------
  // The first message carries a compact summary of this itinerary so COMPASS
  // has full context for this trip; subsequent messages reuse thread_id.
  // Replies are advice only -- nothing here edits the itinerary on screen.
  (function initAna() {
    function getAnaApiUrl() {
      // If loaded directly from the Render deployment, use relative path
      if (window.location.origin.includes("onrender.com")) {
        return "/api/chat";
      }
      return "https://yatrimind-travel-planner-ai-agent.onrender.com/api/chat";
    }

    const fab = document.getElementById("ana-fab");
    const panel = document.getElementById("ana-panel");
    const closeBtn = document.getElementById("ana-close");
    const log = document.getElementById("ana-log");
    const form = document.getElementById("ana-form");
    const input = document.getElementById("ana-input");
    const chips = document.getElementById("ana-chips");
    if (!fab || !panel || !log || !form || !input) return;

    let threadId = null;
    let primed = false;
    let busy = false;

    function setOpen(open) {
      panel.hidden = !open;
      fab.setAttribute("aria-expanded", String(open));
      if (open) input.focus();
    }
    fab.addEventListener("click", () => setOpen(panel.hidden));
    closeBtn.addEventListener("click", () => setOpen(false));

    function itineraryContext() {
      try {
        if (!data || typeof data !== "object") return "";
        const lines = [`I'm looking at a ${data.duration || ""} itinerary for ${data.destination || (payload && payload.destination) || "my trip"}, budget ${data.estimated_budget || "?"} ${currency}.`];
        const safeDays = Array.isArray(days) ? days : (data.days && Array.isArray(data.days) ? data.days : []);
        safeDays.forEach((d, i) => {
          if (!d) return;
          const acts = Array.isArray(d.activities) ? d.activities : [];
          const names = acts.map((a) => {
            if (!a) return "";
            if (typeof a === "string") return a;
            return a.name || "";
          }).filter(Boolean);
          lines.push(`Day ${d.day || i + 1} (${d.theme || "untitled"}): ${names.join(", ") || "no activities"}`);
        });
        return lines.join("\n");
      } catch (e) {
        console.warn("[COMPASS] Could not build itinerary context:", e);
        return "";
      }
    }

    const clearBtn = document.getElementById("ana-clear");
    const sendBtn = document.getElementById("ana-send-btn");

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        log.innerHTML = "";
        threadId = null;
        primed = false;
        addMsg("Chat refreshed! Ask me anything about this trip or general travel questions. ✨", "ana");
      });
    }

    function formatMarkdown(raw) {
      if (!raw) return "";
      let str = escapeHtml(raw);

      // Bold: **text** or __text__
      str = str.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      str = str.replace(/__(.+?)__/g, "<strong>$1</strong>");

      // Italic: *text* or _text_
      str = str.replace(/(^|[^\*])\*([^\*\n]+)\*([^\*]|$)/g, "$1<em>$2</em>$3");
      str = str.replace(/(^|[^_])_([^_\n]+)_([^_]|$)/g, "$1<em>$2</em>$3");

      // Inline code
      str = str.replace(/`([^`\n]+)`/g, "<code class='ana-inline-code'>$1</code>");

      // Process lists and paragraphs line by line
      const lines = str.split("\n");
      const out = [];
      let inList = false;

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) {
          if (inList) {
            out.push(inList === "ul" ? "</ul>" : "</ol>");
            inList = false;
          }
          continue;
        }

        const bulletMatch = line.match(/^[*\-•]\s+(.+)/);
        const numMatch = line.match(/^(\d+)[.)]\s+(.+)/);

        if (bulletMatch) {
          if (inList !== "ul") {
            if (inList) out.push(inList === "ul" ? "</ul>" : "</ol>");
            out.push("<ul class='ana-list'>");
            inList = "ul";
          }
          out.push(`<li>${bulletMatch[1]}</li>`);
        } else if (numMatch) {
          if (inList !== "ol") {
            if (inList) out.push(inList === "ul" ? "</ul>" : "</ol>");
            out.push("<ol class='ana-list'>");
            inList = "ol";
          }
          out.push(`<li>${numMatch[2]}</li>`);
        } else {
          if (inList) {
            out.push(inList === "ul" ? "</ul>" : "</ol>");
            inList = false;
          }
          out.push(`<p class="ana-p">${line}</p>`);
        }
      }

      if (inList) {
        out.push(inList === "ul" ? "</ul>" : "</ol>");
      }

      return out.join("");
    }

    function addMsg(content, who, isTyping = false) {
      const div = document.createElement("div");
      div.className = `ana-msg ana-msg--${who}${isTyping ? " ana-msg--typing" : ""}`;

      if (who === "ana") {
        const author = document.createElement("div");
        author.className = "ana-msg-author";
        author.innerHTML = `<span class="ana-msg-avatar">✦</span><span class="ana-msg-name">COMPASS${isTyping ? " is thinking…" : ""}</span>`;
        div.appendChild(author);

        if (isTyping) {
          const dots = document.createElement("div");
          dots.className = "ana-typing-dots";
          dots.innerHTML = `<span></span><span></span><span></span>`;
          div.appendChild(dots);
        } else {
          const body = document.createElement("div");
          body.className = "ana-msg-content";
          body.innerHTML = formatMarkdown(content);
          div.appendChild(body);
        }
      } else {
        const body = document.createElement("div");
        body.className = "ana-msg-content";
        body.textContent = content;
        div.appendChild(body);
      }

      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
      return div;
    }

    async function send(text) {
      const clean = (text || "").trim();
      if (!clean || busy) return;
      busy = true;
      if (sendBtn) sendBtn.disabled = true;

      addMsg(clean, "user");
      const typing = addMsg("", "ana", true);
      const payload = {
        message: clean,
        thread_id: threadId,
        itinerary_context: primed ? undefined : itineraryContext(),
      };
      primed = true;
      try {
        const endpoint = getAnaApiUrl();
        const res = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const body = await res.json();
        if (!res.ok || !body.success) throw new Error(body.error || `Request failed (${res.status})`);
        threadId = body.thread_id || threadId;
        typing.remove();
        addMsg(body.answer || "I don't have a suggestion for that yet.", "ana");
      } catch (err) {
        console.error("[COMPASS] Chat error:", err);
        typing.remove();
        addMsg("I couldn't reach COMPASS. Make sure the backend is running, then try again.", "ana");
      } finally {
        busy = false;
        if (sendBtn) sendBtn.disabled = false;
        input.focus();
      }
    }

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = input.value;
      input.value = "";
      send(v);
    });

    chips.addEventListener("click", (e) => {
      const chip = e.target.closest(".ana-chip");
      if (chip) {
        // Strip optional leading emoji when sending query
        const text = chip.textContent.replace(/^[\p{Emoji}\s]+/u, "").trim();
        send(text || chip.textContent.trim());
      }
    });
  })();
})();