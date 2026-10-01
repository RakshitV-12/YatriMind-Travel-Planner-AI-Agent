import os 
import re 
import json
import math
import certifi
import urllib.parse
import airportsdata
import pycountry
import requests
from dotenv import load_dotenv

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

API_KEY = os.getenv("AVIATIONSTACK_API_KEY")

# Default origin when user says only destination, e.g. "Japan trip"
DEFAULT_ORIGIN_IATA = os.getenv("DEFAULT_ORIGIN_IATA") or "DEL"


BASE_URL = "https://api.aviationstack.com/v1/flights"


AIRPORTS = airportsdata.load("IATA")



COUNTRY_ALIASES = {
    "usa": "US",
    "u.s.a": "US",
    "u.s.": "US",
    "america": "US",
    "united states": "US",
    "uk": "GB",
    "u.k.": "GB",
    "britain": "GB",
    "england": "GB",
    "uae": "AE",
    "dubai": "AE",
    "south korea": "KR",
    "korea": "KR",
    "russia": "RU",
    "vietnam": "VN",
    "bangladesh": "BD",
    "india": "IN",
    "japan": "JP",
    "china": "CN",
    "singapore": "SG",
    "malaysia": "MY",
    "thailand": "TH",
    "indonesia": "ID",
    "nepal": "NP",
    "qatar": "QA",
    "saudi arabia": "SA",
    "turkey": "TR",
    "canada": "CA",
    "australia": "AU",
    "germany": "DE",
    "france": "FR",
    "italy": "IT",
    "spain": "ES",
}


# Preferred main airport for country-level search
COUNTRY_MAIN_AIRPORT = {
    "BD": "DAC",
    "IN": "DEL",
    "JP": "NRT",
    "US": "JFK",
    "GB": "LHR",
    "AE": "DXB",
    "SG": "SIN",
    "MY": "KUL",
    "TH": "BKK",
    "ID": "CGK",
    "CN": "PEK",
    "KR": "ICN",
    "NP": "KTM",
    "QA": "DOH",
    "SA": "JED",
    "TR": "IST",
    "CA": "YYZ",
    "AU": "SYD",
    "DE": "FRA",
    "FR": "CDG",
    "IT": "FCO",
    "ES": "MAD",
}




CITY_MAIN_AIRPORT = {
    "dhaka": "DAC",
    "delhi": "DEL",
    "new delhi": "DEL",
    "mumbai": "BOM",
    "kolkata": "CCU",
    "chennai": "MAA",
    "bangalore": "BLR",
    "bengaluru": "BLR",
    "tokyo": "NRT",
    "osaka": "KIX",
    "kyoto": "KIX",
    "new york": "JFK",
    "london": "LHR",
    "dubai": "DXB",
    "singapore": "SIN",
    "kuala lumpur": "KUL",
    "bangkok": "BKK",
    "doha": "DOH",
    "istanbul": "IST",
    "toronto": "YYZ",
    "sydney": "SYD",
    "paris": "CDG",
    "rome": "FCO",
    "madrid": "MAD",
    "frankfurt": "FRA",
}


def clean_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    stop_words = [
        "flight", "flights", "ticket", "tickets", "trip", "travel",
        "plan", "complete", "days", "day", "including", "hotel",
        "hotels", "sightseeing", "under", "budget", "info", "information"
    ]
    words = [w for w in text.split() if w not in stop_words]
    return " ".join(words).strip()



def country_name_to_code(text: str):
    text = clean_text(text)

    if text in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[text]

    try:
        country = pycountry.countries.lookup(text)
        return country.alpha_2
    except LookupError:
        pass

    # Detect country name inside longer text
    for country in pycountry.countries:
        country_name = country.name.lower()
        if country_name in text:
            return country.alpha_2

    for alias, code in COUNTRY_ALIASES.items():
        if alias in text:
            return code

    return None



def airport_country_matches(airport: dict, country_code: str) -> bool:
    airport_country = str(airport.get("country", "")).upper().strip()

    if airport_country == country_code:
        return True

    try:
        country = pycountry.countries.get(alpha_2=country_code)
        if country and airport_country.lower() == country.name.lower():
            return True
    except Exception:
        pass

    return False




