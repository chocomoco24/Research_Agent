"""The agent loop.

    goal -> [call Gemini] -> Gemini asks for a tool -> [run tool] -> send result back -> repeat
                                 `-> Gemini calls finish(answer) -> done

Every model message, tool call and tool result is written to the `steps` table
as it happens, so GET /runs/{id} shows progress live.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from google import genai
from google.genai import errors, types

from app import config
from app.db.database import SessionLocal
from app.db.models import Run, Step
from app.tools.research_tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a research agent. Answer the user's research goal using the tools.
Today's date is {today}.

How to work:
1. Use web_search to find sources, then fetch_url to read the best 1-3 of them.
   Prefer official and primary sources (official docs, release notes) over blogs and forums.
2. Use save_note to record key facts together with their source URL.
3. Ground every claim in what you read during this run. Your training data may be out of date:
   if a source disagrees with what you remember (for example about the "latest" version), trust the source.
   Do not add names, numbers, dates or details that did not appear in a tool result. Fetched pages are
   truncated, so if a detail was not in the text you received, leave it out or say it could not be verified.
4. You have a limited budget of {max_steps} turns, so be efficient.
5. Always end by calling finish with the complete answer, citing the source URL for each point."""


@dataclass
class ToolCall:
    id: str | None
    name: str
    args: dict


@dataclass
class ModelTurn:
    """The parts of a model response the loop cares about, independent of the provider's format."""
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    content: object = None  # the raw turn, appended to history unchanged
    stop_reason: str = ""


def build_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT.format(max_steps=config.MAX_STEPS, today=date.today().isoformat()),
        tools=[types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"], description=t["description"], parameters_json_schema=t["parameters"]
            )
            for t in TOOL_SCHEMAS
        ])],
        # We run the tools ourselves in the loop below, so turn off the SDK's automatic calling.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(thinking_level=config.GEMINI_THINKING_LEVEL),
    )


async def call_model(contents: list) -> ModelTurn:
    """One call to Gemini, converted to a ModelTurn. Kept as a separate function so tests can replace it."""
    client = genai.Client(
        api_key=config.GEMINI_API_KEY,
        # Retry temporary server errors (e.g. 503 "model overloaded") with exponential backoff.
        # 429 is NOT retried: free-tier quotas are per day, so retrying just burns more quota.
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(
            attempts=4, http_status_codes=[500, 502, 503, 504]
        )),
    )
    response = await client.aio.models.generate_content(
        model=config.GEMINI_MODEL, contents=contents, config=build_config()
    )
    return parse_response(response)


def parse_response(response: types.GenerateContentResponse) -> ModelTurn:
    usage = response.usage_metadata
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    # Thinking tokens are billed as output tokens.
    output_tokens = ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0

    candidate = response.candidates[0] if response.candidates else None
    if candidate is None or candidate.content is None:
        # Nothing came back, e.g. the prompt was blocked by a safety filter.
        feedback = response.prompt_feedback
        reason = str(feedback.block_reason) if feedback and feedback.block_reason else "empty response"
        return ModelTurn("", [], input_tokens, output_tokens, stop_reason=reason)

    parts = candidate.content.parts or []
    text = "\n".join(p.text for p in parts if p.text and not p.thought).strip()
    tool_calls = [
        ToolCall(id=p.function_call.id, name=p.function_call.name, args=dict(p.function_call.args or {}))
        for p in parts if p.function_call
    ]
    return ModelTurn(text, tool_calls, input_tokens, output_tokens,
                     content=candidate.content, stop_reason=str(candidate.finish_reason))


def tool_result_part(call: ToolCall, result: str) -> types.Part:
    """Wrap a tool's output so the model can match it to the call (by id and name)."""
    key = "error" if result.startswith("Error:") else "result"
    return types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response={key: result}))


class StepLogger:
    """Writes numbered steps for one run to the database."""

    def __init__(self, db, run: Run):
        self.db = db
        self.run = run
        self.count = 0

    def log(self, step_type: str, content):
        self.count += 1
        self.db.add(Step(run_id=self.run.id, step_number=self.count, type=step_type, content=content))
        self.db.commit()


async def agent_loop(db, run: Run) -> None:
    """Run the tool-use loop until finish() is called or MAX_STEPS model calls are used."""
    steps = StepLogger(db, run)
    notes: list[str] = []
    contents = [types.Content(role="user", parts=[types.Part(text=run.goal)])]

    for _ in range(config.MAX_STEPS):
        turn = await call_model(contents)

        # Cost tracking: add this call's token usage to the run's totals.
        run.input_tokens += turn.input_tokens
        run.output_tokens += turn.output_tokens
        db.commit()

        # Log any text the model wrote (its commentary).
        if turn.text:
            steps.log("model_message", turn.text)

        if not turn.tool_calls:
            # The model stopped without calling finish. Accept its text as the answer.
            if turn.text:
                run.status, run.final_answer = "completed", turn.text
            else:
                run.status = "failed"
                run.final_answer = f"Model stopped without an answer ({turn.stop_reason})."
            db.commit()
            return

        # The model's turn must go back into the history unchanged
        # (it carries the function calls and Gemini's thought signatures).
        contents.append(turn.content)

        # Run every requested tool and send all results back in ONE user message.
        result_parts = []
        for call in turn.tool_calls:
            steps.log("tool_call", {"tool": call.name, "input": call.args})

            if call.name == "finish":
                run.status, run.final_answer = "completed", call.args.get("answer", "")
                db.commit()
                return

            result = await execute_tool(call.name, call.args, notes)
            steps.log("tool_result", {"tool": call.name, "output": result})
            result_parts.append(tool_result_part(call, result))

        contents.append(types.Content(role="user", parts=result_parts))

    # Guardrail hit: out of steps. Return whatever notes were gathered as a partial answer.
    run.status = "max_steps_reached"
    run.final_answer = (
        "Step limit reached before the agent finished. Notes gathered so far:\n- " + "\n- ".join(notes)
        if notes else "Step limit reached before the agent finished, and no notes were saved."
    )
    db.commit()


def describe_api_error(e: errors.APIError) -> str:
    """Turn a Gemini API error into a readable reason for the run."""
    if e.code == 429:
        # Google's message says which quota was hit (per-minute or per-day) and when to retry.
        return f"Gemini quota exceeded (429). {(e.message or '')[:400]}"
    if e.code == 503:
        return "The Gemini model is overloaded right now (503). Try again later or set another GEMINI_MODEL."
    if e.code in (400, 401, 403) and "API key" in (e.message or ""):
        return "Gemini rejected the API key. Check GEMINI_API_KEY."
    return f"Gemini API error {e.code}: {e.message}"


async def run_agent(run_id: int) -> None:
    """Entry point used by the API's background task. Owns its own DB session
    (the request's session is closed by the time this runs) and applies the timeout."""
    db = SessionLocal()
    try:
        run = db.get(Run, run_id)
        run.status = "running"
        db.commit()
        try:
            await asyncio.wait_for(agent_loop(db, run), timeout=config.RUN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            run.status = "timed_out"
            run.final_answer = f"Run exceeded the {config.RUN_TIMEOUT_SECONDS}s time limit."
        except errors.APIError as e:
            run.status, run.final_answer = "failed", describe_api_error(e)
        except Exception as e:  # last resort so a run never stays stuck in "running"
            logger.exception("Run %s crashed", run_id)
            run.status, run.final_answer = "failed", f"Unexpected error: {e}"
        db.commit()
    finally:
        db.close()
