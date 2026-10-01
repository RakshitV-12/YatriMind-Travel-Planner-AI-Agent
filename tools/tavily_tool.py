from tavily import TavilyClient
import os
from dotenv import load_dotenv

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None


def tavily_search(query: str, max_results: int = 5) -> str:
    if not client:
        return "Search is unavailable: TAVILY_API_KEY is not configured."

    try:
        response = client.search(
            query=query,
            max_results=max_results
        )
    except Exception as exc:
        return f"Search is temporarily unavailable ({type(exc).__name__})."

    results = []

    for i, r in enumerate(response.get("results", []), 1):
        title   = r.get("title", "Unknown")
        url     = r.get("url", "")
        snippet = r.get("content", "").strip()
        # Keep only the first 300 characters to avoid wall-of-text
        if len(snippet) > 300:
            snippet = snippet[:300].rsplit(" ", 1)[0] + "..."

        results.append(f"{i}. **{title}**\n   {url}\n   {snippet}")

    return "\n\n".join(results) if results else "No results found for this trip."


def tavily_search_with_images(query: str, max_results: int = 5) -> dict:
    if not client:
        return {"text": "Search is unavailable: TAVILY_API_KEY is not configured.", "images": []}

    try:
        response = client.search(
            query=query,
            max_results=max_results,
            include_images=True
        )
    except Exception as exc:
        return {"text": f"Search is temporarily unavailable ({type(exc).__name__}).", "images": []}

    results = []
    for i, r in enumerate(response.get("results", []), 1):
        title   = r.get("title", "Unknown")
        url     = r.get("url", "")
        snippet = r.get("content", "").strip()
        if len(snippet) > 300:
            snippet = snippet[:300].rsplit(" ", 1)[0] + "..."
        results.append(f"{i}. **{title}**\n   {url}\n   {snippet}")

    return {
        "text": "\n\n".join(results) if results else "No results found for this trip.",
        "images": response.get("images", []) or [],
    }

