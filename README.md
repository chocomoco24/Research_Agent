# Research Agent

A small FastAPI service that runs an **LLM research agent**. You POST a goal like
*"Find out what changed in Python 3.13 and summarize the top 5 points"*, and the agent
loops on its own: it decides which tool to call (search the web, read a page, save a note),
reads the result, and continues until it calls `finish` with an answer.
Every step is logged to Postgres so you can inspect exactly what the agent did.

**Stack:** Python 3.11 · FastAPI · SQLAlchemy · PostgreSQL · Google Gemini API (function calling) · httpx · DuckDuckGo search · pytest · Docker

---

## Project structure

```
app/
  main.py              FastAPI app, startup (creates tables), /health
  config.py            Settings from environment variables
  schemas.py           Pydantic request/response models (validation)
  db/database.py       SQLAlchemy engine + session
  db/models.py         `runs` and `steps` tables
  tools/research_tools.py  web_search, fetch_url, save_note, finish + JSON schemas
  agent/loop.py        The agent loop, step logging, guardrails
  routes/runs.py       POST /runs, GET /runs/{id}, GET /runs
  static/              Web UI: index.html, style.css, app.js (plain HTML/CSS/JS, no framework)
tests/                 pytest tests (SQLite + mocked LLM, no network needed)
docker-compose.yml     Postgres (and optionally the API)
Dockerfile
```

## Setup

Requirements: Python 3.11+, Docker Desktop, a free [Gemini API key](https://aistudio.google.com/apikey)
(sign in with a Google account → "Create API key"; no credit card needed for the free tier).

```bash
# 1. Configure environment
cp .env.example .env          # then put your GEMINI_API_KEY in .env

# 2. Start Postgres (host port 5433)
docker compose up -d db

# 3. Install and run the API
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- **Web UI:** http://localhost:8000/: type a goal, watch the steps appear live, read the answer, browse past runs
- **Interactive API docs:** http://localhost:8000/docs

**Everything in Docker instead:** `docker compose --profile full up --build`

**Run the tests** (no Postgres or API key needed, they use SQLite and a mocked LLM):

```bash
pytest -v
```

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | – | Required. Your Gemini API key |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Model used by the agent (has a free tier). If it returns 503 "high demand", try `gemini-3-flash-preview` or `gemini-3.1-flash-lite`. Free-tier quotas are per model and can be as low as 20 requests/day |
| `GEMINI_THINKING_LEVEL` | `medium` | How much the model thinks: `minimal` / `low` / `medium` / `high`. Lower is faster and uses fewer tokens |
| `DATABASE_URL` | `postgresql+psycopg://agent:agent@localhost:5433/research_agent` | SQLAlchemy connection string |
| `MAX_STEPS` | `8` | Max model calls per run |
| `RUN_TIMEOUT_SECONDS` | `180` | Max wall-clock time per run |

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/runs` | Body `{"goal": "..."}`. Creates a run, returns `{"id", "status"}` with **202**, and runs the agent in the background |
| `GET` | `/runs/{id}` | Status, final answer, token usage and the full step-by-step trace |
| `GET` | `/runs?limit=20` | Most recent runs (newest first) |
| `GET` | `/health` | Liveness check |
| `GET` | `/` | Web UI |

### Web UI

A single page in plain HTML/CSS/JavaScript, served by FastAPI itself (same origin, so no CORS setup).
Submitting a goal calls `POST /runs`, then the page **polls** `GET /runs/{id}` every 2 seconds and re-renders
the status badge, the step timeline (search → read page → result, with expandable raw output) and the final
answer. All text from the API is HTML-escaped before display, because it contains content from arbitrary
web pages.

Errors: **422** for invalid input (missing/blank/too-long goal), **404** for an unknown run id,
**500** with a clear message if `GEMINI_API_KEY` is not set.

### Example curl commands

```bash
# Start a run
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"goal": "Find out what changed in Python 3.13 and summarize the top 5 points."}'
# -> {"id": 1, "status": "pending"}

# Check progress / get the answer and trace
curl http://localhost:8000/runs/1

# List recent runs
curl http://localhost:8000/runs
```

(On Windows PowerShell use `curl.exe` and escape the inner quotes, or use the `/docs` page.)

## How the agent loop works

```
POST /runs ──► save run (pending) ──► return id (202)
                     │
                     ▼  background task
   ┌──────────► call Gemini with: goal + history + tool schemas
   │                 │
   │      Gemini replies with text and/or function_call parts
   │                 │
   │     ┌───────────┼────────────────────────────┐
   │     ▼           ▼                            ▼
   │  finish?     other tool                  no tool at all
   │  → save      → run it (search/fetch/note)  → use its text as the answer
   │  answer,     → log tool_call + tool_result
   │  done        → send function_response back (matched by call id + name)
   │                 │
   └─────────────────┘   (until finish, MAX_STEPS, or timeout)
