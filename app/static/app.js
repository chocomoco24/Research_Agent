// Frontend for the Research Agent API: submit a goal, poll the run, show steps and the answer.

const POLL_MS = 2000;
const FINISHED = ["completed", "failed", "timed_out", "max_steps_reached"];

const form = document.getElementById("goal-form");
const goalInput = document.getElementById("goal");
const runButton = document.getElementById("run-button");
const formError = document.getElementById("form-error");
let pollTimer = null;

// Escape text so page content from the web can never inject HTML into this page.
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Tiny markdown subset for the answer: # headings, * bullets, **bold**, `code`, [text](url) and bare URLs.
function formatAnswer(text) {
  return escapeHtml(text)
    .replace(/^#{1,6}\s*(.+)$/gm, '<span class="heading">$1</span>')
    .replace(/^\s*[*-]\s+/gm, "• ")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s)<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}

// Short one-line description of a step, plus optional full output.
function describeStep(step) {
  const c = step.content;
  if (step.type === "tool_call") {
    const labels = { web_search: "🔍 Search", fetch_url: "📄 Read page", save_note: "📝 Save note", finish: "✅ Finish" };
    const arg = c.input.query || c.input.url || c.input.text || "";
    return { label: labels[c.tool] || c.tool, detail: c.tool === "finish" ? "" : arg };
  }
  if (step.type === "tool_result") {
    const isError = c.output.startsWith("Error:");
    return { label: isError ? "⚠️ Tool error" : "Result", detail: `${c.output.length} characters`, full: c.output };
  }
  return { label: "💬 Model message", detail: "", full: c };
}

function renderRun(run) {
  document.getElementById("run-view").classList.remove("hidden");
  document.getElementById("run-title").textContent = `Run #${run.id}`;

  const badge = document.getElementById("run-status");
  badge.textContent = run.status.replaceAll("_", " ");
  badge.className = `badge ${run.status}`;

  document.getElementById("run-goal").textContent = run.goal;
  document.getElementById("run-meta").textContent =
    `${new Date(run.created_at).toLocaleString()} · tokens in/out: ${run.input_tokens} / ${run.output_tokens}`;

  const answer = document.getElementById("run-answer");
  if (run.final_answer) {
    answer.classList.remove("placeholder");
    answer.innerHTML = formatAnswer(run.final_answer);
  } else {
    answer.classList.add("placeholder");
    answer.textContent = "The agent is working… steps appear below as they happen.";
  }

  const list = document.getElementById("run-steps");
  list.innerHTML = "";
  for (const step of run.steps) {
    const { label, detail, full } = describeStep(step);
    const li = document.createElement("li");
    li.className = step.type;
    li.innerHTML = `<div class="step-label">${step.step_number}. ${escapeHtml(label)}</div>` +
      (detail ? `<div class="step-detail">${escapeHtml(detail)}</div>` : "");
    if (full) {
      li.innerHTML += `<details><summary>Show full text</summary><pre>${escapeHtml(full)}</pre></details>`;
    }
    list.appendChild(li);
  }
  if (run.steps.length === 0) list.innerHTML = '<li class="step-detail">No steps yet.</li>';
}

async function loadRun(id) {
  clearTimeout(pollTimer);
  const res = await fetch(`/runs/${id}`);
  if (!res.ok) return;
  const run = await res.json();
  renderRun(run);

  if (FINISHED.includes(run.status)) {
    runButton.disabled = false;
    loadRecentRuns();
  } else {
    pollTimer = setTimeout(() => loadRun(id), POLL_MS); // keep polling until the run finishes
  }
}

async function loadRecentRuns() {
  const list = document.getElementById("recent-runs");
  const runs = await (await fetch("/runs?limit=20")).json();
  list.innerHTML = runs.length ? "" : '<li class="empty">No runs yet.</li>';
  for (const run of runs) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="recent-goal">#${run.id} · ${escapeHtml(run.goal)}</span>` +
      `<span class="badge ${run.status}">${run.status.replaceAll("_", " ")}</span>`;
    li.addEventListener("click", () => {
      loadRun(run.id);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    list.appendChild(li);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.textContent = "";
  runButton.disabled = true;

  const res = await fetch("/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal: goalInput.value }),
  });
  const data = await res.json();

  if (!res.ok) {
    // 422 returns a list of validation errors; other errors return a string.
    formError.textContent = Array.isArray(data.detail)
      ? data.detail.map((e) => e.msg).join("; ")
      : data.detail;
    runButton.disabled = false;
    return;
  }
  loadRun(data.id);
  loadRecentRuns();
});

// Ctrl/Cmd + Enter submits the form.
goalInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) form.requestSubmit();
});

loadRecentRuns();
