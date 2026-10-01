import os
import sys
import json
import json5
import re
import time
import traceback
import urllib.parse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from groq import Groq
from pydantic import BaseModel

from backend.agent import run_travel_agent
from backend.ana_chat import compass_companion, ana_companion
from tools.tavily_tool import tavily_search, tavily_search_with_images
from tools.flight_tool import search_flights_structured, find_nearest_departure_hubs, resolve_location_to_iata

load_dotenv()

FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(
    title="YATRMIND TRAVEL AI AGENT",
    description="Multi-Agent Travel Planner with LangGraph and Interactive Frontend",
    version="1.0.0"
)

# Enable CORS for all local origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_private_network=True,
)

# Mount static directory for LangGraph chat interface
if (BASE_DIR / "static").exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static"
    )

# Mount frontend directory for environment-agnostic relative asset and page resolution
if FRONTEND_DIR.exists():
    app.mount(
        "/frontend",
        StaticFiles(directory=str(FRONTEND_DIR)),
        name="frontend"
    )

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


# ============================================================================
# Schemas
# ============================================================================

class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    itinerary_context: str | None = None


class TripPlanRequest(BaseModel):
    destination: str
    origin: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    travelers: int = 2
    budget: float = 50000.0
    currency: str = "INR"
    interests: list[str] = []
    trip_tier: str = "Standard"