```

1. The goal becomes the first user message. Gemini also gets a system instruction (research
   instructions) and the **JSON schemas** of the four tools. The SDK's automatic function calling
   is turned off, so the loop is fully written by hand.
2. Gemini answers with a `function_call`, e.g. `web_search({"query": "Python 3.13 what's new"})`.
   The model only *requests* the call; **our code executes it**.
3. The tool's output is sent back as a `function_response` with the same call id and name, and
   the full history is re-sent (including the model's turn unchanged, which carries Gemini's
   *thought signatures*). Gemini reads the result and decides the next action.
4. The loop ends when Gemini calls `finish(answer)`. Every model message, tool call and tool
   result is written to the `steps` table as it happens, so `GET /runs/{id}` shows progress live.

`call_model()` converts Gemini's response into a small provider-neutral `ModelTurn`
(text, tool calls, token counts). The loop only works with `ModelTurn`, which keeps it simple,
makes the LLM easy to mock in tests, and means switching providers only touches one function.

**Tools**

| Tool | What it does |
|---|---|
| `web_search(query)` | DuckDuckGo search, top 5 results (title, URL, snippet). Free, no API key |
| `fetch_url(url)` | Downloads a page with `httpx`, strips HTML to text, **truncates to ~4000 chars**. Timeouts / HTTP errors are returned to the model as `Error: ...` text instead of crashing |
| `save_note(text)` | Stores a finding in the run's notes (used as a partial answer if the step limit is hit) |
| `finish(answer)` | Ends the loop with the final answer |

### Guardrails (runaway loops and cost)

- **Step limit:** at most `MAX_STEPS` (default 8) model calls per run. If the limit is reached
  the run ends as `max_steps_reached` and the saved notes are returned as a partial answer.
- **Timeout:** the whole run is wrapped in `asyncio.wait_for` (default 180 s) → status `timed_out`.
- **Page truncation:** fetched pages are capped at ~4000 characters to keep context (and cost) small.
- **Token tracking:** `input_tokens` / `output_tokens` (including thinking tokens) from every model call
  are summed on the `runs` row.
- **Retries only where they help:** temporary server errors (500/502/503/504, e.g. "model overloaded") are
  retried with exponential backoff (up to 4 attempts). Quota errors (429) are *not* retried, because the
  free tier's quota is per day and retrying only wastes it (see the failure teardown below).
- **Errors never leave a run stuck:** API errors (bad key, rate limit, network) are caught and the run
  is marked `failed` with a readable reason.

Run statuses: `pending → running → completed | failed | timed_out | max_steps_reached`

## Database

```
runs:  id, goal, status, final_answer, input_tokens, output_tokens, created_at
steps: id, run_id (FK → runs.id, indexed), step_number, type, content (JSON), created_at
       type ∈ tool_call | tool_result | model_message