def get_best_airport_for_country(country_code: str):
    preferred = COUNTRY_MAIN_AIRPORT.get(country_code)

    if preferred and preferred in AIRPORTS:
        return preferred

    candidates = []

    for iata, airport in AIRPORTS.items():
        if not iata:
            continue

        if airport_country_matches(airport, country_code):
            name = str(airport.get("name", "")).lower()
            city = str(airport.get("city", "")).lower()

            score = 0

            if "international" in name:
                score += 50
            if "intl" in name:
                score += 40
            if "capital" in name:
                score += 20
            if city:
                score += 5

            candidates.append((score, iata))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]




def resolve_location_to_iata(location: str):
    """
    Converts country/city/airport/IATA into IATA code.

    Examples:
    Bangladesh -> DAC
    Japan -> NRT
    Dhaka -> DAC
    Tokyo -> NRT
    DAC -> DAC
    """

    if not location:
        return None

    raw_location = location.strip()

    # Direct IATA code
    if re.fullmatch(r"[A-Za-z]{3}", raw_location):
        code = raw_location.upper()
        if code in AIRPORTS:
            return code

    location_clean = clean_text(raw_location)

    if not location_clean:
        return None

    # City preferred airport
    if location_clean in CITY_MAIN_AIRPORT:
        return CITY_MAIN_AIRPORT[location_clean]

    # Fuzzy match city names if user had a typo (e.g. "duabi" -> "dubai")
    from difflib import SequenceMatcher
    for known_city, iata in CITY_MAIN_AIRPORT.items():
        if SequenceMatcher(None, location_clean, known_city).ratio() >= 0.8:
            return iata

    # Country preferred airport
    country_code = country_name_to_code(location_clean)
    if country_code:
        airport = get_best_airport_for_country(country_code)
        if airport:
            return airport

    # Exact city match from airport database
    city_matches = []

    for iata, airport in AIRPORTS.items():
        city = str(airport.get("city", "")).lower().strip()
        name = str(airport.get("name", "")).lower().strip()

        score = 0

        if city == location_clean:
            score += 100
        elif location_clean in city:
            score += 70

        if location_clean in name:
            score += 50

        if "international" in name:
            score += 10

        if score > 0:
            city_matches.append((score, iata))

    if city_matches:
        city_matches.sort(reverse=True)
        return city_matches[0][1]

    return None




def find_location_mentions(query: str):
    """
    Finds country or city names inside a natural language query.
    """

    q = query.lower()
    mentions = []

    # Country aliases
    for alias in COUNTRY_ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", q):
            mentions.append(alias)

    # Country names from pycountry
    for country in pycountry.countries:
        name = country.name.lower()
        if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", q):
            mentions.append(name)

    # City names from our preferred city map
    for city in CITY_MAIN_AIRPORT:
        if re.search(rf"\b{re.escape(city)}\b", q):
            mentions.append(city)

    # Fuzzy match city names if user had a typo (e.g. "duabi" -> "dubai")
    words = re.findall(r"\b[a-zA-Z]{3,}\b", q)
    from difflib import SequenceMatcher
    for w in words:
        for city in CITY_MAIN_AIRPORT:
            if city not in mentions:
                if SequenceMatcher(None, w.lower(), city).ratio() >= 0.8:
                    mentions.append(city)

    # Remove duplicate while keeping order
    unique_mentions = []
    for item in mentions:
        if item not in unique_mentions:
            unique_mentions.append(item)

    return unique_mentions