# ============================================================================
# Frontend UI Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the ticket-style frontend if available, else LangGraph template."""
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/results", response_class=HTMLResponse)
@app.get("/results.html", response_class=HTMLResponse)
async def results_page():
    """Serve the results page for the ticket-style frontend."""
    results_file = FRONTEND_DIR / "results.html"
    if results_file.exists():
        return HTMLResponse(content=results_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Results page not found</h1>", status_code=404)


@app.get("/style.css")
async def style_css():
    css_file = FRONTEND_DIR / "style.css"
    if css_file.exists():
        return FileResponse(css_file, media_type="text/css")
    return JSONResponse(status_code=404, content={"error": "style.css not found"})


@app.get("/app.js")
async def app_js():
    js_file = FRONTEND_DIR / "app.js"
    if js_file.exists():
        return FileResponse(js_file, media_type="application/javascript")
    return JSONResponse(status_code=404, content={"error": "app.js not found"})


@app.get("/results.js")
async def results_js():
    js_file = FRONTEND_DIR / "results.js"
    if js_file.exists():
        return FileResponse(js_file, media_type="application/javascript")
    return JSONResponse(status_code=404, content={"error": "results.js not found"})


@app.get("/chat", response_class=HTMLResponse)
async def chat_ui(request: Request):
    """Serve the LangGraph conversational chat UI."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


# ============================================================================
# API Endpoints
# ============================================================================

def _gather_tavily_context(destination: str, trip_tier: str, num_days: int) -> dict:
    """
    Runs targeted Tavily searches to ground the itinerary in real, retrievable
    web content instead of asking the LLM to invent places from nothing. Each
    query is independent so one failing (rate limit, transient error) doesn't
    take the others down with it.

    Attraction coverage scales with trip length: a fixed 5-result search was
    fine for a 2-3 day trip but ran dry by day 3-4 on longer trips, forcing
    the "fewer activities rather than invented ones" safeguard to kick in
    and produce visibly thin days. Two differently-angled attraction queries
    (broad "top attractions" + "things to do / hidden gems") also reduce the
    duplicate-heavy results you get from asking one query for more results.
    Counts are budgeted to stay comfortably inside Groq's 7000 ITPM limit.

    A category coming back completely empty (Tavily transient failure, or a
    query that just didn't return results) used to fail silently: the LLM
    would still write names for that category with nothing to ground them
    against, and every single one would score 0.0 and get stripped later --
    producing a hotel/dining section that looked "fully hallucinated" when
    the real cause was an empty search result. This is now logged loudly and
    retried once per category, so it's diagnosable instead of silent.
    """
    attraction_count = min(14, max(6, num_days * 3))

    queries = {
        "attractions": (
            f"best tourist attractions and landmarks to visit in {destination}",
            attraction_count,
        ),
        "things_to_do": (
            f"things to do and hidden gems in {destination} for tourists",
            attraction_count,
        ),
        "breakfast_cafes": (
            f"best breakfast spots, cafes, and bakeries in {destination}",
            min(8, max(5, num_days * 2)),
        ),
        "dinner_restaurants": (
            f"best local restaurants, casual dining, and dinner places to eat in {destination}",
            min(10, max(6, num_days * 2)),
        ),
        "hotels": (
            f"best budget hotels, moderate hotels, and luxury comfort resorts to stay in {destination}",
            6,
        ),
    }
    context = {}
    for label, (query, count) in queries.items():
        result = None
        for attempt in range(2):
            try:
                res_data = tavily_search_with_images(query, max_results=count)
                if isinstance(res_data, dict):
                    text = res_data.get("text", "")
                    imgs = res_data.get("images", [])
                else:
                    text = str(res_data)
                    imgs = []
                if text and text.strip() and "Search is temporarily unavailable" not in text:
                    result = text
                    if imgs:
                        context.setdefault("_images", []).extend(imgs)
                    break
            except Exception as exc:
                print(f"[WARN] Tavily search failed for '{query}' (attempt {attempt + 1}/2): {exc!r}")
                result = None
        if result and result.strip():
            context[label] = result
        else:
            print(
                f"[WARN] Tavily returned NO usable results for '{label}' after retry -- "
                f"'{query}'. Anything the LLM proposes in this category will fail grounding "
                f"and be stripped, which will look like a fully-empty section, not a partial one."
            )
    return context


def _strip_ungrounded_coordinates(data: dict) -> dict:
    """
    This project has Tavily web search but no geocoding API -- any
    latitude/longitude the LLM outputs is a guess, not a verified location
    (the previous version of this function hardcoded New Delhi's coordinates
    for every destination, which is exactly the kind of silent wrong-data bug
    this guards against). Strip coordinates unconditionally so the frontend's
    Directions buttons always fall back to a text address/name query instead.
    """
    for day in data.get("days", []):
        for activity in day.get("activities", []) or []:
            if isinstance(activity, dict):
                activity.pop("latitude", None)
                activity.pop("longitude", None)
        for d in day.get("dining", []) or []:
            if isinstance(d, dict):
                d.pop("latitude", None)
                d.pop("longitude", None)
    for hotel in data.get("hotel_recommendations", []) or []:
        if isinstance(hotel, dict):
            hotel.pop("latitude", None)
            hotel.pop("longitude", None)
    return data


# ============================================================================
# Grounding verification — structural check, not just prompt instructions
# ============================================================================

def _extract_context_fragments(context: dict) -> list[str]:
    """
    Pulls every plausible proper-noun fragment out of the raw Tavily context
    text.  These are the names the LLM is *allowed* to use.  We extract:
      - Bold markdown titles  (**Name Here**)
      - Lines that look like numbered-list headings (the format tavily_search
        returns: "1. **Title**")
      - Any remaining capitalised multi-word runs (≥2 words) that aren't
        common English filler

    Returns lowercased fragments for case-insensitive matching later.
    """
    fragments: set[str] = set()
    filler = {
        "the", "a", "an", "of", "in", "at", "to", "for", "and", "or", "is",
        "are", "was", "were", "with", "on", "by", "from", "this", "that",
        "best", "top", "most", "search", "results", "unknown", "no",
    }

    for text in context.values():
        # Bold names:  **Some Place Name**
        for m in re.finditer(r"\*\*(.+?)\*\*", text):
            name = m.group(1).strip()
            if len(name) >= 3:
                fragments.add(name.lower())

        # Capitalised runs (e.g. "Burj Khalifa" inside a sentence)
        for m in re.finditer(r"(?:[A-Z][a-z]+(?:\s+(?:of|the|and|at|in|de|du|la|le|al|el)\s+)?)+[A-Z][a-z]+", text):
            name = m.group(0).strip()
            words = name.split()
            meaningful = [w for w in words if w.lower() not in filler]
            if len(meaningful) >= 2 and len(name) >= 5:
                fragments.add(name.lower())

    return list(fragments)


def _clean_name_for_matching(name: str) -> str:
    cleaned = re.sub(r"\([^)]*\)", "", name)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(cleaned.split()).lower()


def _best_fuzzy_score(name: str, fragments: list[str], raw_context: str = "") -> float:
    """
    Returns the highest SequenceMatcher ratio between `name` and any context
    fragment, using raw context substring checks, word overlap, and fuzzy fragment matching.
    """
    from difflib import SequenceMatcher
    if not fragments and not raw_context:
        return 0.0

    name_lower = name.lower().strip()
    clean = _clean_name_for_matching(name)
    best = 0.0

    # 1. Direct substring in raw context
    if raw_context:
        if name_lower in raw_context or (clean and clean in raw_context):
            return 0.95

    # 2. Word-level & phrase overlap in raw context
    if raw_context:
        _filler = {
            "the", "a", "an", "of", "in", "at", "to", "for", "and", "or",
            "on", "by", "from", "is", "are", "was", "with", "best", "top",
            "area", "city", "hotel", "restaurant", "resort", "tower", "mall",
        }
        # Generic descriptor words carry little identifying weight on their
        # own. A real place was getting stripped when the LLM phrased it as
        # "Al Fahidi Historical Neighbourhood" but the source text said
        # "Al Fahidi Historic District" -- same real place, but "historic/
        # historical" and "district/neighbourhood" mismatching dragged word
        # overlap down to 33% even though the actual identifying word
        # ("fahidi") matched perfectly. Setting these aside and requiring
        # only the non-generic ("core") words to match avoids punishing a
        # place for a generic-suffix phrasing difference that has nothing
        # to do with whether it's a real, correctly-identified place.
        _generic_descriptors = {
            "historic", "historical", "district", "neighborhood", "neighbourhood",
            "quarter", "old", "town", "village", "beach", "park", "garden",
            "gardens", "museum", "market", "souk", "square", "centre", "center",
            "avenue", "street", "road", "promenade", "walk", "waterfront",
            "sanctuary", "reserve", "conservation", "heritage", "site",
        }
        words = [w for w in clean.split() if len(w) >= 3 and w not in _filler]
        core_words = [w for w in words if w not in _generic_descriptors]
        if words:
            matched_words = [w for w in words if w in raw_context]
            if len(matched_words) == len(words):
                return 0.90
            if len(words) >= 2 and len(matched_words) / len(words) >= 0.65:
                best = max(best, 0.85)
            if core_words and all(w in raw_context for w in core_words):
                best = max(best, 0.80)

    # 3. Fragment fuzzy match
    for frag in fragments:
        r = SequenceMatcher(None, name_lower, frag).ratio()
        if r > best:
            best = r
        if clean:
            r_clean = SequenceMatcher(None, clean, frag).ratio()
            if r_clean > best:
                best = r_clean
        if frag in name_lower or name_lower in frag or (clean and (frag in clean or clean in frag)):
            best = max(best, 0.92)
        if best >= 0.92:
            return best

    return best


_GROUNDING_THRESHOLD = float(os.getenv("GROUNDING_THRESHOLD", "0.40"))

# Words that indicate a "dining" name is actually a non-food venue that
# happened to be mentioned in search results (a temple, a spa, a whole
# neighborhood) rather than a place to actually eat. Kept short and
# conservative -- deliberately excludes ambiguous terms like "market" or
# "mall" that legitimately host real restaurants.
_NON_DINING_NAME_MARKERS = (
    "temple", "mosque", "church", "synagogue", "gurudwara",
    "spa", "wellness", "museum", "sanctuary", "gallery",
    "neighborhood", "neighbourhood", "residence", "reserve",
)

_NON_DINNER_NAME_OR_REASON_MARKERS = (
    "bakery", "bakehouse", "bakeshop", "cake", "pastry", "pastries",
    "dessert", "desserts", "ice cream", "gelato", "donut", "doughnut",
    "pancake", "waffle",
)


def _looks_like_non_dining_name(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _NON_DINING_NAME_MARKERS)


def _looks_like_mismatched_dinner(name: str, reason: str = "") -> bool:
    lowered_name = name.lower()
    lowered_reason = reason.lower()
    for marker in _NON_DINNER_NAME_OR_REASON_MARKERS:
        if marker in lowered_name:
            return True
        if marker in lowered_reason and "dinner" not in lowered_reason and "restaurant" not in lowered_name:
            return True
    if ("breakfast" in lowered_name or "breakfast" in lowered_reason) and "dinner" not in lowered_reason and "restaurant" not in lowered_name:
        return True
    return False


def _verify_grounding(data: dict, context: dict) -> dict:
    """
    Post-generation structural check: every activity, dining spot, and hotel
    name the LLM returned is checked against the RIGHT category of Tavily
    search context -- not the whole context blob merged together.

    Dining is per-day again (each day's breakfast/lunch/dinner is meant to be
    near that day's own activities), but still checked only against the food
    search context specifically -- not the attractions context -- which is
    what stops a temple/spa/neighborhood name from passing as "grounded"
    just because it was mentioned somewhere in a different search.
    """
    activity_context = {k: v for k, v in context.items() if k in ("attractions", "things_to_do")}
    dining_context = {k: v for k, v in context.items() if k in ("breakfast_cafes", "dinner_restaurants")}
    hotel_context = {k: v for k, v in context.items() if k == "hotels"}

    activity_fragments = _extract_context_fragments(activity_context)
    activity_raw = "\n".join(activity_context.values()).lower()

    dining_fragments = _extract_context_fragments(dining_context)
    dining_raw = "\n".join(dining_context.values()).lower()

    hotel_fragments = _extract_context_fragments(hotel_context)
    hotel_raw = "\n".join(hotel_context.values()).lower()

    if not context:
        return data

    removed: list[str] = []

    for day in data.get("days", []):
        # --- Activities ---
        activities = day.get("activities")
        if isinstance(activities, list):
            verified = []
            for act in activities:
                if not isinstance(act, dict):
                    verified.append(act)
                    continue
                name = act.get("name", "")
                score = _best_fuzzy_score(name, activity_fragments, activity_raw)
                if score >= _GROUNDING_THRESHOLD:
                    verified.append(act)
                else:
                    removed.append(f"activity '{name}' (best score {score:.2f})")
            day["activities"] = verified

        # --- Dining (per-day: Breakfast, Lunch, Dinner) ---
        dining_list = day.get("dining") if isinstance(day.get("dining"), list) else []
        if not dining_context:
            # No point scoring against nothing -- that would zero out every
            # entry and look identical to "the model hallucinated all of
            # it." Leave the day's dining as-is; the missing-context warning
            # was already printed in _gather_tavily_context.
            pass
        else:
            verified_dining = []
            for d in dining_list:
                if not isinstance(d, dict):
                    continue
                name = d.get("name", "")
                if _looks_like_non_dining_name(name):
                    removed.append(f"dining '{name}' (looks like a non-dining venue, not a restaurant/cafe)")
                    continue
                meal = (d.get("meal") or "").lower().strip()
                reason = d.get("reason", "")
                if meal == "dinner" and _looks_like_mismatched_dinner(name, reason):
                    removed.append(f"dining '{name}' (bakery/dessert spot mismatched as Dinner)")
                    continue
                score = _best_fuzzy_score(name, dining_fragments, dining_raw)
                if score >= _GROUNDING_THRESHOLD:
                    verified_dining.append(d)
                else:
                    removed.append(f"dining '{name}' (best score {score:.2f} against food-search context)")
            day["dining"] = verified_dining
        day["restaurant"] = (day.get("dining") or [None])[0]

    # --- Hotels ---
    hotels = data.get("hotel_recommendations")
    if isinstance(hotels, list):
        if not hotel_context:
            pass
        else:
            verified_hotels = []
            for hotel in hotels:
                if not isinstance(hotel, dict):
                    verified_hotels.append(hotel)
                    continue
                name = hotel.get("name", "")
                score = _best_fuzzy_score(name, hotel_fragments, hotel_raw)
                if score >= _GROUNDING_THRESHOLD:
                    verified_hotels.append(hotel)
                else:
                    removed.append(f"hotel '{name}' (best score {score:.2f})")
            data["hotel_recommendations"] = verified_hotels

    if removed:
        print(f"[GROUNDING] Stripped {len(removed)} unverified name(s):")
        for r in removed:
            print(f"  - {r}")

    for day in data.get("days", []):
        activity_count = len(day.get("activities") or [])
        if activity_count < 2:
            print(
                f"[GROUNDING] Day {day.get('day')} ('{day.get('theme')}') has "
                f"{activity_count} activity(ies) after grounding verification."
            )

    return data


def _fix_unescaped_internal_quotes(text: str) -> str:
    """Escapes inner unescaped double quotes inside JSON string literals."""
    result = []
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '\\' and in_string:
            result.append(c)
            if i + 1 < n:
                result.append(text[i+1])
                i += 2
                continue
            i += 1
            continue

        if c == '"':
            if not in_string:
                in_string = True
                result.append(c)
            else:
                # Check if this quote is followed by structural JSON punctuation (closing string)
                rest = text[i+1:].lstrip()
                if rest == "" or rest[0] in {",", "}", "]", ":"}:
                    in_string = False
                    result.append(c)
                else:
                    # Unescaped quote inside string value
                    result.append('\\"')
            i += 1
            continue

        result.append(c)
        i += 1
    return "".join(result)


def _parse_and_repair_json(raw: str) -> dict:
    """Multi-stage resilient parser for LLM-generated JSON."""
    cleaned = raw.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1:
        raise RuntimeError("The model did not return a JSON object.")
    if end != -1 and end > start:
        cleaned = cleaned[start:end + 1]
    else:
        cleaned = cleaned[start:]

    # 1. Direct standard json.loads
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. json5 parser (handles trailing commas, unquoted keys, single quotes, comments)
    try:
        return json5.loads(cleaned)
    except Exception:
        pass

    # 3. Strip trailing commas before closing braces/brackets
    no_trailing = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(no_trailing)
    except Exception:
        pass
    try:
        return json5.loads(no_trailing)
    except Exception:
        pass

    # 4. Fix unescaped internal quotes inside strings
    fixed_quotes = _fix_unescaped_internal_quotes(no_trailing)
    try:
        return json.loads(fixed_quotes)
    except Exception:
        pass
    try:
        return json5.loads(fixed_quotes)
    except Exception:
        pass

    # 5. Handle potential cutoff / incomplete trailing object or unclosed braces
    truncated = fixed_quotes
    if truncated.count('"') % 2 != 0:
        truncated += '"'
    open_curly = truncated.count("{") - truncated.count("}")
    open_square = truncated.count("[") - truncated.count("]")
    if open_curly > 0 or open_square > 0:
        salvaged = truncated + ("]" * max(0, open_square)) + ("}" * max(0, open_curly))
        try:
            return json5.loads(salvaged)
        except Exception:
            pass
        try:
            return json.loads(salvaged)
        except Exception:
            pass

    # If all stages fail, raise clear error
    try:
        return json.loads(cleaned)
    except Exception as exc:
        raise RuntimeError(f"The model returned malformed JSON: {exc}") from exc


def _dedupe_days(data: dict) -> dict:
    """
    Groq occasionally repeats a day wholesale on a given generation --
    same theme, same activities, word-for-word -- especially when the
    Tavily context is dominated by one strong cluster of attractions
    (e.g. a desert-safari day duplicating itself). This is non-deterministic
    LLM behavior, not something the prompt alone can fully rule out, so this
    is a structural guarantee that a duplicate day can never reach the
    frontend even if it slips past the model.

    A day's "signature" is its normalized theme plus the sorted set of its
    activity names -- two days need to match on both to count as the same
    day, so two genuinely different days that happen to share a theme name
    are not merged.
    """
    days = data.get("days", [])
    if not isinstance(days, list) or len(days) < 2:
        return data

    seen_signatures = set()
    deduped = []
    removed_count = 0

    for day in days:
        if not isinstance(day, dict):
            deduped.append(day)
            continue

        theme = (day.get("theme") or "").strip().lower()
        activity_names = tuple(sorted(
            (a.get("name") or "").strip().lower()
            for a in (day.get("activities") or [])
            if isinstance(a, dict)
        ))
        signature = (theme, activity_names)

        if signature in seen_signatures and any(activity_names):
            removed_count += 1
            continue

        seen_signatures.add(signature)
        deduped.append(day)

    if removed_count:
        print(f"[DEDUP] Removed {removed_count} duplicate day(s) from Groq's output.")
        # Renumber sequentially so "day" stays 1..N with no gaps after removal.
        for i, day in enumerate(deduped, start=1):
            if isinstance(day, dict):
                day["day"] = i

    data["days"] = deduped
    return data


_GENERIC_TRAVEL_WORDS = {
    "lake", "lakeside", "river", "riverside", "sea", "seaside", "bay", "ocean", "waterfront",
    "promenade", "walkway", "walk", "stroll", "tour", "visit", "ride", "boat", "boating", "cruise",
    "palace", "fort", "fortress", "castle", "haveli", "mahal", "palaces",
    "temple", "mandir", "shrine", "mosque", "masjid", "church", "cathedral", "gurudwara",
    "museum", "gallery", "exhibit", "exhibition", "memorial", "monument",
    "garden", "gardens", "park", "parks", "sanctuary", "reserve", "safari", "zoo",
    "market", "bazaar", "souk", "mall", "centre", "center", "square", "plaza",
    "ghat", "gate", "bridge", "tower", "viewpoint", "point", "overlook", "hill", "hills",
    "street", "road", "drive", "avenue", "lane", "boulevard", "district", "quarter",
    "beach", "island", "heritage", "historic", "historical", "cultural",
    "cafe", "café", "bistro", "restaurant", "eatery", "dining", "hotel", "resort",
    "the", "a", "an", "and", "or", "of", "in", "at", "by", "for", "on", "to", "with",
    "old", "new", "city", "town", "best", "top", "royal"
}

_BASIC_STOPWORDS = {"the", "a", "an", "and", "or", "of", "in", "at", "by", "for", "on", "to", "with"}


def _normalize_place_name(name: str) -> str:
    cleaned = re.sub(r"\([^)]*\)", "", name or "")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned).lower()
    return " ".join(cleaned.split())


def _extract_core_tokens(name: str) -> set[str]:
    norm = _normalize_place_name(name)
    words = norm.split()
    core = {w for w in words if w not in _GENERIC_TRAVEL_WORDS and len(w) >= 3}
    if not core:
        core = {w for w in words if len(w) >= 3 and w not in _BASIC_STOPWORDS}
    return core


def _is_mostly_generic(name: str) -> bool:
    norm = _normalize_place_name(name)
    words = norm.split()
    non_generic = [w for w in words if w not in _GENERIC_TRAVEL_WORDS and len(w) >= 3]
    return len(non_generic) == 0


def _are_places_duplicate(item1: dict | str, item2: dict | str) -> bool:
    """
    Returns True if two items represent the same place, landmark, or venue.
    Checks exact matches, substrings, shared core proper nouns (e.g. 'Fateh Sagar'),
    token overlap, difflib sequence similarity, and address/reason cross-location.
    """
    from difflib import SequenceMatcher

    name1 = item1.get("name", "") if isinstance(item1, dict) else str(item1)
    name2 = item2.get("name", "") if isinstance(item2, dict) else str(item2)

    norm1 = _normalize_place_name(name1)
    norm2 = _normalize_place_name(name2)
    if not norm1 or not norm2:
        return False

    # 1. Exact normalized match
    if norm1 == norm2:
        return True

    # 2. Direct substring match
    if len(norm1) >= 4 and len(norm2) >= 4:
        is_dining1 = isinstance(item1, dict) and "meal" in item1
        is_dining2 = isinstance(item2, dict) and "meal" in item2
        if norm1 in norm2 or norm2 in norm1:
            if not (is_dining1 and is_dining2 and "restaurant" in norm1 and "restaurant" in norm2 and _extract_core_tokens(name1) != _extract_core_tokens(name2)):
                return True

    # 3. Core token set comparison
    core1 = _extract_core_tokens(name1)
    core2 = _extract_core_tokens(name2)
    if core1 and core2:
        if core1 == core2:
            return True
        if core1.issubset(core2) or core2.issubset(core1):
            return True
        overlap = len(core1 & core2)
        min_len = min(len(core1), len(core2))
        if min_len > 0 and (overlap / min_len) >= 0.65:
            return True

    # 4. Fuzzy SequenceMatcher ratio on core strings
    core_str1 = " ".join(sorted(core1)) if core1 else norm1
    core_str2 = " ".join(sorted(core2)) if core2 else norm2
    if len(core_str1) >= 4 and len(core_str2) >= 4:
        ratio = SequenceMatcher(None, core_str1, core_str2).ratio()
        if ratio >= 0.78:
            return True

    # 5. Address & Reason cross-check (if one place is located at or describes the other place)
    addr1 = _normalize_place_name(item1.get("address", "")) if isinstance(item1, dict) else ""
    reason1 = _normalize_place_name(item1.get("reason", "")) if isinstance(item1, dict) else ""
    addr2 = _normalize_place_name(item2.get("address", "")) if isinstance(item2, dict) else ""
    reason2 = _normalize_place_name(item2.get("reason", "")) if isinstance(item2, dict) else ""

    text1 = f"{addr1} {reason1}"
    text2 = f"{addr2} {reason2}"

    is_activity1 = isinstance(item1, dict) and "meal" not in item1
    is_activity2 = isinstance(item2, dict) and "meal" not in item2

    if (is_activity1 and is_activity2) or _is_mostly_generic(name2) or _is_mostly_generic(name1):
        if core1 and len(core1) >= 2 and all(w in text2 for w in core1):
            return True
        if core2 and len(core2) >= 2 and all(w in text1 for w in core2):
            return True

    return False


def _filter_context_for_day(raw_context: str, excluded_records: set | list) -> str:
    """
    Filters out search result snippets from raw_context that match any already-used venue,
    ensuring the LLM does not see previously visited places in subsequent day prompts.
    """
    if not excluded_records or not raw_context:
        return raw_context

    filtered_lines = []
    skip_current_block = False

    for line in raw_context.splitlines():
        # Check if line is the start of a numbered item e.g. "1. **Place Name**"
        match = re.match(r"^\s*\d+\.\s*\*\*([^*]+)\*\*", line)
        if match:
            item_title = match.group(1).strip()
            is_dup = any(_are_places_duplicate(item_title, ex) for ex in excluded_records)
            skip_current_block = is_dup
            if is_dup:
                continue
        elif line.strip().startswith(tuple(f"{i}." for i in range(1, 25))):
            is_dup = any(_are_places_duplicate(line, ex) for ex in excluded_records)
            skip_current_block = is_dup
            if is_dup:
                continue

        if skip_current_block:
            continue
        filtered_lines.append(line)

    return "\n".join(filtered_lines)


def _extract_available_attractions(context: dict, destination: str) -> list[dict]:
    """
    Pulls clean candidate tourist sights from the Tavily search context for backfilling
    whenever duplicates are removed.
    """
    raw_texts = [
        context.get("attractions", ""),
        context.get("things_to_do", "")
    ]
    candidates = []
    seen = set()

    for text in raw_texts:
        if not text:
            continue
        matches = re.findall(r"\d+\.\s*\*\*([^*]+)\*\*(?:\s*\n\s*https?://[^\n]+)?\s*\n\s*([^\n]+)", text)
        for title, snippet in matches:
            clean_title = title.strip()
            clean_title = re.sub(r"^\d+[\.\s\-]+", "", clean_title).strip()
            lowered = clean_title.lower()
            if any(p in lowered for p in ["things to do", "best places", "top attractions", "tourist guide", "itinerary", "visit in"]):
                continue
            if len(clean_title) < 3 or lowered in seen:
                continue
            seen.add(lowered)

            reason = snippet.strip()
            if len(reason) > 130:
                reason = reason[:127].rsplit(" ", 1)[0] + "..."
            if not reason.endswith("."):
                reason += "."

            candidates.append({
                "name": clean_title,
                "address": f"{clean_title}, {destination}",
                "reason": reason or f"Iconic landmark in {destination}.",
                "duration": "2 hours",
                "is_free": False,
            })

    return candidates


def _dedupe_all_places_across_trip(data: dict, context: dict, destination: str) -> dict:
    """
    Enforces absolute zero repetition across the entire itinerary:
    - No activity or place is repeated across any day once suggested.
    - No dining venue duplicates an activity or previously suggested dining.
    - If removing duplicates leaves a day with fewer than 2 activities, it is
      backfilled from grounded, unused attractions in the search context.
    """
    seen_places: list[dict] = []
    attraction_candidates = _extract_available_attractions(context, destination)
    candidate_idx = 0
    removed_activities = 0
    removed_dining = 0

    tavily_images = context.get("_images") or []

    for day in data.get("days", []):
        day_num = day.get("day", 1)
        kept_activities = []
        for act in (day.get("activities") or []):
            if not isinstance(act, dict) or not act.get("name"):
                continue
            is_dup = False
            matched_name = ""
            for prev in seen_places:
                if _are_places_duplicate(act, prev):
                    is_dup = True
                    matched_name = prev.get("name", "")
                    break
            if not is_dup:
                for earlier in kept_activities:
                    if _are_places_duplicate(act, earlier):
                        is_dup = True
                        matched_name = earlier.get("name", "")
                        break
            if is_dup:
                removed_activities += 1
                print(f"[DEDUP] Dropped duplicate activity '{act.get('name')}' on Day {day_num} (matches '{matched_name}')")
                continue

            # Attach verified Tavily image if available and none present
            if not act.get("image_url") and tavily_images:
                act_norm = _normalize_place_name(act.get("name", ""))
                act_core = _extract_core_tokens(act.get("name", ""))
                for img in tavily_images:
                    img_low = img.lower()
                    if act_core and all(w in img_low for w in act_core):
                        act["image_url"] = img
                        break

            kept_activities.append(act)
            seen_places.append(act)

        # Backfill if fewer than 2 activities remain
        while len(kept_activities) < 2 and candidate_idx < len(attraction_candidates):
            cand = attraction_candidates[candidate_idx]
            candidate_idx += 1
            if any(_are_places_duplicate(cand, prev) for prev in seen_places) or any(_are_places_duplicate(cand, a) for a in kept_activities):
                continue
            time_slot = "10:00 AM" if not kept_activities else ("02:30 PM" if len(kept_activities) == 1 else "05:00 PM")
            cand_copy = dict(cand)
            cand_copy["time"] = time_slot
            kept_activities.append(cand_copy)
            seen_places.append(cand_copy)
            print(f"[DEDUP] Backfilled unique attraction '{cand['name']}' on Day {day_num}")

        day["activities"] = kept_activities

        # Deduplicate dining against all seen places (activities + earlier dining)
        kept_dining = []
        for d in (day.get("dining") or []):
            if not isinstance(d, dict) or not d.get("name"):
                continue
            is_dup = False
            for prev in seen_places:
                if _are_places_duplicate(d, prev):
                    is_dup = True
                    break
            if not is_dup:
                for earlier in kept_dining:
                    if _are_places_duplicate(d, earlier):
                        is_dup = True
                        break
            if is_dup:
                removed_dining += 1
                print(f"[DEDUP] Dropped duplicate dining venue '{d.get('name')}' on Day {day_num}")
                continue

            kept_dining.append(d)
            seen_places.append(d)

        day["dining"] = kept_dining
        day["restaurant"] = kept_dining[0] if kept_dining else None

    if removed_activities or removed_dining:
        print(f"[DEDUP] Summary: Removed {removed_activities} repeated activity(ies) and {removed_dining} repeated dining venue(s) across trip.")

    return data


def _clean_addresses(data: dict, destination: str) -> dict:
    """
    Cleans up any malformed address strings (e.g. 'null, Dubai', ', Dubai',
    'undefined, Dubai') by omitting null/empty segments cleanly.
    Also enriches bare city dining addresses (e.g. 'Dubai') with the
    neighborhood of that day's scheduled activities.
    """
    def _clean_addr(addr: str | None) -> str:
        if not addr or not isinstance(addr, str):
            return destination
        parts = [p.strip() for p in addr.split(",") if p.strip()]
        valid = []
        for p in parts:
            if p.lower() in {"null", "none", "undefined", "n/a", "unknown"}:
                continue
            if p not in valid:
                valid.append(p)
        if not valid:
            return destination
        return ", ".join(valid)

    for day in data.get("days", []):
        day_area = ""
        for act in day.get("activities", []) or []:
            if isinstance(act, dict) and "address" in act:
                act["address"] = _clean_addr(act.get("address"))
                if not day_area and act["address"].lower() != destination.lower():
                    parts = [p.strip() for p in act["address"].split(",") if p.strip()]
                    if len(parts) >= 2:
                        day_area = parts[0]

        for d in day.get("dining", []) or []:
            if isinstance(d, dict) and "address" in d:
                cleaned = _clean_addr(d.get("address"))
                if cleaned.strip().lower() == destination.strip().lower() and day_area:
                    cleaned = f"{day_area}, {destination}"
                d["address"] = cleaned

        if isinstance(day.get("restaurant"), dict):
            r_cleaned = _clean_addr(day["restaurant"].get("address"))
            if r_cleaned.strip().lower() == destination.strip().lower() and day_area:
                r_cleaned = f"{day_area}, {destination}"
            day["restaurant"]["address"] = r_cleaned

    for h in data.get("hotel_recommendations", []) or []:
        if isinstance(h, dict) and "address" in h:
            h["address"] = _clean_addr(h.get("address"))
    return data


def _flag_thin_days(data: dict) -> dict:
    """
    Ensures days with 0 activities have a friendly exploration note,
    without slapping negative apology banners on normal vacation days.
    """
    for day in data.get("days", []):
        count = len(day.get("activities") or [])
        if count == 0:
            day["fill_status"] = "empty"
            day["fill_reason"] = "Leisure & free exploration time"
        else:
            day.pop("fill_reason", None)
            day.pop("fill_status", None)
    return data


def _call_groq_json(client: Groq, model_name: str, messages: list[dict], max_tokens: int, temperature: float = 0.3) -> dict:
    """Helper to execute Groq chat completions with JSON mode and rate-limit backoff."""
    kwargs = {
        "model": model_name,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    resp = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except Exception as groq_err:
            err_str = str(groq_err)
            if "response_format" in err_str.lower():
                kwargs.pop("response_format", None)
                continue
            if ("429" in err_str or "rate_limit" in err_str.lower()) and attempt < 2:
                print(f"[RETRY] Groq OTPM rate limit hit, backing off 12s (attempt {attempt + 1}/3)...")
                time.sleep(12)
                continue
            raise groq_err

    raw = resp.choices[0].message.content.strip()
    return _parse_and_repair_json(raw)


def _generate_hotel_recommendations(
    client: Groq,
    model_name: str,
    req: "TripPlanRequest",
    hotels_context: str,
    day_areas: list[str],
) -> list[dict]:
    """
    Dedicated function for hotel recommendations, kept separate from theme/
    budget generation and from day activities. Since a traveler books ONE
    place to stay for the whole trip (not a different hotel per day), "near
    the places you recommend" is interpreted here as centrally located
    relative to the *areas visited across the whole itinerary* -- the actual
    districts the generated days ended up in, passed in as `day_areas` -- 
    rather than picking a different hotel per day, which isn't how trip
    lodging actually works. This is still text/area matching, not real
    geocoded distance, since this project has no geocoding API.
    """
    if not hotels_context.strip():
        print("[WARN] No hotels search context available -- skipping hotel recommendations.")
        return []

    areas_str = ", ".join(dict.fromkeys(a for a in day_areas if a)) or "the main tourist districts"

    hotel_prompt = f"""=== HOTELS SEARCH RESULTS ===
{hotels_context}

Recommend hotels for {req.travelers} traveler(s) visiting {req.destination}.
Budget: {req.budget} {req.currency}. Trip tier: {req.trip_tier}.
This trip's planned activities are centered around these areas: {areas_str}.

Instructions:
- Recommend 3 to 4 distinct real hotels from HOTELS SEARCH RESULTS: covering Budget, Moderate, and Comfort/Luxury tiers.
- Each hotel MUST be a genuine lodging property (hotel, resort, suites) -- NEVER a restaurant, cafe, or tourist attraction.
- Prefer hotels located in or near the areas listed above, so the traveler isn't far from where the itinerary actually takes them. If nothing suitable exists in those areas, pick the most central option available in the search results.
- Include realistic price_per_night in {req.currency}, rating, and a specific neighborhood/district address.

Output strictly valid JSON:
{{
  "hotel_recommendations": [
    {{"name": "real hotel name", "tier": "Budget", "price_per_night": "{req.currency} amount", "address": "district, {req.destination}", "rating": 4.2, "best_for": "Affordable, clean, well-connected stay"}},
    {{"name": "real hotel name", "tier": "Moderate", "price_per_night": "{req.currency} amount", "address": "district, {req.destination}", "rating": 4.5, "best_for": "Central location, modern amenities"}},
    {{"name": "real hotel name", "tier": "Comfort", "price_per_night": "{req.currency} amount", "address": "district, {req.destination}", "rating": 4.8, "best_for": "Luxury stay, premium skyline views"}}
  ]
}}
Return only JSON."""

    try:
        result = _call_groq_json(
            client=client,
            model_name=model_name,
            messages=[
                {"role": "system", "content": "You are a hotel-booking specialist. Output strictly valid JSON."},
                {"role": "user", "content": hotel_prompt},
            ],
            max_tokens=480,
            temperature=0.3,
        )
        return result.get("hotel_recommendations") or []
    except Exception as exc:
        print(f"[WARN] Failed generating hotel recommendations: {exc!r}")
        return []


def _generate_dining_for_day(
    client: Groq,
    model_name: str,
    req: "TripPlanRequest",
    day_idx: int,
    num_days: int,
    theme: str,
    day_activities: list[dict],
    breakfast_context: str,
    dinner_context: str,
    excluded_names: set[str],
) -> list[dict]:
    """
    Dedicated function for dining, called once per day right after that
    day's activities are generated, so it can be pointed at the actual
    areas/places just generated for that day -- e.g. "near Downtown Dubai,
    Dubai Marina" -- instead of guessing blind or being a disconnected
    trip-wide list. Still text/area matching against Tavily search results,
    not real geocoded distance (no geocoding API in this project), but this
    is the best-effort version of "near where the traveler actually is that
    day" that the current architecture can support.
    """
    if not breakfast_context.strip() and not dinner_context.strip():
        print(f"[WARN] No dining search context available for Day {day_idx} -- skipping dining.")
        return []

    activity_lines = "\n".join(
        f"- {a.get('name', '')} ({a.get('address', '')})"
        for a in day_activities
        if isinstance(a, dict) and a.get("name")
    ) or "(no specific activities generated for this day)"

    excluded_str = ", ".join(sorted(excluded_names)) if excluded_names else "None yet"

    dining_prompt = f"""=== BREAKFAST CAFES SEARCH RESULTS ===
{breakfast_context}

=== DINNER RESTAURANTS SEARCH RESULTS ===
{dinner_context}

This is Day {day_idx} of {num_days} in {req.destination}. Theme: {theme}
Today's planned activities are:
{activity_lines}

Recommend Breakfast/Cafe, Lunch, and Dinner spots for THIS DAY specifically, chosen to be
near the activity areas listed above -- not just anywhere in {req.destination}.
Places already used on earlier days or as activities (DO NOT REUSE ANY OF THESE): {excluded_str}

Rules:
- CRITICAL: ZERO REPETITION. NEVER reuse, repeat, or revisit ANY dining venue, cafe, bakery, or restaurant already suggested on earlier days or visited as an activity.
- Recommend distinct dining places: 1 Breakfast/Cafe spot, 1 Lunch spot, and 1 Dinner restaurant.
- Places MUST be genuine eateries (cafes, bakeries, bistros, restaurants) -- NEVER an attraction, landmark, museum, or hotel.
- Breakfast: a cafe or breakfast spot from BREAKFAST CAFES SEARCH RESULTS, ideally near today's morning activity.
- Lunch: a casual lunch spot or bistro from either search section, ideally near today's activities.
- Dinner: an evening restaurant from DINNER RESTAURANTS SEARCH RESULTS, ideally near today's activities. NEVER pick a bakery, cake shop, dessert parlor, or breakfast-only spot for Dinner.
- NEVER reuse any name from the already-used list or any variation thereof.
- Every address MUST include the neighborhood/district (e.g. "Downtown Dubai, {req.destination}", "Dubai Marina, {req.destination}"). NEVER output just "{req.destination}".
- Keep each reason strictly to 1 concise factual sentence.

Output strictly valid JSON:
{{
  "dining": [
    {{"name": "real cafe or breakfast spot", "meal": "Breakfast", "address": "district, {req.destination}", "reason": "concise factual note"}},
    {{"name": "real casual lunch spot", "meal": "Lunch", "address": "district, {req.destination}", "reason": "concise factual note"}},
    {{"name": "real evening restaurant", "meal": "Dinner", "address": "district, {req.destination}", "reason": "concise factual note"}}
  ]
}}
Return only JSON."""

    try:
        result = _call_groq_json(
            client=client,
            model_name=model_name,
            messages=[
                {"role": "system", "content": "You are a local food guide. Output strictly valid JSON."},
                {"role": "user", "content": dining_prompt},
            ],
            max_tokens=450,
            temperature=0.3,
        )
        raw_dining = result.get("dining") or []
        verified_dining = []
        for d in raw_dining:
            if not isinstance(d, dict) or not d.get("name"):
                continue
            if any(_are_places_duplicate(d, prev) for prev in excluded_names):
                print(f"[DEDUP] Dropped duplicate dining venue '{d.get('name')}' on Day {day_idx}")
                continue
            if any(_are_places_duplicate(d, earlier) for earlier in verified_dining):
                print(f"[DEDUP] Dropped same-day duplicate dining venue '{d.get('name')}' on Day {day_idx}")
                continue
            verified_dining.append(d)
        return verified_dining
    except Exception as exc:
        print(f"[WARN] Failed generating dining for day {day_idx}: {exc!r}")
        return []


def _generate_single_day(
    client: Groq,
    model_name: str,
    req: "TripPlanRequest",
    day_idx: int,
    num_days: int,
    theme_raw: str,
    attractions_context: str,
    breakfast_context: str,
    dinner_context: str,
    used_venues: set,
    used_dining_names: set,
    day_areas: list,
) -> dict:
    """
    Generates one day's activities, then that day's dining, tied to the
    activities just generated. Pulled out into its own function so the
    calling loop can wrap a single day's worth of work in one try/except.
    Enforces strict place deduplication so no sight or venue is ever repeated.
    """
    theme = re.sub(r"^Day\s*\d+\s*[:\-]\s*", "", theme_raw, flags=re.I).strip()
    all_used = used_venues | used_dining_names
    excluded_str = ", ".join(sorted(all_used)) if all_used else "None yet"

    # Filter attractions context so snippets of previously used venues are not shown
    day_attractions_context = _filter_context_for_day(attractions_context, all_used)

    day_prompt = f"""=== ATTRACTIONS & THINGS TO DO ===
{day_attractions_context}

Plan Day {day_idx} of {num_days} in {req.destination}.
Theme: {theme}
Travelers: {req.travelers}. Budget: {req.budget} {req.currency}.
Venues already used in earlier days or dining (DO NOT REUSE ANY OF THESE): {excluded_str}

CRITICAL RULES -- ABSOLUTE ZERO DUPLICATION:
- NEVER suggest, repeat, or revisit any place, attraction, sight, lake, palace, garden, viewpoint, or venue that was already suggested in earlier days or dining.
- DO NOT suggest variations or different activities at the same place (for example: if "Fateh Sagar Lake" or "Fateh Sagar" was already used, do NOT suggest "Fateh Sagar", "Lakeside Promenade", "Fateh Sagar Boating", "Fateh Sagar Walkway", or any activity near Fateh Sagar again!).
- Every stop across the entire trip must be a completely NEW and DIFFERENT destination.
- 2 to 3 distinct real activities from ATTRACTIONS / THINGS TO DO. Prefer 3 when the search results support it; only use 2 if a third genuinely good match isn't available.
- Every place to visit MUST be a genuine tourist sight, cultural landmark, museum, or scenic attraction -- NEVER a restaurant, cafe, bakery, or hotel.
- Every address MUST include the neighborhood/district (e.g. "Downtown Dubai, {req.destination}", "Dubai Marina, {req.destination}", "Al Karama, {req.destination}", "Jumeirah, {req.destination}"). NEVER output just "{req.destination}".
- Keep each reason strictly to 1 concise factual sentence.
- NEVER reuse any name from the already-used list.

Output strictly valid JSON:
{{
  "day": {day_idx},
  "theme": "{theme}",
  "summary": "One concise sentence overview for Day {day_idx}.",
  "activities": [
    {{"name": "real place name", "time": "10:00 AM", "duration": "2 hours", "address": "district, {req.destination}", "reason": "concise factual reason", "is_free": false}},
    {{"name": "real place name", "time": "01:30 PM", "duration": "2 hours", "address": "district, {req.destination}", "reason": "concise factual reason", "is_free": false}},
    {{"name": "real place name", "time": "04:30 PM", "duration": "2 hours", "address": "district, {req.destination}", "reason": "concise factual reason", "is_free": false}}
  ]
}}
Return only JSON."""

    try:
        day_obj = _call_groq_json(
            client=client,
            model_name=model_name,
            messages=[
                {"role": "system", "content": "You are a day-by-day travel itinerary builder. Output strictly valid JSON."},
                {"role": "user", "content": day_prompt},
            ],
            max_tokens=520,
            temperature=0.3,
        )
    except Exception as exc:
        print(f"[WARN] Failed generating day {day_idx}: {exc!r}")
        day_obj = {
            "day": day_idx,
            "theme": theme,
            "summary": f"Exploration and leisure in {req.destination}.",
            "activities": [],
        }

    raw_activities = day_obj.get("activities") or []
    verified_activities = []
    for act in raw_activities:
        if not isinstance(act, dict) or not act.get("name"):
            continue
        is_dup = any(_are_places_duplicate(act, prev) for prev in (used_venues | used_dining_names))
        if not is_dup:
            is_dup = any(_are_places_duplicate(act, earlier) for earlier in verified_activities)
        if is_dup:
            print(f"[DEDUP] Dropped duplicate activity '{act.get('name')}' on Day {day_idx}")
            continue
        verified_activities.append(act)
        used_venues.add(act["name"])

    # Backfill if fewer than 2 activities
    if len(verified_activities) < 2:
        candidate_pool = _extract_available_attractions({"attractions": attractions_context}, req.destination)
        for cand in candidate_pool:
            if not any(_are_places_duplicate(cand, prev) for prev in (used_venues | used_dining_names)) and not any(_are_places_duplicate(cand, a) for a in verified_activities):
                cand_item = dict(cand)
                cand_item["time"] = "02:30 PM" if len(verified_activities) == 1 else "10:00 AM"
                verified_activities.append(cand_item)
                used_venues.add(cand_item["name"])
                print(f"[DEDUP] Backfilled unique attraction '{cand_item['name']}' on Day {day_idx}")
                if len(verified_activities) >= 2:
                    break

    day_obj["activities"] = verified_activities
    for act in verified_activities:
        addr = act.get("address")
        if isinstance(addr, str) and addr.strip():
            try:
                area = addr.split(",")[0].strip()
                if area and area.lower() != req.destination.lower():
                    day_areas.append(area)
            except Exception as exc:
                print(f"[WARN] Could not parse area from address '{addr!r}' on day {day_idx}: {exc!r}")

    time.sleep(2)

    day_obj["dining"] = _generate_dining_for_day(
        client=client,
        model_name=model_name,
        req=req,
        day_idx=day_idx,
        num_days=num_days,
        theme=theme,
        day_activities=verified_activities,
        breakfast_context=breakfast_context,
        dinner_context=dinner_context,
        excluded_names=used_dining_names | used_venues,
    )
    for d in day_obj["dining"]:
        if isinstance(d, dict) and d.get("name"):
            used_dining_names.add(d["name"])

    return day_obj


def generate_structured_itinerary(req: TripPlanRequest) -> dict:
    """
    Builds a day-by-day itinerary grounded in real Tavily search results.

    Three separate concerns, three separate functions/call sequences:
    1. Trip overview: day themes and budget breakdown only (no hotels here).
    2. Per-day activities, followed immediately by a per-day call to
       _generate_dining_for_day() -- dining is tied to THAT day's actual
       generated activities/areas, not a blind trip-wide list and not mixed
       into the activities prompt itself.
    3. _generate_hotel_recommendations() runs once, after all days are
       generated, using the real areas the itinerary ended up covering so
       the hotel pick is centrally located relative to the actual trip
       rather than a generic guess made before any day existed.

    All of this is still text/area-name matching against Tavily search
    results -- this project has no geocoding API, so "near today's
    activities" is a best-effort instruction to the LLM, not a verified
    distance calculation.
    """
    num_days = 3
    if req.start_date and req.end_date:
        try:
            d1 = datetime.strptime(req.start_date, "%Y-%m-%d")
            d2 = datetime.strptime(req.end_date, "%Y-%m-%d")
            diff = (d2 - d1).days
            if diff > 0:
                num_days = min(diff, 14)
        except Exception:
            pass

    duration_str = f"{num_days} day{'s' if num_days > 1 else ''}, {max(1, num_days - 1)} night{'s' if max(1, num_days - 1) > 1 else ''}"

    context = _gather_tavily_context(req.destination, req.trip_tier, num_days)
    if not context:
        raise RuntimeError(
            f"Couldn't find verified information about {req.destination} via web search. "
            "Check that TAVILY_API_KEY is set and valid in your .env file, then try again."
        )

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("GROQ_API_KEY is missing -- cannot generate an itinerary.")

    model_name = os.getenv("GROQ_PLANNER_MODEL", os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"))
    client = Groq(api_key=groq_key)

    # -------------------------------------------------------------------------
    # 1. Overview Call: Day Themes & Budget Breakdown (hotels are separate now)
    # -------------------------------------------------------------------------
    attractions_context = (context.get("attractions", "") + "\n" + context.get("things_to_do", ""))[:3000]

    overview_prompt = f"""=== ATTRACTIONS SEARCH RESULTS ===
{attractions_context}

Plan the trip overview for {req.travelers} traveler(s) visiting {req.destination} for {num_days} days.
Budget: {req.budget} {req.currency}. Trip tier: {req.trip_tier}.

Instructions:
1. Provide a list of {num_days} distinct, descriptive day themes (one per day).
2. Calculate budget breakdown for {req.budget} {req.currency}.

Output strictly valid JSON:
{{
  "themes": ["Day 1 theme", "Day 2 theme", "Day 3 theme"],
  "budget_breakdown": {{
    "accommodation": {round(req.budget * 0.40)},
    "flights": {round(req.budget * 0.30)},
    "food_dining": {round(req.budget * 0.20)},
    "activities": {round(req.budget * 0.10)}
  }}
}}
Return only JSON."""

    try:
        overview = _call_groq_json(
            client=client,
            model_name=model_name,
            messages=[
                {"role": "system", "content": "You are a travel coordinator. Output strictly valid JSON."},
                {"role": "user", "content": overview_prompt},
            ],
            max_tokens=350,
            temperature=0.3,
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to generate trip overview ({model_name}): {exc}") from exc

    themes = overview.get("themes") or []
    default_themes = [
        "Iconic Landmarks & City Views",
        "Waterfront, Marina & Modern Living",
        "Heritage, Culture & Local Traditions",
        "Hidden Gems & Natural Wonders",
    ]
    while len(themes) < num_days:
        themes.append(default_themes[len(themes) % len(default_themes)])

    # -------------------------------------------------------------------------
    # 2. Per-Day Chunked Generation: activities, then that day's dining.
    # -------------------------------------------------------------------------
    breakfast_context = context.get("breakfast_cafes", "")[:3000]
    dinner_context = context.get("dinner_restaurants", "")[:3000]

    days: list[dict] = []
    used_venues: set[str] = set()
    used_dining_names: set[str] = set()
    day_areas: list[str] = []

    for day_idx in range(1, num_days + 1):
        if day_idx > 1:
            # Small proactive gap between calls -- each day now costs TWO
            # Groq calls (activities + dining) instead of one, so pacing
            # matters even more for staying clear of the OTPM ceiling.
            time.sleep(2)

        try:
            day_obj = _generate_single_day(
                client=client,
                model_name=model_name,
                req=req,
                day_idx=day_idx,
                num_days=num_days,
                theme_raw=themes[day_idx - 1] if day_idx - 1 < len(themes) else f"Day {day_idx} Exploration",
                attractions_context=attractions_context,
                breakfast_context=breakfast_context,
                dinner_context=dinner_context,
                used_venues=used_venues,
                used_dining_names=used_dining_names,
                day_areas=day_areas,
            )
        except Exception as exc:
            # This is the actual architectural fix: previously, ANY uncaught
            # exception anywhere in a day's processing (a malformed field
            # shape from the LLM, a bug in the area-tracking code, anything)
            # would propagate all the way out of generate_structured_itinerary
            # and abort every remaining day silently -- Day 1 would look
            # fine (already appended before the crash) while Days 2-4 simply
            # never existed, with no visible error anywhere. One bad day can
            # no longer take down the rest of the trip.
            print(f"[ERROR] Day {day_idx} generation crashed unexpectedly, skipping this day only: {exc!r}")
            traceback.print_exc()
            day_obj = {
                "day": day_idx,
                "theme": f"Day {day_idx} Exploration",
                "summary": f"Exploration and leisure in {req.destination}.",
                "activities": [],
                "dining": [],
            }

        days.append(day_obj)

    # -------------------------------------------------------------------------
    # 3. Hotel recommendations -- separate function, run once the real areas
    #    covered by the itinerary are known.
    # -------------------------------------------------------------------------
    time.sleep(2)
    hotels_context = context.get("hotels", "")[:3000]
    hotel_recommendations = _generate_hotel_recommendations(
        client=client,
        model_name=model_name,
        req=req,
        hotels_context=hotels_context,
        day_areas=day_areas,
    )

    data = {
        "destination": req.destination,
        "duration": duration_str,
        "estimated_budget": req.budget,
        "currency": req.currency,
        "days": days,
        "hotel_recommendations": hotel_recommendations,
        "budget_breakdown": overview.get("budget_breakdown") or {
            "accommodation": round(req.budget * 0.40),
            "flights": round(req.budget * 0.30),
            "food_dining": round(req.budget * 0.20),
            "activities": round(req.budget * 0.10),
        },
    }

    data = _strip_ungrounded_coordinates(data)
    data = _verify_grounding(data, context)
    data = _dedupe_all_places_across_trip(data, context, req.destination)
    data = _dedupe_days(data)
    data = _flag_thin_days(data)
    data = _clean_addresses(data, req.destination)
    return data


def _lookup_flights(req: TripPlanRequest) -> dict | None:
    """
    Looks up live flights based on the user's source and destination:
    1. If source to destination flights are found: recommend them.
    2. If no flights are found from the source to destination:
       - Reminds traveler that source to destination was searched.
       - Dynamically recommends the nearest major departure hub (e.g. Delhi for Jaipur, Mumbai for Hyderabad)
         WITHOUT any hardcoded values.
       - Recommends flights departing from that nearest hub.
    3. If no departure origin was given: reminds the user to provide a source for direct/nearest hub routing.
    """
    origin_clean = (req.origin or "").strip()
    dest_clean = (req.destination or "").strip()

    dest_iata = resolve_location_to_iata(dest_clean)

    # Base destination URLs
    base_google_flights_url = f"https://www.google.com/travel/flights?q={urllib.parse.quote_plus(f'flights to {dest_clean}')}"
    base_skyscanner_url = (
        f"https://www.skyscanner.com/transport/flights//to-{dest_iata.lower()}/"
        if dest_iata
        else f"https://www.skyscanner.com/transport/flights//to-{dest_clean.lower()}/"
    )

    if not origin_clean:
        note = (
            f"Specify your departure city above to get direct flight options or nearest airport hub recommendations to {dest_clean}."
        )
        return {
            "note": note,
            "route_info": f"Flights to {dest_clean}",
            "flights": [],
            "google_flights_url": base_google_flights_url,
            "skyscanner_url": base_skyscanner_url,
            "suggested_hub": None,
            "is_rerouted_hub": False,
            "original_origin": "",
            "original_google_flights_url": None,
            "original_skyscanner_url": None,
        }

    orig_iata = resolve_location_to_iata(origin_clean)
    original_google_flights_url = f"https://www.google.com/travel/flights?q={urllib.parse.quote_plus(f'flights from {origin_clean} to {dest_clean}')}"
    original_skyscanner_url = (
        f"https://www.skyscanner.com/transport/flights/{orig_iata.lower()}/{dest_iata.lower()}/"
        if (orig_iata and dest_iata)
        else f"https://www.skyscanner.com/transport/flights/{origin_clean.lower()}/{dest_clean.lower()}/"
    )

    google_flights_url = original_google_flights_url
    skyscanner_url = original_skyscanner_url
    route_info = f"Flights from {origin_clean} to {dest_clean}"
    suggested_hub = None
    is_rerouted_hub = False
    flights = []
    raw_list = []

    has_api_key = bool(os.getenv("AVIATIONSTACK_API_KEY"))

    if has_api_key:
        try:
            # 1. Search directly from source to destination first
            query_str = f"flights from {origin_clean} to {dest_clean}"
            result = search_flights_structured(query_str, limit=12)
            if isinstance(result, dict) and "flights" in result:
                raw_list = result.get("flights") or []
        except Exception as exc:
            print(f"[WARN] Live flight lookup error ({origin_clean} -> {dest_clean}): {exc}")

    if raw_list:
        route_info = f"Flights from {origin_clean} to {dest_clean}"
        note = (
            f"Suggested flights from {origin_clean} to {dest_clean} via live AviationStack. "
            "Click 'Check prices on Google Flights' on any flight to view real-time fares and seat options."
        )
    else:
        # NO flights found from source to destination!
        # Do not recommend random flights arriving from other parts of the world.
        # Dynamically find the nearest major source station / departure hub without hardcoded values:
        hubs = find_nearest_departure_hubs(origin_clean, dest_clean)
        found_hub_flights = False

        if has_api_key and hubs:
            for hub in hubs:
                h_iata = hub.get("iata")
                h_city = hub.get("city")
                if not h_iata:
                    continue
                try:
                    hub_res = search_flights_structured(f"flights from {h_iata} to {dest_clean}", limit=12)
                    hub_flights = hub_res.get("flights") or [] if isinstance(hub_res, dict) else []
                    if hub_flights:
                        raw_list = hub_flights
                        suggested_hub = hub
                        is_rerouted_hub = True
                        found_hub_flights = True
                        route_info = f"Flights from {h_city} ({h_iata}) to {dest_clean}"
                        note = (
                            f"No direct flights found from {origin_clean} to {dest_clean}. "
                            f"We searched your departure city first. Recommending flights from the nearest major hub: "
                            f"{h_city} ({h_iata}) - {hub.get('distance_note', '')}."
                        )
                        google_flights_url = f"https://www.google.com/travel/flights?q={urllib.parse.quote_plus(f'flights from {h_city} to {dest_clean}')}"
                        skyscanner_url = (
                            f"https://www.skyscanner.com/transport/flights/{h_iata.lower()}/{dest_iata.lower()}/"
                            if dest_iata
                            else f"https://www.skyscanner.com/transport/flights/{h_iata.lower()}/"
                        )
                        break
                except Exception as exc:
                    print(f"[WARN] Hub flight lookup error for {h_city} ({h_iata}): {exc}")

        if not found_hub_flights:
            if hubs:
                top_hub = hubs[0]
                suggested_hub = top_hub
                is_rerouted_hub = True
                route_info = f"Flights from {top_hub['city']} ({top_hub['iata']}) to {dest_clean}"
                note = (
                    f"No direct flights found from {origin_clean} to {dest_clean}. "
                    f"We searched your departure city first. We recommend flying from the nearest major departure hub: "
                    f"{top_hub['city']} ({top_hub['iata']}) - {top_hub.get('distance_note', '')}. "
                    "Use the links below to view live airline schedules, real-time ticket prices, and connecting routes."
                )
                hub_city_name = top_hub.get("city", "")
                hub_query = f"flights from {hub_city_name} to {dest_clean}"
                google_flights_url = f"https://www.google.com/travel/flights?q={urllib.parse.quote_plus(hub_query)}"
                top_hub_iata = top_hub.get("iata", "").lower()
                skyscanner_url = (
                    f"https://www.skyscanner.com/transport/flights/{top_hub_iata}/{dest_iata.lower()}/"
                    if (dest_iata and top_hub_iata)
                    else f"https://www.skyscanner.com/transport/flights/{top_hub_iata}/"
                )
            else:
                note = (
                    f"No direct flights found from {origin_clean} to {dest_clean}. "
                    "Use the links below to check live fares and connecting flight options."
                )

    if raw_list:
        active_scheduled = [f for f in raw_list if f.get("status") in ("scheduled", "active")]
        landed = [f for f in raw_list if f.get("status") not in ("scheduled", "active")]
        sorted_pool = active_scheduled + landed

        selected = []
        seen_airlines = set()
        for f in sorted_pool:
            air = f.get("airline") or ""
            if air and air not in seen_airlines:
                seen_airlines.add(air)
                selected.append(f)
            if len(selected) == 3:
                break

        if len(selected) < 3:
            for f in sorted_pool:
                if f not in selected:
                    selected.append(f)
                if len(selected) == 3:
                    break

        eff_dep = (suggested_hub.get("iata") if (is_rerouted_hub and suggested_hub) else origin_clean) or ""
        for f in selected:
            dep_code = (f.get("departure") or {}).get("iata") or eff_dep
            arr_code = (f.get("arrival") or {}).get("iata") or dest_clean
            air = f.get("airline") or ""
            f_no = f.get("flight_number") or ""

            parts = ["flights"]
            if dep_code and arr_code:
                parts.extend(["from", dep_code, "to", arr_code])
            elif arr_code:
                parts.extend(["to", arr_code])
            if air and air != "Unknown airline":
                parts.append(air)
            if f_no:
                parts.append(f_no)

            f_query = " ".join(parts)
            f["google_flights_url"] = f"https://www.google.com/travel/flights?q={urllib.parse.quote_plus(f_query)}"
            if dep_code and arr_code:
                f["skyscanner_url"] = f"https://www.skyscanner.com/transport/flights/{dep_code.lower()}/{arr_code.lower()}/"
            else:
                f["skyscanner_url"] = f"https://www.skyscanner.com/transport/flights//to-{arr_code.lower()}/"

        flights = selected[:3]

    return {
        "note": note,
        "route_info": route_info,
        "flights": flights,
        "google_flights_url": google_flights_url,
        "skyscanner_url": skyscanner_url,
        "suggested_hub": suggested_hub,
        "is_rerouted_hub": is_rerouted_hub,
        "original_origin": origin_clean,
        "original_google_flights_url": original_google_flights_url,
        "original_skyscanner_url": original_skyscanner_url,
    }


@app.post("/api/plan-trip")
@app.post("/api/generate-itinerary")
async def plan_trip_endpoint(trip_request: TripPlanRequest):
    """Main endpoint for the ticket-style frontend."""
    if not trip_request.destination or len(trip_request.destination.strip()) < 2:
        return JSONResponse(status_code=400, content={"message": "Destination is required."})

    try:
        data = await run_in_threadpool(generate_structured_itinerary, trip_request)
    except RuntimeError as e:
        # Honest failure: no verified data available, so no fabricated
        # itinerary is returned in its place.
        return JSONResponse(status_code=422, content={"message": str(e)})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"message": f"Error generating itinerary: {str(e)}"}
        )

    data["flights"] = await run_in_threadpool(_lookup_flights, trip_request)
    return JSONResponse(content=data)


@app.post("/api/travel")
async def travel_planner(request_data: TravelRequest):
    """LangGraph multi-agent endpoint."""
    try:
        user_message = request_data.message.strip()

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Message cannot be empty."
                }
            )

        result = await run_in_threadpool(
            run_travel_agent,
            user_input=user_message,
            thread_id=request_data.thread_id
        )

        return JSONResponse(
            content={
                "success": True,
                "thread_id": result["thread_id"],
                "answer": result["answer"],
                "flight_results": result["flight_results"],
                "hotel_results": result["hotel_results"],
                "itinerary": result["itinerary"],
                "llm_calls": result["llm_calls"],
            }
        )

    except Exception as e:
        print("ERROR:", e)
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )


@app.post("/api/chat")
async def chat_companion(request_data: ChatRequest):
    """COMPASS AI travel companion chat endpoint with session memory."""
    try:
        user_message = request_data.message.strip()

        if not user_message:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Message cannot be empty."
                }
            )

        answer, thread_id = await run_in_threadpool(
            compass_companion.ask,
            user_message=user_message,
            thread_id=request_data.thread_id,
            itinerary_context=request_data.itinerary_context,
        )

        return JSONResponse(
            content={
                "success": True,
                "thread_id": thread_id,
                "answer": answer,
            }
        )

    except Exception as e:
        print("ERROR (COMPASS Chat):", e)
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "YATRMIND TRAVEL AI AGENT API is running",
        "endpoints": ["/api/plan-trip", "/api/travel", "/api/chat", "/health"]
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


# Mount frontend directory at root for direct browser access
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend_root")


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )