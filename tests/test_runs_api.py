"""API tests. The LLM is mocked: call_model returns scripted turns, so no
real API calls (or quota) are used, and the result is deterministic."""
import pytest
from google.genai import types

from app.agent import loop
from app.agent.loop import ModelTurn, ToolCall


def turn(text="", tool_calls=(), stop_reason="STOP"):
    return ModelTurn(text=text, tool_calls=list(tool_calls), input_tokens=100, output_tokens=20,
                     content=types.Content(role="model", parts=[]), stop_reason=stop_reason)


@pytest.fixture
def scripted_model(monkeypatch):
    """Replace the real Gemini call with a list of canned turns."""
    def install(turns):
        calls = []

        async def fake_call_model(contents):
            calls.append(list(contents))  # copy: the loop keeps appending to the same list
            return turns[len(calls) - 1]

        monkeypatch.setattr(loop, "call_model", fake_call_model)
        return calls

    return install


def test_post_run_executes_agent_and_logs_steps(client, scripted_model):
    calls = scripted_model([
        turn("I'll save a note first.", [ToolCall("call_1", "save_note", {"text": "fact A"})]),
        turn(tool_calls=[ToolCall("call_2", "finish", {"answer": "Final answer: fact A"})]),
    ])

    r = client.post("/runs", json={"goal": "Research something useful"})
    assert r.status_code == 202
    run_id = r.json()["id"]

    # TestClient runs background tasks before returning, so the run is already done.
    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "completed"
    assert run["final_answer"] == "Final answer: fact A"
    assert run["input_tokens"] == 200 and run["output_tokens"] == 40
    assert [s["type"] for s in run["steps"]] == ["model_message", "tool_call", "tool_result", "tool_call"]
    assert run["steps"][1]["content"] == {"tool": "save_note", "input": {"text": "fact A"}}

    # The second model call must include the tool result, linked to the call by id and name.
    result = calls[1][-1].parts[0].function_response
    assert (result.id, result.name) == ("call_1", "save_note")
    assert result.response == {"result": "Note saved (1 notes so far)."}


def test_step_limit_returns_notes_as_partial_answer(client, scripted_model, monkeypatch):
    monkeypatch.setattr(loop.config, "MAX_STEPS", 2)
    scripted_model([
        turn(tool_calls=[ToolCall("call_1", "save_note", {"text": "fact A"})]),
        turn(tool_calls=[ToolCall("call_2", "save_note", {"text": "fact B"})]),
    ])

    run_id = client.post("/runs", json={"goal": "A goal that never finishes"}).json()["id"]
    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "max_steps_reached"
    assert "fact A" in run["final_answer"] and "fact B" in run["final_answer"]


def test_post_run_rejects_bad_input(client):
    assert client.post("/runs", json={}).status_code == 422
    assert client.post("/runs", json={"goal": "   "}).status_code == 422


def test_post_run_without_api_key(client, monkeypatch):
    monkeypatch.setattr(loop.config, "GEMINI_API_KEY", "")
    r = client.post("/runs", json={"goal": "Research something useful"})
    assert r.status_code == 500
    assert "GEMINI_API_KEY" in r.json()["detail"]


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Research Agent" in r.text
    assert client.get("/static/app.js").status_code == 200


def test_get_missing_run_returns_404(client):
    assert client.get("/runs/9999").status_code == 404


def test_list_runs(client, scripted_model):
    scripted_model([turn("Done.")] * 2)
    client.post("/runs", json={"goal": "First research goal"})
    client.post("/runs", json={"goal": "Second research goal"})

    runs = client.get("/runs").json()
    assert [r["goal"] for r in runs] == ["Second research goal", "First research goal"]  # newest first


def test_parse_response_extracts_text_tool_calls_and_tokens():
    """parse_response turns a real Gemini response object into a ModelTurn."""
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(
            content=types.Content(role="model", parts=[
                types.Part(text="thinking...", thought=True),  # thought summaries are not logged
                types.Part(text="Let me search."),
                types.Part(function_call=types.FunctionCall(id="c1", name="web_search", args={"query": "python"})),
            ]),
            finish_reason="STOP",
        )],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=50, candidates_token_count=10, thoughts_token_count=5
        ),
    )
    result = loop.parse_response(response)
    assert result.text == "Let me search."
    assert result.tool_calls == [ToolCall("c1", "web_search", {"query": "python"})]
    assert (result.input_tokens, result.output_tokens) == (50, 15)


def test_system_prompt_includes_todays_date():
    """Without the date, the model assumed its training year and reported an outdated 'latest' version."""
    from datetime import date
    assert date.today().isoformat() in loop.build_config().system_instruction


def test_api_errors_become_readable_reasons():
    from google.genai import errors
    quota = errors.ClientError(429, {"error": {"code": 429, "message": "Quota exceeded ... limit: 20"}})
    overloaded = errors.ServerError(503, {"error": {"code": 503, "message": "high demand"}})
    assert "limit: 20" in loop.describe_api_error(quota)
    assert "overloaded" in loop.describe_api_error(overloaded)


def test_parse_response_handles_blocked_prompt():
    response = types.GenerateContentResponse(
        candidates=[], prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY")
    )
    result = loop.parse_response(response)
    assert result.text == "" and result.tool_calls == []
    assert "SAFETY" in result.stop_reason
