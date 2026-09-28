"""The four tools the agent can use, plus their JSON schemas for the model.

Tools never raise: any error is turned into a string starting with "Error:"
so the model can read it and decide what to do next (e.g. try another URL).
"""
import asyncio

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

MAX_PAGE_CHARS = 4000
FETCH_TIMEOUT_SECONDS = 10

# JSON schemas sent to the model. The descriptions matter: they are how the model
# decides which tool to call and with what arguments.
TOOL_SCHEMAS = [
    {
        "name": "web_search",
        "description": "Search the web. Returns up to 5 results with title, URL and a short snippet. "
        "Use this first to find sources, then fetch_url to read the most promising ones.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": f"Download a web page and return its visible text (truncated to {MAX_PAGE_CHARS} characters).",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full http(s) URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "save_note",
        "description": "Save a short finding (with its source URL) to your notes so you don't lose it.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "The finding to remember"}},
            "required": ["text"],
        },
    },
    {
        "name": "finish",
        "description": "Call this exactly once when you are done, with the complete final answer for the user.",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string", "description": "The final answer, citing source URLs"}},
            "required": ["answer"],
        },
    },
]


async def web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo. The ddgs library is synchronous, so run it in a thread
    to avoid blocking the event loop."""
    try:
        results = await asyncio.to_thread(lambda: DDGS().text(query, max_results=max_results))
    except Exception as e:
        return f"Error: search failed: {e}"
    if not results:
        return "No results found."
    return "\n\n".join(
        f"{i}. {r.get('title', '')}\n   URL: {r.get('href', '')}\n   {r.get('body', '')}"
        for i, r in enumerate(results, 1)
    )


async def fetch_url(url: str, client: httpx.AsyncClient | None = None) -> str:
    """Fetch a page and return its readable text, truncated to MAX_PAGE_CHARS.
    `client` can be injected (tests pass one with a fake transport)."""
    if not url.startswith(("http://", "https://")):
        return "Error: URL must start with http:// or https://"

    own_client = client is None
    client = client or httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (research-agent demo)"},
    )
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.TimeoutException:
        return f"Error: timed out after {FETCH_TIMEOUT_SECONDS}s fetching {url}"
    except httpx.HTTPStatusError as e:
        return f"Error: {url} returned HTTP {e.response.status_code}"
    except httpx.HTTPError as e:
        return f"Error: could not fetch {url}: {e.__class__.__name__}: {e}"
    finally:
        if own_client:
            await client.aclose()

    # Strip scripts/styles/navigation and collapse whitespace to get readable text.
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())

    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + f"\n...[truncated, page had {len(text)} characters]"
    return text or "Error: page had no readable text"


def save_note(text: str, notes: list[str]) -> str:
    """Append a finding to this run's notes list."""
    notes.append(text)
    return f"Note saved ({len(notes)} notes so far)."


async def execute_tool(name: str, tool_input: dict, notes: list[str]) -> str:
    """Dispatch a tool call from the model to the matching Python function.
    `finish` is handled by the agent loop itself, not here."""
    try:
        if name == "web_search":
            return await web_search(tool_input["query"])
        if name == "fetch_url":
            return await fetch_url(tool_input["url"])
        if name == "save_note":
            return save_note(tool_input["text"], notes)
        return f"Error: unknown tool '{name}'"
    except KeyError as e:
        return f"Error: missing required argument {e} for tool '{name}'"