```

## Sample trace

A real run (`gemini-3-flash-preview`, thinking level `medium`) from `GET /runs/5`, after the fixes
described below. Long tool outputs and the final answer are shortened with `...` for readability.

```json
{
  "id": 5,
  "goal": "What is the latest version of Python and what's new in it?",
  "status": "completed",
  "input_tokens": 13129,
  "output_tokens": 2298,
  "created_at": "2026-09-28T16:47:04Z",
  "final_answer": "As of late September 2026, the latest versions of Python are **Python 3.14** (current stable release) and **Python 3.15** (currently in its final release candidate stage, scheduled for full release on October 1, 2026).\n\n### Python 3.15 ...\n* **Explicit Lazy Imports (PEP 810)** ...\n* **Built-in `frozendict` (PEP 814)** ...\n\nSources: https://www.python.org/downloads/latest/python3.15/, https://docs.python.org/3.15/whatsnew/3.15.html, https://docs.python.org/3/whatsnew/3.14.html",
  "steps": [
    { "step_number": 1, "type": "tool_call",
      "content": { "tool": "web_search", "input": { "query": "latest stable Python version September 2026" } } },
    { "step_number": 2, "type": "tool_result",
      "content": { "tool": "web_search", "output": "1. Python Release Python 3.15.0rc2 | Python.org\n   URL: https://www.python.org/downloads/latest/python3.15/\n   The next release of Python 3.15 will be 3.15.0 final, scheduled for 2026-10-01 ..." } },
    { "step_number": 3, "type": "tool_call",
      "content": { "tool": "web_search", "input": { "query": "What's new in Python 3.15" } } },
    { "step_number": 4, "type": "tool_result",
      "content": { "tool": "web_search", "output": "1. What's new in Python 3.15 - Python 3.15.0rc2 documentation\n   URL: https://docs.python.org/3.15/whatsnew/3.15.html ..." } },
    { "step_number": 5, "type": "tool_call",
      "content": { "tool": "fetch_url", "input": { "url": "https://docs.python.org/3.15/whatsnew/3.15.html" } } },
    { "step_number": 6, "type": "tool_result",
      "content": { "tool": "fetch_url", "output": "What's new in Python 3.15 - Python 3.15.0rc2 documentation ... [truncated, page had 106719 characters]" } },
    { "step_number": 7, "type": "tool_call",
      "content": { "tool": "web_search", "input": { "query": "What's new in Python 3.14 summary" } } },
    { "step_number": 8, "type": "tool_result",
      "content": { "tool": "web_search", "output": "1. What's new in Python 3.14 - Python 3.14.7 documentation ... Python 3.14 was released on 7 October 2025 ..." } },
    { "step_number": 9, "type": "model_message",
      "content": "As of late September 2026, the latest versions of Python are **Python 3.14** ... and **Python 3.15** ..." }
  ]
}
```

The agent searched, found the official release page and docs, read the 3.15 "What's New" page,
did one more search for 3.14, and answered in 4 model calls (~15k tokens). `created_at` on each
step is omitted above; in the real response every step has one.

## Failure teardown: what went wrong and how I fixed it

I ran real goals and checked every trace step by step against what the agent actually read.
Four problems showed up.

### 1. The agent trusted its memory over its own search results (outdated answer)

**Goal:** *"What is the latest version of Python and what's new in it?"* (deliberately vague: "latest" depends on today's date)

**What happened (run 4):** the first search result said *"As of June 2026, Python 3.14.6 is the
latest stable"*, and python.org listed *"3.14.7, Aug. 5, 2026"* and *"3.15 pre-release"*. The agent still answered:

> "As of May 2025, the latest major stable version of Python is **Python 3.13** ... **Python 3.13.3** (released April 8, 2025)"

"3.13.3 / April 8, 2025" appears nowhere in the trace. The model **doesn't know today's date**, so it
assumed its training period was "now", ignored the evidence, invented a version number and then spent
the rest of the run researching the wrong release (157 s, ~17k tokens for a wrong answer).

**Fix:** put today's date in the system prompt and state the rule explicitly: *"Your training data may be
out of date: if a source disagrees with what you remember, trust the source."* A test now asserts the
date is in the prompt.

**Result (run 5, the sample trace above):** the agent searched for *"latest stable Python version September 2026"*
and correctly reported **3.14.7 as latest stable and 3.15 as a release candidate due 2026-10-01**.

### 2. Details filled in from memory past a truncated page (hallucination)

**Goal:** *"Find out what changed in Python 3.13 and summarize the top 5 points."*

**What happened (run 3):** `fetch_url` truncates pages to ~4000 characters, so the agent only saw the
summary section of the docs page, which says *"Several legacy standard library modules have now been removed"*.
The answer said **"20 legacy modules have been removed ... `telnetlib`, `cgi`, `pipes`, `aifc`, `sunau`"**.
None of those names or the count were in what it read, and the count is wrong (PEP 594 removed 19). It cited the
docs page as if that were the source.

**Fix:** a stricter grounding rule in the prompt: *"Do not add names, numbers, dates or details that did not appear
in a tool result. Fetched pages are truncated, so if a detail was not in the text you received, leave it out or
say it could not be verified."*

**Result:** I checked every specific claim in run 5 (PEP numbers, feature names, versions, dates) against the
tool results: all but one were present in what the agent read. Remaining weakness: some 3.14 points came from
third-party search snippets (Real Python) but were cited to the official docs URL. A next step would be
having the agent cite the exact URL each fact came from.

### 3. The model ignored "always call finish"

In both successful runs the model ended with a plain text answer instead of calling `finish`, even after the
prompt said *"Always end by calling finish"*. The loop already handled this: if the model stops without a tool call,
its text is accepted as the final answer. **Lesson: prompts are suggestions; behavior that matters must be enforced
in code.**

### 4. Free-tier limits: overloaded models and a 20-requests-per-day quota

- The default model returned **503 "high demand"** for every request at first. The client retries 5xx errors with
  exponential backoff, then the run fails cleanly with a readable reason. Switching `GEMINI_MODEL` to another free
  model (`gemini-3-flash-preview`) worked.
- Later, a run failed mid-way with **429**. Google's error said the free tier for that model is
  **20 requests per day**, and a single research run uses 3-8 requests.
- **Bug found:** my client was also retrying 429s. For a *daily* quota, retrying just burns more quota and still fails.
  **Fix:** retry only 500/502/503/504, never 429, and pass Google's quota message through to `final_answer`
  so the run shows which limit was hit.

## Deploying (Render / Railway)

1. Push the repo to GitHub (the `.env` file is git-ignored, so keys stay local).
2. Create a Postgres database on the platform and copy its connection string.
3. Create a web service from the repo using the `Dockerfile`. Set env vars:
   `GEMINI_API_KEY`, `GEMINI_MODEL`, and `DATABASE_URL` (paste the platform's URL as-is; `postgres://` and
   `postgresql://` are converted to the `postgresql+psycopg://` driver form automatically).
   The container listens on `$PORT` automatically. Health check path: `/health`.

## Limitations (intentionally simple)

- Background work uses FastAPI `BackgroundTasks` (in-process). If the server restarts mid-run, that
  run stays `running`. A production version would use a job queue (Celery / RQ / arq).
- Tables are created with `create_all()` at startup; a production version would use Alembic migrations.
- No authentication or rate limiting on the API.
- On Gemini's free tier, Google may use prompts and responses to improve its products, so don't send
  private data through it.
