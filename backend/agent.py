import os 
import certifi
from dotenv import load_dotenv

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import logging
import re
import time
from typing import TypedDict, Annotated
import operator
import uuid

logger = logging.getLogger(__name__)

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)
from langchain_groq import ChatGroq

import sys
from pathlib import Path

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from tools.tavily_tool import tavily_search
from tools.flight_tool import search_flights
from backend.checkpointer_json import JSONFileSaver


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")


# =========================
# LLM
# =========================

llm = ChatGroq(
    model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
    max_tokens=int(os.getenv("GROQ_MAX_TOKENS", "800")),
    api_key=GROQ_API_KEY
)


# =========================
# State
# =========================

class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    flight_results: str
    hotel_results: str
    itinerary: str
    llm_calls: int


# =========================
# Flight Agent
# =========================

def flight_agent(state: TravelState):
    query = state["user_query"]
    flight_data = search_flights(query)

    if isinstance(flight_data, str) and flight_data.startswith(("Flight API error", "Flight API request failed")):
        clean_message = "Live flight tracking is currently unavailable for this route."
    else:
        clean_message = flight_data

    return {
        "flight_results": clean_message,
        "messages": [
            AIMessage(content="Flight results fetched.")
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }



# =========================
# Hotel Agent
# =========================

def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['user_query']}"
    try:
        hotel_results = tavily_search(query, max_results=5)
    except Exception as exc:
        logger.warning(f"Hotel search failed ({type(exc).__name__}: {exc}); continuing without it.")
        hotel_results = "Hotel search is currently unavailable for this trip."

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(content="Hotel information fetched.")
        ],
        "llm_calls": state.get("llm_calls", 0) + 1
    }




# =========================
# Helpers
# =========================

def _invoke_llm_with_retry(messages: list, agent_name: str):
    """
    One retry on a rate-limit error before giving up -- the Groq 429 body
    usually names the exact wait time, so a short real backoff often
    succeeds outright rather than crashing the whole graph on the first
    hiccup.
    """
    for attempt in range(2):
        try:
            return llm.invoke(messages)
        except Exception as exc:
            exc_str = str(exc)
            is_rate_limit = "429" in exc_str or "rate_limit" in exc_str.lower() or type(exc).__name__ == "RateLimitError"
            if is_rate_limit and attempt == 0:
                wait_s = _parse_retry_after_seconds(exc_str)
                logger.warning(f"{agent_name}: Groq rate limit hit, retrying once after {wait_s}s")
                time.sleep(wait_s)
                continue
            logger.error(f"{agent_name}: LLM call failed ({type(exc).__name__}: {exc})")
            return None
    return None


def _parse_retry_after_seconds(error_text: str, default: float = 3.0, cap: float = 8.0) -> float:
    match = re.search(r"try again in\s+([\d.]+)s", error_text, re.IGNORECASE)
    if match:
        try:
            return min(float(match.group(1)) + 0.2, cap)
        except ValueError:
            pass
    return default




# =========================
# Itinerary Agent
# =========================

def itinerary_agent(state: TravelState):
    prompt = f"""
Create a complete travel itinerary.

User Query:
{state['user_query']}

Flight Information / Context:
{state['flight_results']}

Hotel Results:
{state['hotel_results']}

Make the itinerary practical, budget-aware, and easy to follow.
CRITICAL RULE: Do NOT repeat any attraction, landmark, lake, activity, or restaurant once suggested across the itinerary. Every place must be completely distinct and unique.
Include realistic estimated round-trip flight and accommodation expense ranges in the budget planning based on typical travel costs for this route.
"""

    response = _invoke_llm_with_retry([
        SystemMessage(content="You are an expert travel planner."),
        HumanMessage(content=prompt)
    ], "itinerary_agent")

    if response is None:
        fallback_text = (
            "We couldn't generate a detailed itinerary right now due to a "
            "temporary service issue. Please try again in a moment."
        )
        return {
            "itinerary": fallback_text,
            "messages": [AIMessage(content=fallback_text)],
            "llm_calls": state.get("llm_calls", 0) + 1
        }

    return {
        "itinerary": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1
    }



