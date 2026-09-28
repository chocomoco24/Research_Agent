"""Unit tests for the tool functions. Network calls are faked with httpx.MockTransport."""
import asyncio

import httpx

from app.tools.research_tools import MAX_PAGE_CHARS, execute_tool, fetch_url, save_note


def fake_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_url_extracts_text_and_truncates():
    long_html = "<html><script>ignore()</script><body><p>" + "word " * 5000 + "</p></body></html>"
    client = fake_client(lambda request: httpx.Response(200, text=long_html))

    text = asyncio.run(fetch_url("https://example.com", client=client))

    assert "ignore()" not in text  # scripts are stripped
    assert text.startswith("word word")
    assert "[truncated" in text
    assert len(text) < MAX_PAGE_CHARS + 100


def test_fetch_url_returns_error_on_http_error():
    client = fake_client(lambda request: httpx.Response(404))
    result = asyncio.run(fetch_url("https://example.com/missing", client=client))
    assert result == "Error: https://example.com/missing returned HTTP 404"


def test_fetch_url_returns_error_on_timeout():
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    result = asyncio.run(fetch_url("https://slow.example.com", client=fake_client(handler)))
    assert result.startswith("Error: timed out")


def test_fetch_url_rejects_non_http_urls():
    assert asyncio.run(fetch_url("file:///etc/passwd")).startswith("Error:")


def test_save_note_appends():
    notes = []
    assert save_note("Python 3.13 adds a new REPL", notes) == "Note saved (1 notes so far)."
    assert notes == ["Python 3.13 adds a new REPL"]


def test_execute_tool_handles_unknown_tool_and_missing_args():
    notes = []
    assert asyncio.run(execute_tool("delete_everything", {}, notes)) == "Error: unknown tool 'delete_everything'"
    assert "missing required argument" in asyncio.run(execute_tool("save_note", {}, notes))