def parse_route(query: str):
    """
    Returns:
    dep_iata, arr_iata

    Can return:
    None, None  -> global live flights
    DAC, NRT    -> filtered route
    DAC, None   -> all flights from DAC
    None, NRT   -> all flights to NRT
    """

    q = query.strip()
    q_lower = q.lower()

    # Global / all-country query
    global_keywords = [
        "all country",
        "all countries",
        "global flight",
        "global flights",
        "all flight",
        "all flights",
        "worldwide flight",
        "worldwide flights",
    ]

    if any(keyword in q_lower for keyword in global_keywords):
        return None, None

    # Direct IATA code route: DAC to NRT
    codes = re.findall(r"\b[A-Z]{3}\b", q)

    if len(codes) >= 2:
        dep = codes[0].upper()
        arr = codes[1].upper()
        return dep, arr

    # Pattern: from X to Y
    match = re.search(
        r"\bfrom\s+(.+?)\s+\bto\s+(.+?)(?:\s+(?:on|for|under|including|with|in|at)\b|[.!?]|$)",
        q_lower,
    )

    if match:
        origin_text = match.group(1)
        dest_text = match.group(2)

        dep_iata = resolve_location_to_iata(origin_text)
        arr_iata = resolve_location_to_iata(dest_text)

        return dep_iata, arr_iata

    # Pattern: to Y from X
    match = re.search(
        r"\bto\s+(.+?)\s+\bfrom\s+(.+?)(?:\s+(?:on|for|under|including|with|in|at)\b|[.!?]|$)",
        q_lower,
    )

    if match:
        dest_text = match.group(1)
        origin_text = match.group(2)

        dep_iata = resolve_location_to_iata(origin_text)
        arr_iata = resolve_location_to_iata(dest_text)

        return dep_iata, arr_iata

    # Pattern: flights from X
    match = re.search(r"\bfrom\s+(.+?)(?:[.!?]|$)", q_lower)

    if match:
        origin_text = match.group(1)
        dep_iata = resolve_location_to_iata(origin_text)
        return dep_iata, None

    # Pattern: flights to X
    match = re.search(r"\bto\s+(.+?)(?:[.!?]|$)", q_lower)

    if match:
        dest_text = match.group(1)
        arr_iata = resolve_location_to_iata(dest_text)
        return None, arr_iata

    # Fallback: find country/city mentions
    mentions = find_location_mentions(q)

    if len(mentions) >= 2:
        dep_iata = resolve_location_to_iata(mentions[0])
        arr_iata = resolve_location_to_iata(mentions[1])
        return dep_iata, arr_iata

    if len(mentions) == 1:
        arr_iata = resolve_location_to_iata(mentions[0])
        return DEFAULT_ORIGIN_IATA, arr_iata

    return None, None


def format_flight(flight: dict):
    airline = flight.get("airline", {}).get("name") or "Unknown airline"
    flight_number = flight.get("flight", {}).get("iata") or "Unknown flight number"
    status = flight.get("flight_status") or "Unknown"

    dep = flight.get("departure", {}) or {}
    arr = flight.get("arrival", {}) or {}

    dep_airport = dep.get("airport") or "Unknown departure airport"
    dep_iata = dep.get("iata") or "Unknown"
    dep_terminal = dep.get("terminal") or "N/A"
    dep_gate = dep.get("gate") or "N/A"
    dep_scheduled = dep.get("scheduled") or "Unknown"
    dep_delay = dep.get("delay")
    dep_delay_text = f"{dep_delay} minutes" if dep_delay is not None else "N/A"

    arr_airport = arr.get("airport") or "Unknown arrival airport"
    arr_iata = arr.get("iata") or "Unknown"
    arr_terminal = arr.get("terminal") or "N/A"
    arr_gate = arr.get("gate") or "N/A"
    arr_scheduled = arr.get("scheduled") or "Unknown"
    arr_delay = arr.get("delay")
    arr_delay_text = f"{arr_delay} minutes" if arr_delay is not None else "N/A"

    # Build direct search URLs for Google Flights & Skyscanner
    dep_iata_clean = dep_iata if dep_iata != "Unknown" else ""
    arr_iata_clean = arr_iata if arr_iata != "Unknown" else ""
    q_parts = ["flights"]
    if dep_iata_clean and arr_iata_clean:
        q_parts.extend(["from", dep_iata_clean, "to", arr_iata_clean])
    elif arr_iata_clean:
        q_parts.extend(["to", arr_iata_clean])
    if airline and airline != "Unknown airline":
        q_parts.append(airline)
    if flight_number and flight_number != "Unknown flight number":
        q_parts.append(flight_number)
    google_flights_url = f"https://www.google.com/travel/flights?q={urllib.parse.quote_plus(' '.join(q_parts))}"
    if dep_iata_clean and arr_iata_clean:
        skyscanner_url = f"https://www.skyscanner.com/transport/flights/{dep_iata_clean.lower()}/{arr_iata_clean.lower()}/"
    elif arr_iata_clean:
        skyscanner_url = f"https://www.skyscanner.com/transport/flights//to-{arr_iata_clean.lower()}/"
    else:
        skyscanner_url = "https://www.skyscanner.com/"

    return f"""
Airline: {airline}
Flight: {flight_number}
Status: {status}

Departure:
- Airport: {dep_airport}
- IATA: {dep_iata}
- Terminal: {dep_terminal}
- Gate: {dep_gate}
- Scheduled: {dep_scheduled}
- Delay: {dep_delay_text}

Arrival:
- Airport: {arr_airport}
- IATA: {arr_iata}
- Terminal: {arr_terminal}
- Gate: {arr_gate}
- Scheduled: {arr_scheduled}
- Delay: {arr_delay_text}

Booking & Live Prices:
- Google Flights: {google_flights_url}
- Skyscanner: {skyscanner_url}
""".strip()