# =========================
# Final Response Agent
# =========================

def final_agent(state: TravelState):
    final_prompt = f"""
Generate the final travel response for the user.

User Request:
{state['user_query']}

Flights Context:
{state['flight_results']}

Hotels:
{state['hotel_results']}

Itinerary:
{state['itinerary']}

Format the final answer beautifully using these sections:

1. Trip Summary
2. Flight Information & Estimated Fares
3. Hotel Suggestions
4. Day-by-Day Itinerary
5. Estimated Budget Breakdown
6. Final Recommendations

Important for Section 2 (Flight Information & Estimated Fares):
- Suggest 2-3 specific flight options based on the Flights Context (e.g. Air India, Emirates, IndiGo).
- For each suggested flight, include the airline, flight number, schedule, and the clickable Google Flights redirect link so the traveler can click to view real-time prices, baggage rules, and booking options.
- Provide a clearly labeled *Estimated Price Range* (e.g. Economy Round-Trip estimate) based on general route pricing.
- Disclose honestly that flight prices are AI estimates based on typical seasonal rates, and recommend checking Google Flights or airline booking sites for live quotes.
- If live flight status data was found in Flights Context, summarize it; otherwise state that live schedule tracking was unavailable and focus on the estimated fares and flight options.

Important for Section 5 (Estimated Budget Breakdown):
- Include the estimated flight cost so the total budget breakdown is complete and realistic.

Be clear, practical, and helpful.
"""

    response = _invoke_llm_with_retry([
        SystemMessage(content="You are a professional AI travel booking assistant."),
        HumanMessage(content=final_prompt)
    ], "final_agent")

    if response is None:
        fallback_text = state.get("itinerary") or (
            "We're experiencing a temporary issue generating your travel plan. Please try again shortly."
        )
        return {
            "messages": [AIMessage(content=fallback_text)],
            "llm_calls": state.get("llm_calls", 0) + 1
        }

    return {
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1
    }


# =========================
# Build Graph
# =========================

graph = StateGraph(TravelState)

graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "flight_agent")
graph.add_edge("flight_agent", "hotel_agent")
graph.add_edge("hotel_agent", "itinerary_agent")
graph.add_edge("itinerary_agent", "final_agent")
graph.add_edge("final_agent", END)


# =========================
# Checkpointer (JSON file — no database server required)
# =========================

CHECKPOINT_PATH = os.getenv(
    "CHECKPOINT_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "checkpoints.json")
)


def _build_checkpointer():
    try:
        saver = JSONFileSaver(path=CHECKPOINT_PATH)
        print(f"[INFO] Using JSON file checkpointer at {CHECKPOINT_PATH}")
        return saver
    except Exception as exc:
        print(f"[WARN] Could not initialize JSON checkpointer ({exc!r}). Falling back to in-memory checkpointer.")
        return MemorySaver()


checkpointer = _build_checkpointer()
travel_graph = graph.compile(checkpointer=checkpointer)



# =========================
# Function for FastAPI
# =========================

def run_travel_agent(user_input: str, thread_id: str | None = None):
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    try:
        result = travel_graph.invoke(
            {
                "messages": [
                    HumanMessage(content=user_input)
                ],
                "user_query": user_input,
                "flight_results": "",
                "hotel_results": "",
                "itinerary": "",
                "llm_calls": 0
            },
            config=config
        )
    except Exception as exc:
        logger.error(f"run_travel_agent: unhandled failure ({type(exc).__name__}: {exc})")
        return {
            "thread_id": thread_id,
            "answer": (
                "Sorry, something went wrong while planning your trip. "
                "Please try again in a moment."
            ),
            "flight_results": "",
            "hotel_results": "",
            "itinerary": "",
            "llm_calls": 0,
        }

    final_answer = result["messages"][-1].content

    return {
        "thread_id": thread_id,
        "answer": final_answer,
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "itinerary": result.get("itinerary", ""),
        "llm_calls": result.get("llm_calls", 0),
    }