def flight_to_dict(flight: dict) -> dict:
    """
    Same field extraction as format_flight(), but returns structured data
    instead of a pre-formatted string -- this is what lets a frontend render
    each flight as a compact, scannable card instead of a wall of text.
    Delay is kept as a raw int/None (not stringified) so the frontend can
    decide its own display and color-coding.
    """
    airline = flight.get("airline", {}).get("name") or "Unknown airline"
    flight_number = flight.get("flight", {}).get("iata") or None
    status = flight.get("flight_status") or "unknown"

    dep = flight.get("departure", {}) or {}
    arr = flight.get("arrival", {}) or {}
    dep_iata = dep.get("iata") or ""
    arr_iata = arr.get("iata") or ""

    q_parts = ["flights"]
    if dep_iata and arr_iata:
        q_parts.extend(["from", dep_iata, "to", arr_iata])
    elif arr_iata:
        q_parts.extend(["to", arr_iata])
    if airline and airline != "Unknown airline":
        q_parts.append(airline)
    if flight_number:
        q_parts.append(flight_number)
    google_flights_url = f"https://www.google.com/travel/flights?q={urllib.parse.quote_plus(' '.join(q_parts))}"
    if dep_iata and arr_iata:
        skyscanner_url = f"https://www.skyscanner.com/transport/flights/{dep_iata.lower()}/{arr_iata.lower()}/"
    elif arr_iata:
        skyscanner_url = f"https://www.skyscanner.com/transport/flights//to-{arr_iata.lower()}/"
    else:
        skyscanner_url = "https://www.skyscanner.com/"

    return {
        "airline": airline,
        "flight_number": flight_number,
        "status": status,
        "google_flights_url": google_flights_url,
        "skyscanner_url": skyscanner_url,
        "departure": {
            "airport": dep.get("airport") or None,
            "iata": dep.get("iata") or None,
            "terminal": dep.get("terminal") or None,
            "gate": dep.get("gate") or None,
            "scheduled": dep.get("scheduled") or None,
            "delay_minutes": dep.get("delay"),
        },
        "arrival": {
            "airport": arr.get("airport") or None,
            "iata": arr.get("iata") or None,
            "terminal": arr.get("terminal") or None,
            "gate": arr.get("gate") or None,
            "scheduled": arr.get("scheduled") or None,
            "delay_minutes": arr.get("delay"),
        },
    }


def _fetch_flight_data(query: str, limit: int = 10):
    """
    Shared AviationStack fetch + error handling used by both search_flights()
    (text, for the chat agent) and search_flights_structured() (data, for the
    itinerary planner's UI) -- one place to hit the API and parse the route,
    instead of duplicating that logic in both.

    Returns either {"error": "<message>"} or
    {"route_info": "<text>", "dep_iata": ..., "arr_iata": ..., "flight_data": [...]}
    where flight_data is the raw AviationStack list, unprocessed.
    """
    if not API_KEY:
        return {"error": "AVIATIONSTACK_API_KEY is missing."}

    dep_iata, arr_iata = parse_route(query)

    params = {
        "access_key": API_KEY,
        "limit": min(limit, 100),
    }
    if dep_iata:
        params["dep_iata"] = dep_iata
    if arr_iata:
        params["arr_iata"] = arr_iata

    try:
        response = requests.get(BASE_URL, params=params, timeout=30)
        data = response.json()
    except requests.exceptions.RequestException as e:
        return {"error": f"Flight API request failed: {e}"}
    except ValueError:
        return {"error": "Flight API returned invalid JSON."}

    if "error" in data:
        error = data["error"]
        return {"error": f"Flight API error ({error.get('code', 'unknown')}): {error.get('message', 'Unknown error')}"}

    flight_data = data.get("data", [])

    route_info = "Global live flights"
    if dep_iata and arr_iata:
        route_info = f"Live flights from {dep_iata} to {arr_iata}"
    elif dep_iata:
        route_info = f"Live flights from {dep_iata}"
    elif arr_iata:
        route_info = f"Live flights to {arr_iata}"

    return {
        "route_info": route_info,
        "dep_iata": dep_iata,
        "arr_iata": arr_iata,
        "flight_data": flight_data,
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def find_nearest_departure_hubs(origin: str, destination: str) -> list[dict]:
    """
    Dynamically identifies the nearest major departure hub airports for an origin
    city that have strong flight connectivity to the destination.
    Uses LLM network reasoning with an intelligent geographic Haversine fallback --
    absolutely zero hardcoded values.
    """
    if not origin or not destination:
        return []

    # 1. Dynamic LLM lookup via Groq (primary, world-aware, handles connectivity)
    groq_api_key = os.getenv("GROQ_API_KEY")
    if groq_api_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_api_key)
            prompt = (
                f'You are an airline routing expert. A traveler wants to fly from "{origin}" to "{destination}".\n'
                f'If there are no direct flights or limited connectivity from "{origin}" to "{destination}", '
                f'identify the top 3 nearest major departure hubs / source stations (in physical proximity to "{origin}") '
                f'that have strong flight connectivity to "{destination}".\n'
                f'Important: Always prioritize the nearest major hub airports within the same country or geographic region '
                f'first as viable departure points (e.g. for Indian cities, suggest nearest Indian international hubs '
                f'like Mumbai, Delhi, Bengaluru before suggesting foreign transit hubs).\n'
                f'Return ONLY a valid JSON list of objects:\n'
                f'[\n'
                f'  {{"city": "City Name", "iata": "3-letter IATA", "distance_note": "Brief explanation of distance from {origin} and connectivity to {destination}"}}\n'
                f']\n'
                f'No other text.'
            )
            resp = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=350,
            )
            content = resp.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.strip("`")
                if content.startswith("json"):
                    content = content[4:].strip()
            data = json.loads(content)
            if isinstance(data, list) and len(data) > 0:
                validated = []
                for item in data:
                    iata = str(item.get("iata", "")).upper().strip()
                    city = str(item.get("city", "")).strip()
                    dist_note = str(item.get("distance_note", "")).strip()
                    if len(iata) == 3 and (iata in AIRPORTS or city):
                        validated.append({
                            "city": city or (AIRPORTS.get(iata, {}).get("city") or iata),
                            "iata": iata,
                            "distance_note": dist_note or f"Major hub near {origin}."
                        })
                if validated:
                    return validated
        except Exception as exc:
            print(f"[WARN] Dynamic LLM hub lookup failed ({exc}), falling back to geographic calculation.")

    # 2. Dynamic Geographic Haversine fallback using AIRPORTS database
    orig_iata = resolve_location_to_iata(origin)
    orig_ap = AIRPORTS.get(orig_iata) if orig_iata else None
    if orig_ap and orig_ap.get("lat") and orig_ap.get("lon"):
        orig_lat = orig_ap["lat"]
        orig_lon = orig_ap["lon"]
        orig_country = orig_ap.get("country", "")

        candidates = []
        for iata, ap in AIRPORTS.items():
            if iata == orig_iata or not ap.get("lat") or not ap.get("lon"):
                continue
            name = str(ap.get("name", "")).lower()
            city = str(ap.get("city", "")).strip()
            country = str(ap.get("country", "")).strip()
            if not city:
                continue

            # Check if international airport or capital
            is_intl = "international" in name or "intl" in name
            is_same_country = (country == orig_country)
            if not is_intl and not is_same_country:
                continue

            dist = _haversine_km(orig_lat, orig_lon, ap["lat"], ap["lon"])
            candidates.append({
                "same_country": is_same_country,
                "dist": dist,
                "city": city,
                "iata": iata,
                "name": ap.get("name", "")
            })

        candidates.sort(key=lambda x: (not x["same_country"], x["dist"]))
        results = []
        seen_cities = set()
        for c in candidates:
            if c["city"] in seen_cities:
                continue
            seen_cities.add(c["city"])
            results.append({
                "city": c["city"],
                "iata": c["iata"],
                "distance_note": f"Approximately {round(c['dist'])} km from {orig_ap.get('city') or origin}."
            })
            if len(results) == 3:
                break
        if results:
            return results

    return []


def search_flights(query: str, limit: int = 10):
    if not API_KEY:
        return (
            "Flight API error: AVIATIONSTACK_API_KEY is missing.\n"
            "Please add this in your .env file:\n"
            "AVIATIONSTACK_API_KEY=your_api_key_here"
        )

    result = _fetch_flight_data(query, limit)

    if "error" in result:
        return f"Flight API error:\n{result['error']}"

    flight_data = result["flight_data"]

    if not flight_data:
        dep_iata = result.get("dep_iata")
        arr_iata = result.get("arr_iata")

        if dep_iata and arr_iata:
            # Check for nearest major hub dynamically
            hubs = find_nearest_departure_hubs(dep_iata, arr_iata)
            if hubs:
                top_hub = hubs[0]
                # Try fetching from nearest hub
                hub_res = _fetch_flight_data(f"flights from {top_hub['iata']} to {arr_iata}", limit)
                hub_flights = hub_res.get("flight_data", []) if "flight_data" in hub_res else []
                if hub_flights:
                    formatted_hub_flights = [format_flight(f) for f in hub_flights[:limit]]
                    return (
                        f"Note: No direct flights were found from {dep_iata} to {arr_iata}.\n"
                        f"Recommended nearest major departure hub: {top_hub['city']} ({top_hub['iata']}) - {top_hub.get('distance_note', '')}.\n\n"
                        f"Live flights from {top_hub['city']} ({top_hub['iata']}) to {arr_iata}:\n\n"
                        + "\n\n---\n\n".join(formatted_hub_flights)
                    )
                else:
                    return (
                        f"No direct live flights found from {dep_iata} to {arr_iata}.\n\n"
                        f"Recommended Departure Hub: {top_hub['city']} ({top_hub['iata']}) - {top_hub.get('distance_note', '')}.\n"
                        f"For real-time ticket prices and connecting flight options, check Google Flights or Skyscanner."
                    )

        route_text = ""
        if dep_iata and arr_iata:
            route_text = f" for route {dep_iata} to {arr_iata}"
        elif dep_iata:
            route_text = f" from {dep_iata}"
        elif arr_iata:
            route_text = f" to {arr_iata}"

        return (
            f"No live flight data found{route_text}.\n\n"
            "Note: AviationStack provides live/status flight data, not ticket prices. "
            "For actual fare prices, use a flight-pricing API such as Amadeus."
        )

    formatted_flights = [format_flight(flight) for flight in flight_data[:limit]]
    return f"{result['route_info']}\n\n" + "\n\n---\n\n".join(formatted_flights)


def search_flights_structured(query: str, limit: int = 10) -> dict:
    """
    Same search as search_flights(), but returns structured data instead of
    a formatted string -- for a frontend that wants to render individual
    flight cards rather than a text block. Flights are sorted by scheduled
    departure time (ascending, unknown times last) so they read chronologically
    rather than in whatever order the API happened to return them in.

    Returns: {"route_info": str, "flights": [...]} or {"error": str}
    """
    result = _fetch_flight_data(query, limit)

    if "error" in result:
        return {"error": result["error"]}

    flight_data = result["flight_data"]

    if not flight_data:
        return {"error": "no_data", "route_info": result["route_info"]}

    flights = [flight_to_dict(f) for f in flight_data[:limit]]
    flights.sort(
        key=lambda f: f["departure"]["scheduled"] or "9999"  # unknown times sort last
    )

    return {"route_info": result["route_info"], "flights": flights}


if __name__ == "__main__":
    print(search_flights("Plan a 7 days Japan trip from Bangladesh"))
    print("\n" + "=" * 80 + "\n")
    print(search_flights("all country flight info"))