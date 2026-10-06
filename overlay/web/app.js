/* Task overlay -- thin client.
 *
 * Deliberately holds NO business logic. It does not decide what is safe, what
 * is approvable, or what may run: it renders what the API returns and calls the
 * API back. Buttons are disabled purely for usability -- the server re-checks
 * every rule, so a tampered client gains nothing.
 *
 * To point a desktop shell (Tauri/Electron) at a server, set API_BASE. That is
 * the only change needed; nothing else in this file is browser-specific.
 */

const API_BASE = window.OVERLAY_API_BASE || "";

const state = {
  runs: [],
  selectedId: null,
  tickets: [],
  detail: null,
  audit: [],
  liveTickets: [],
  // Filtering is presentation, so it lives here. Policy stays server-side.
  queueFilter: { bucket: "you", search: "", group: "", sort: "newest" },
  inboxFilter: { status: "", search: "", group: "", matchedOnly: false },
};

/* ================================================================
 * Vocabulary: one place that turns internal codes into plain words.
 * The server owns the codes; this owns how they read to a person.
 * ================================================================ */

const RISK_INFO = {
  R0: {
    word: "Read-only",
    help: "Looks at things without changing anything.",
  },
  R1: {
    word: "Low impact",
    help: "A small, reversible change such as posting a note on the ticket.",
  },
  R2: {
    word: "Real change",
    help: "Changes a running system, so it needs its own separate approval.",
  },
  R3: {
    word: "High impact",
    help: "Destructive or hard to undo. Always done by a person.",
  },
  RX: {
    word: "Unclassified",
    help: "Safety cannot be proven from the instruction, so a person does it.",
  },
};

/** Run states grouped into what a person actually needs to do. */
const STATE_INFO = {
  needs_parameters: {
    word: "Needs details",
    bucket: "you",
    action: "Fill in the missing values before anything can be approved.",
  },
  awaiting_approval: {
    word: "Needs approval",
    bucket: "you",
    action: "Review the next group of steps and approve or reject it.",
  },
  ready: {
    word: "Ready to run",
    bucket: "you",
    action: "Approved and waiting. Press Run to let the agent do it.",
  },
  paused_for_human: {
    word: "Your turn",
    bucket: "you",
    action: "The agent has stopped. This step is yours to do.",
  },
  running: {
    word: "Running",
    bucket: "active",
    action: "The agent is working through the approved steps.",
  },
  blocked: {
    word: "Blocked",
    bucket: "attention",
    action: "Something was rejected. Re-approve or cancel it.",
  },
  failed: {
    word: "Failed",
    bucket: "attention",
    action: "A step failed. Review the audit trail below.",
  },
  completed: { word: "Done", bucket: "done", action: "All steps are complete." },
  cancelled: { word: "Cancelled", bucket: "done", action: "This run was cancelled." },
  proposed: {
    word: "New",
    bucket: "you",
    action: "Newly matched. Review the plan below.",
  },
};

/** Live inbox statuses in plain words. */
const LIVE_STATUS_INFO = {
  new: { word: "Ready to triage", help: "A procedure matched this ticket." },
  no_match: { word: "No procedure", help: "Nothing codified yet, so it stays with a person." },
  proposed: { word: "In the queue", help: "A run was already created for this." },
  ignored: { word: "Dismissed", help: "Someone chose to skip this." },
};

const QUEUE_BUCKETS = [
  { id: "you", label: "Needs you", help: "Waiting on a decision or an action from a person." },
  { id: "active", label: "Running", help: "The agent is working." },
  { id: "attention", label: "Problems", help: "Blocked or failed." },
  { id: "done", label: "Finished", help: "Completed or cancelled." },
  { id: "all", label: "All", help: "Every ticket in the queue." },
];

function stateInfo(stateName) {
  return (
    STATE_INFO[stateName] || {
      word: String(stateName || "").replace(/_/g, " "),
      bucket: "all",
      action: "",
    }
  );
}

function riskInfo(code) {
  return RISK_INFO[code] || { word: code, help: "" };
}

/* ---------------------------------------------------------------- helpers */

const $ = (id) => document.getElementById(id);

/** Escape untrusted text. Ticket content comes from ServiceNow; never trust it. */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function actor() {
  return $("actor").value.trim() || "unknown";
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`${API_BASE}/api${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Actor": actor(),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  let payload = null;
  try {
    payload = await res.json();
  } catch {
    /* empty body */
  }

  if (!res.ok) {
    const detail = payload?.detail;
    // Some errors carry structure (e.g. a duplicate run names the existing
    // one). Keep it on the Error so callers can offer a real choice.
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `Request failed (${res.status})`;
    const err = new Error(message);
    err.status = res.status;
    if (detail && typeof detail === "object") err.info = detail;
    throw err;
  }
  return payload;
}

function setMsg(el, text, kind = "") {
  el.textContent = text;
  el.className = `msg ${kind}`;
}

function shortTime(iso) {
  if (!iso) return "";
  return String(iso).replace("T", " ").replace("+00:00", "Z");
}

/* ------------------------------------------------------------------ intake */

async function loadTickets() {
  const { tickets } = await api("/sample-tickets?limit=60");
  state.tickets = tickets;

  const sel = $("ticket-picker");
  sel.innerHTML = "";

  // Group by whether a procedure exists, so it is obvious which tickets the
  // overlay can act on. The rest are shown deliberately -- "no procedure" is a
  // correct answer, not a failure.
  const withProc = tickets.filter((t) => t._match?.matched);
  const without = tickets.filter((t) => !t._match?.matched);

  const addGroup = (label, items) => {
    if (!items.length) return;
    const group = document.createElement("optgroup");
    group.label = label;
    items.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = String(tickets.indexOf(t));
      const num = t.number || "(no number)";
      const desc = (t.short_description || "(no description)").slice(0, 64);
      opt.textContent = t._match?.matched
        ? `${num} — ${desc}  ·  ${t._match.procedure_name}`
        : `${num} — ${desc}`;
      group.appendChild(opt);
    });
    sel.appendChild(group);
  };

  addGroup(`A procedure applies (${withProc.length})`, withProc);
  addGroup(`No procedure yet — stays with a human (${without.length})`, without);

  $("intake-summary").textContent =
    `${withProc.length} of ${tickets.length} scanned tickets have a codified procedure.`;
}

async function proposeSelected() {
  const msg = $("intake-msg");
  const idx = Number($("ticket-picker").value);
  const ticket = state.tickets[idx];
  if (!ticket) {
    setMsg(msg, "Pick a ticket first.", "error");
    return;
  }

  $("propose").disabled = true;
  setMsg(msg, "Matching…");
  try {
    const run = await createRun(ticket, msg);
    if (run) {
      setMsg(msg, `Matched ${run.procedure_name}.`, "ok");
      await refreshQueue();
      await select(run.run_id);
    }
  } catch (err) {
    setMsg(msg, err.message, "error");
  } finally {
    $("propose").disabled = false;
  }
}

/** Create a run, asking first if the ticket already has one in progress. */
async function createRun(ticket, msg) {
  try {
    return await api("/runs", { method: "POST", body: ticket });
  } catch (err) {
    if (err.info?.code !== "duplicate_run") throw err;

    const go = confirm(
      `${err.info.ticket_id} already has a run in progress ` +
        `(${String(err.info.existing_state).replace(/_/g, " ")}).\n\n` +
        `OK — open the existing run\n` +
        `Cancel — create a second run anyway`
    );

    if (go) {
      // Take them to the run that already exists.
      await refreshQueue();
      await select(err.info.existing_run_id);
      setMsg(msg, "Opened the run that already exists for this ticket.", "ok");
      return null;
    }

    return await api("/runs?allow_duplicate=true", {
      method: "POST",
      body: ticket,
    });
  }
}

/* ------------------------------------------------------------------- queue */

async function refreshQueue() {
  const [{ runs }, summary] = await Promise.all([
    api("/runs?limit=500"),
    api("/summary"),
  ]);
  state.runs = runs;

  $("queue-count").textContent = `${runs.length} total`;

  const bits = [
    `${summary.procedures_loaded} procedures`,
    `${summary.published_procedures ?? 0} published`,
    `${summary.dictionary_entries ?? 0} dictionary terms`,
    `connectors ${summary.connectors_enabled ? "ENABLED" : "disabled"}`,
  ];
  if (summary.live?.enabled) {
    bits.push(`live feed ${summary.live.running ? "polling" : "idle"}`);
  }
  $("footer-summary").textContent = bits.join(" · ");

  if (summary.pending_drafts) {
    $("count-drafts").textContent = String(summary.pending_drafts);
  }
  if (summary.live?.counts?.new) {
    $("count-live").textContent = String(summary.live.counts.new);
  }

  if ((summary.registry_errors || []).length) {
    console.warn("registry errors", summary.registry_errors);
  }

  renderQueueFilters();
  renderQueue();
}

/* -- filtering ----------------------------------------------------------- */

function bucketOf(run) {
  return stateInfo(run.state).bucket;
}

function bucketCounts() {
  const counts = { you: 0, active: 0, attention: 0, done: 0, all: state.runs.length };
  state.runs.forEach((r) => {
    const b = bucketOf(r);
    if (b in counts) counts[b] += 1;
  });
  return counts;
}

function filteredRuns() {
  const f = state.queueFilter;
  const needle = f.search.trim().toLowerCase();

  let rows = state.runs.filter((run) => {
    if (f.bucket !== "all" && bucketOf(run) !== f.bucket) return false;
    if (f.group && (run.assignment_group || "") !== f.group) return false;
    if (needle) {
      const hay = [
        run.ticket_id,
        run.ticket.short_description,
        run.procedure_name,
        run.assignment_group,
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });

  const remaining = (r) => r.total_step_count - r.completed_step_count;
  const sorters = {
    newest: (a, b) => (a.created_at < b.created_at ? 1 : -1),
    oldest: (a, b) => (a.created_at > b.created_at ? 1 : -1),
    remaining: (a, b) => remaining(b) - remaining(a),
    progress: (a, b) => remaining(a) - remaining(b),
  };
  return rows.sort(sorters[f.sort] || sorters.newest);
}

function renderQueueFilters() {
  const counts = bucketCounts();
  const f = state.queueFilter;

  $("queue-buckets").innerHTML = QUEUE_BUCKETS.map(
    (b) => `
    <button type="button" class="seg" data-bucket="${b.id}"
            aria-pressed="${f.bucket === b.id}" title="${esc(b.help)}">
      ${esc(b.label)}
      <span class="seg-count">${counts[b.id] ?? 0}</span>
    </button>`
  ).join("");

  // Group options come from the loaded runs, so the list only offers groups
  // that actually appear.
  const groups = [...new Set(state.runs.map((r) => r.assignment_group).filter(Boolean))].sort();
  const sel = $("queue-group");
  const previous = f.group;
  sel.innerHTML =
    '<option value="">All groups</option>' +
    groups
      .map((g) => `<option value="${esc(g)}">${esc(g)}</option>`)
      .join("");
  sel.value = groups.includes(previous) ? previous : "";
  f.group = sel.value;

  $("queue-buckets")
    .querySelectorAll("[data-bucket]")
    .forEach((btn) => {
      btn.addEventListener("click", () => {
        state.queueFilter.bucket = btn.dataset.bucket;
        renderQueueFilters();
        renderQueue();
      });
    });
}

function renderQueue() {
  const list = $("run-list");
  const rows = filteredRuns();
  const f = state.queueFilter;

  $("queue-showing").textContent =
    rows.length === state.runs.length
      ? `Showing all ${rows.length}`
      : `Showing ${rows.length} of ${state.runs.length}`;

  if (!state.runs.length) {
    list.innerHTML =
      '<li><p class="empty" style="padding:.9rem">Nothing queued yet. Add a ticket above, or pull real ones from the Live tab.</p></li>';
    return;
  }

  if (!rows.length) {
    const label =
      QUEUE_BUCKETS.find((b) => b.id === f.bucket)?.label || f.bucket;
    list.innerHTML = `
      <li><p class="empty" style="padding:.9rem">
        Nothing under <strong>${esc(label)}</strong>${
          f.search ? ` matching “${esc(f.search)}”` : ""
        }.
        <button type="button" class="btn btn-quiet" id="clear-filters">Clear filters</button>
      </p></li>`;
    $("clear-filters")?.addEventListener("click", () => {
      state.queueFilter = { bucket: "all", search: "", group: "", sort: "newest" };
      $("queue-search").value = "";
      $("queue-sort").value = "newest";
      renderQueueFilters();
      renderQueue();
    });
    return;
  }

  list.innerHTML = rows
    .map((run) => {
      const si = stateInfo(run.state);
      const selected = run.run_id === state.selectedId;
      const pct = run.total_step_count
        ? Math.round((run.completed_step_count / run.total_step_count) * 100)
        : 0;

      return `
      <li>
        <button type="button" class="run-item" aria-current="${selected}"
                data-run="${esc(run.run_id)}">
          <span class="run-item-top">
            <span class="ticket">
              ${esc(run.ticket_id)}
              ${run.runs_for_ticket > 1
                ? `<span class="dup-flag"
                         title="This ticket has ${run.runs_for_ticket} runs. Check you are working the right one.">
                     ${run.runs_for_ticket} runs
                   </span>`
                : ""}
            </span>
            <span class="state-tag state-${esc(si.bucket)}">${esc(si.word)}</span>
          </span>
          <p class="desc">${esc(run.ticket.short_description || "(no description)")}</p>
          <span class="progress" role="img"
                aria-label="${run.completed_step_count} of ${run.total_step_count} steps done">
            <span class="progress-bar" style="width:${pct}%"></span>
          </span>
          <span class="meta">
            <span class="owner">${run.completed_step_count}/${run.total_step_count} steps</span>
            <span class="owner owner-ai">${run.ai_step_count} agent</span>
            <span class="owner">${run.human_step_count} you</span>
            ${run.assignment_group
              ? `<span class="owner">${esc(run.assignment_group)}</span>`
              : ""}
          </span>
        </button>
      </li>`;
    })
    .join("");

  list.querySelectorAll("[data-run]").forEach((btn) => {
    btn.addEventListener("click", () => select(btn.dataset.run));
  });
}

/* ------------------------------------------------------------------ detail */

async function select(runId) {
  state.selectedId = runId;
  renderQueue();

  const [run, auditRes] = await Promise.all([
    api(`/runs/${runId}`),
    api(`/runs/${runId}/audit`),
  ]);
  state.detail = run;
  state.audit = auditRes.events;
  renderDetail();
}

function riskBadge(risk, _label) {
  const info = riskInfo(risk);
  // Plain words lead; the code stays as a tooltip for anyone who wants it.
  return `<span class="risk risk-${esc(risk)}" title="${esc(risk)} — ${esc(info.help)}">${esc(info.word)}</span>`;
}

function renderStep(step, run) {
  const done = ["succeeded", "skipped"].includes(step.state);
  const needs = ["needs_human", "blocked", "failed"].includes(step.state);
  const cls = done ? "done" : needs ? "needs" : "";

  const isNextActionable =
    run.steps.find((s) => !["succeeded", "skipped"].includes(s.state))?.step_id ===
    step.step_id;

  const canCompleteManually =
    !done && (step.owner === "human" || needs) && isNextActionable;

  return `
    <div class="step ${cls}">
      <span class="step-num">${step.sequence}</span>
      <div class="step-main">
        <div class="step-desc">${esc(step.description)}</div>
        <p class="step-rendered">${esc(step.rendered)}</p>
        <div class="step-badges">
          ${riskBadge(step.risk, step.risk_label)}
          <span class="owner ${step.owner === "ai" ? "owner-ai" : ""}">${
            step.owner === "ai" ? "agent" : "you"
          }</span>
          ${done
            ? '<span class="owner">done</span>'
            : needs
            ? '<span class="owner">needs you</span>'
            : ""}
        </div>
        ${step.owner === "human" && !done
          ? `<p class="step-why">${esc(step.risk_reason)}</p>` : ""}
        ${step.outcome ? `<p class="step-outcome">→ ${esc(step.outcome)}</p>` : ""}
        ${step.evidence
          ? `<p class="step-evidence">Evidence: ${esc(step.evidence)}
             ${step.completed_by ? `(${esc(step.completed_by)})` : ""}</p>` : ""}
        ${canCompleteManually
          ? `<div class="manual-form">
               <label class="sr-only" for="ev-${esc(step.step_id)}">
                 Evidence for step ${step.sequence}
               </label>
               <input id="ev-${esc(step.step_id)}" type="text"
                      placeholder="What did you do? (required)" />
               <button type="button" class="btn" data-complete="${esc(step.step_id)}">
                 Mark done
               </button>
             </div>` : ""}
      </div>
    </div>`;
}

function renderChunk(chunk, run) {
  const steps = chunk.step_ids
    .map((id) => run.steps.find((s) => s.step_id === id))
    .filter(Boolean);

  const allDone = steps.every((s) => ["succeeded", "skipped"].includes(s.state));
  const anyReady = steps.some((s) => s.state === "ready");
  const nextChunkId = run.steps.find(
    (s) => !["succeeded", "skipped"].includes(s.state)
  )?.chunk_id;
  const isNext = chunk.chunk_id === nextChunkId;

  const agentChunk = chunk.owner === "ai" && chunk.policy !== "manual_only";
  const blocked = run.missing_parameters.length > 0;

  let controls = "";
  if (allDone) {
    controls = '<span class="state-tag state-done">Done</span>';
  } else if (!agentChunk) {
    controls = '<span class="state-tag state-you">You do this</span>';
  } else if (anyReady) {
    controls = `<button type="button" class="btn btn-run"
                  data-execute="${esc(chunk.chunk_id)}">Run these steps</button>`;
  } else {
    controls = `
      <button type="button" class="btn btn-approve" ${blocked ? "disabled" : ""}
              data-approve="${esc(chunk.chunk_id)}"
              title="${blocked ? "Fill in the missing details first" : ""}">
        Let the agent do this
      </button>
      <button type="button" class="btn" data-reject="${esc(chunk.chunk_id)}">
        Refuse
      </button>`;
  }

  return `
    <div class="chunk ${isNext ? "is-next" : ""}">
      <div class="chunk-head">
        <span class="chunk-title">
          ${esc(chunk.label)}
          ${isNext ? '<span class="up-next">up next</span>' : ""}
        </span>
        <span class="chunk-badges">
          ${riskBadge(chunk.risk, chunk.risk_label)}
          <span class="owner ${chunk.owner === "ai" ? "owner-ai" : ""}">${
            chunk.owner === "ai" ? "agent" : "you"
          }</span>
          ${controls}
        </span>
      </div>
      <div class="chunk-body">
        ${steps.map((s) => renderStep(s, run)).join("")}
      </div>
    </div>`;
}

function renderDetail() {
  const run = state.detail;
  if (!run) return;

  const si = stateInfo(run.state);
  $("detail-heading").textContent = `${run.ticket_id} — ${run.procedure_name}`;
  $("run-state").textContent = si.word;
  $("run-state").className = `pill state-tag state-${si.bucket}`;

  const t = run.ticket;
  const params = Object.entries(run.parameters);
  const missing = run.missing_parameters;

  const nextStep = run.steps.find(
    (s) => !["succeeded", "skipped"].includes(s.state)
  );

  const pct = run.total_step_count
    ? Math.round((run.completed_step_count / run.total_step_count) * 100)
    : 0;

  // Lead with the single thing this person should do next.
  const whoNext = nextStep
    ? nextStep.owner === "ai"
      ? "the agent"
      : "you"
    : null;

  $("detail").innerHTML = `
    <div class="next-action next-${esc(si.bucket)}">
      <div class="next-head">
        <strong>${esc(si.word)}</strong>
        ${nextStep
          ? `<span class="hint" style="margin:0">
               next: step ${nextStep.sequence} of ${run.total_step_count}, handled by
               <strong>${esc(whoNext)}</strong>
             </span>`
          : ""}
      </div>
      <p class="next-what">${esc(si.action)}</p>
      <span class="progress progress-lg"
            role="img" aria-label="${run.completed_step_count} of ${run.total_step_count} steps done">
        <span class="progress-bar" style="width:${pct}%"></span>
      </span>
      <p class="hint" style="margin:.3rem 0 0">
        ${run.completed_step_count} of ${run.total_step_count} steps done ·
        ${run.ai_step_count} can be done by the agent ·
        ${run.human_step_count} need a person
      </p>
    </div>

    <details class="detail-meta">
      <summary>Ticket &amp; matching details</summary>
      <dl class="kv">
        <dt>Assignment group</dt><dd>${esc(t.assignment_group || "—")}</dd>
        <dt>Configuration item</dt><dd>${esc(t.cmdb_ci || "—")}</dd>
        <dt>Procedure</dt>
        <dd>${esc(run.procedure_id)}
            <span class="owner">${esc(run.procedure_version)}</span></dd>
        <dt>Why it matched</dt>
        <dd>score ${run.match_score}
            ${run.matched_keywords.length
              ? `· keywords: ${esc(run.matched_keywords.join(", "))}` : ""}</dd>
        ${run.alternatives.length
          ? `<dt>Also considered</dt><dd>${esc(
              run.alternatives.map((a) => `${a.procedure_name} (${a.score})`).join(", ")
            )}</dd>`
          : ""}
      </dl>
    </details>

    <p class="ticket-desc">${esc(t.short_description || "(no description)")}</p>

    <h3>Parameters</h3>
    ${missing.length
      ? `<p class="warn">Missing required: ${esc(missing.join(", "))}.
           Nothing can be approved until these are filled in.</p>` : ""}
    <div class="param-grid">
      ${[...new Set([...params.map(([k]) => k), ...missing])]
        .map((key) => {
          const val = run.parameters[key] ?? "";
          const isMissing = missing.includes(key);
          return `<div>
            <label for="p-${esc(key)}">${esc(key)}</label>
            <input id="p-${esc(key)}" data-param="${esc(key)}"
                   class="${isMissing ? "missing" : ""}"
                   value="${esc(val)}" />
          </div>`;
        }).join("")}
    </div>
    <div class="btn-row">
      <button type="button" class="btn" id="save-params">Save parameters</button>
      <span class="hint" style="margin:0;align-self:center">
        Editing invalidates any approval already given.
      </span>
    </div>
    <p id="detail-msg" class="msg" role="status"></p>

    <h3>Plan</h3>
    <p class="hint">
      Steps are grouped so one approval never covers a bigger change than you
      reviewed. The agent stops at every group it is not allowed to run.
    </p>
    ${run.chunks.map((c) => renderChunk(c, run)).join("")}

    <h3>Audit trail</h3>
    <ul class="audit">
      ${state.audit.map((e) => `
        <li class="${e.event.includes("refused") ? "refused" : ""}">
          <time>${esc(shortTime(e.created_at))}</time>
          <span><span class="ev">${esc(e.event)}</span>
                <span class="who">· ${esc(e.actor)}</span>
                ${e.detail?.reason ? `<br><span class="who">${esc(e.detail.reason)}</span>` : ""}
          </span>
        </li>`).join("")}
    </ul>
  `;

  wireDetail(run);
}

function wireDetail(run) {
  const msg = $("detail-msg");

  const act = async (fn) => {
    try {
      setMsg(msg, "Working…");
      await fn();
      setMsg(msg, "", "");
      await refreshQueue();
      await select(run.run_id);
    } catch (err) {
      setMsg(msg, err.message, "error");
    }
  };

  $("save-params")?.addEventListener("click", () => {
    const parameters = {};
    document.querySelectorAll("[data-param]").forEach((i) => {
      parameters[i.dataset.param] = i.value.trim();
    });
    act(() =>
      api(`/runs/${run.run_id}/parameters`, {
        method: "PATCH",
        body: { parameters },
      })
    );
  });

  document.querySelectorAll("[data-approve]").forEach((btn) => {
    btn.addEventListener("click", () =>
      act(() =>
        api(`/runs/${run.run_id}/chunks/${btn.dataset.approve}/decision`, {
          method: "POST",
          body: { decision: "approved", reason: "approved in overlay" },
        })
      )
    );
  });

  document.querySelectorAll("[data-reject]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const reason = prompt("Why are you rejecting this chunk?");
      if (reason === null) return;
      act(() =>
        api(`/runs/${run.run_id}/chunks/${btn.dataset.reject}/decision`, {
          method: "POST",
          body: { decision: "rejected", reason },
        })
      );
    });
  });

  document.querySelectorAll("[data-execute]").forEach((btn) => {
    btn.addEventListener("click", () =>
      act(() =>
        api(`/runs/${run.run_id}/chunks/${btn.dataset.execute}/execute`, {
          method: "POST",
        })
      )
    );
  });

  document.querySelectorAll("[data-complete]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const stepId = btn.dataset.complete;
      const evidence = $(`ev-${stepId}`)?.value.trim() || "";
      if (!evidence) {
        setMsg(msg, "Evidence is required to mark a step done.", "error");
        return;
      }
      act(() =>
        api(`/runs/${run.run_id}/steps/${stepId}/complete`, {
          method: "POST",
          body: { evidence },
        })
      );
    });
  });
}

/* =================================================================
 * KNOWLEDGE: uploads, review, publish, dictionary
 * ================================================================= */

async function loadFormats() {
  const f = await api("/knowledge/formats");
  const mb = (f.max_bytes / (1024 * 1024)).toFixed(1);
  $("upload-formats").textContent =
    `Handbook: ${f.handbook.join(", ")} · Dictionary: ${f.dictionary.join(", ")} · max ${mb} MB`;
}

async function doUpload() {
  const msg = $("upload-msg");
  const input = $("upload-file");
  const kind = $("upload-kind").value;
  const file = input.files?.[0];

  if (!file) {
    setMsg(msg, "Choose a file first.", "error");
    return;
  }

  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);

  $("do-upload").disabled = true;
  setMsg(msg, "Uploading…");

  try {
    // FormData sets its own multipart Content-Type; do not override it.
    const res = await fetch(`${API_BASE}/api/knowledge/upload`, {
      method: "POST",
      headers: { "X-Actor": actor() },
      body: form,
    });
    const payload = await res.json().catch(() => null);
    if (!res.ok) {
      throw new Error(
        typeof payload?.detail === "string" ? payload.detail : `Upload failed (${res.status})`
      );
    }

    setMsg(msg, payload.note, "ok");
    renderUploadResult(payload);
    input.value = "";
    await refreshKnowledge();
    await refreshQueue();
  } catch (err) {
    setMsg(msg, err.message, "error");
    $("upload-result").innerHTML = "";
  } finally {
    $("do-upload").disabled = false;
  }
}

function renderUploadResult(payload) {
  const s = payload.source;
  const warnings = s.warnings || [];

  $("upload-result").innerHTML = `
    <div class="result-card">
      <div class="result-head">
        <strong>${esc(s.filename)}</strong>
        <span class="owner">${esc(s.kind)}</span>
        <span class="owner">${esc(s.file_format)}</span>
        <span class="owner">${s.size_bytes.toLocaleString()} bytes</span>
      </div>
      <p class="hint">
        ${payload.drafts.length
          ? `${payload.drafts.length} draft procedure(s) created — none are active yet.`
          : `${payload.entries.length} dictionary entr(y/ies) added and active.`}
      </p>
      ${warnings.length
        ? `<div class="warn"><strong>Warnings</strong><ul>${
            warnings.map((w) => `<li>${esc(w)}</li>`).join("")
          }</ul></div>`
        : ""}
    </div>`;
}

async function refreshKnowledge() {
  const [sources, drafts, published, dict] = await Promise.all([
    api("/knowledge/sources"),
    api("/knowledge/drafts"),
    api("/knowledge/published"),
    api("/knowledge/dictionary"),
  ]);

  renderSources(sources.sources);
  await renderDrafts(drafts.drafts);
  renderPublished(published.published);
  renderDictionary(dict.entries);
}

function renderSources(sources) {
  if (!sources.length) {
    $("source-list").innerHTML = '<p class="empty">Nothing uploaded yet.</p>';
    return;
  }
  $("source-list").innerHTML = `
    <table>
      <thead><tr><th>File</th><th>Type</th><th>Result</th><th>By</th><th>When</th></tr></thead>
      <tbody>
        ${sources.map((s) => `
          <tr>
            <td>${esc(s.filename)}</td>
            <td>${esc(s.kind)}</td>
            <td>${s.status === "failed"
                  ? `<span class="risk risk-R3">failed</span>`
                  : esc(s.kind === "handbook"
                      ? `${s.draft_count} draft(s)`
                      : `${s.entry_count} entr(ies)`)}</td>
            <td>${esc(s.uploaded_by)}</td>
            <td>${esc(shortTime(s.uploaded_at))}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

async function renderDrafts(drafts) {
  const pending = drafts.filter((d) => d.status === "draft");
  $("draft-count").textContent = `${pending.length} awaiting review`;
  $("count-drafts").textContent = pending.length ? String(pending.length) : "";

  if (!drafts.length) {
    $("draft-list").innerHTML =
      '<p class="empty">No drafts. Upload a handbook to create some.</p>';
    return;
  }

  // Fetch the per-step effect for each pending draft so review is informed.
  const detailed = await Promise.all(
    pending.map((d) => api(`/knowledge/drafts/${d.draft_id}`).catch(() => null))
  );

  const reviewed = drafts.filter((d) => d.status !== "draft");

  $("draft-list").innerHTML =
    detailed.filter(Boolean).map(renderDraftCard).join("") +
    (reviewed.length
      ? `<details class="reviewed">
           <summary>${reviewed.length} already reviewed</summary>
           <table>
             <thead><tr><th>Procedure</th><th>Status</th><th>By</th><th>Note</th></tr></thead>
             <tbody>
               ${reviewed.map((d) => `
                 <tr>
                   <td>${esc(d.procedure_id)}</td>
                   <td>${esc(d.status)}</td>
                   <td>${esc(d.reviewed_by || "")}</td>
                   <td>${esc(d.review_note || "")}</td>
                 </tr>`).join("")}
             </tbody>
           </table>
         </details>`
      : "");

  wireDrafts();
}

function renderDraftCard(d) {
  const eff = d.effect || { steps: 0, agent_steps: 0, human_steps: 0, breakdown: [] };
  const blocked = (d.validation_errors || []).length > 0;

  return `
    <div class="draft-card">
      <div class="draft-head">
        <div>
          <strong>${esc(d.name)}</strong>
          <div class="hint" style="margin:.15rem 0 0">
            <code>${esc(d.procedure_id)}</code>
            ${d.conflicts_with_existing
              ? ' · <span class="risk risk-R2">replaces an existing procedure</span>'
              : ""}
          </div>
        </div>
        <span class="chunk-badges">
          <span class="owner owner-ai">${eff.agent_steps} agent</span>
          <span class="owner">${eff.human_steps} human</span>
        </span>
      </div>

      ${blocked
        ? `<div class="warn"><strong>Cannot publish until fixed</strong><ul>${
            d.validation_errors.map((e) => `<li>${esc(e)}</li>`).join("")
          }</ul></div>`
        : ""}

      ${(d.risk_warnings || []).length
        ? `<div class="warn"><strong>Read before publishing</strong><ul>${
            d.risk_warnings.map((w) => `<li>${esc(w)}</li>`).join("")
          }</ul></div>`
        : ""}

      <table class="step-table">
        <thead><tr><th>#</th><th>Action</th><th>Risk</th><th>Owner</th><th>Description</th></tr></thead>
        <tbody>
          ${eff.breakdown.map((b) => `
            <tr>
              <td>${b.sequence}</td>
              <td><code>${esc(b.action)}</code></td>
              <td><span class="risk risk-${esc(b.risk)}">${esc(b.risk)}</span></td>
              <td>${esc(b.owner)}</td>
              <td>${esc(b.description || "")}</td>
            </tr>`).join("")}
        </tbody>
      </table>

      <div class="btn-row">
        <button type="button" class="btn btn-approve" ${blocked ? "disabled" : ""}
                data-publish="${esc(d.draft_id)}">
          Publish to live rule set
        </button>
        <button type="button" class="btn" data-reject-draft="${esc(d.draft_id)}">
          Reject
        </button>
      </div>
    </div>`;
}

function wireDrafts() {
  const msg = $("upload-msg");
  const act = async (fn) => {
    try {
      setMsg(msg, "Working…");
      const r = await fn();
      setMsg(
        msg,
        r?.procedures_loaded
          ? `Published. ${r.procedures_loaded} procedures now active.`
          : "Done.",
        "ok"
      );
      await refreshKnowledge();
      await refreshQueue();
    } catch (err) {
      setMsg(msg, err.message, "error");
    }
  };

  document.querySelectorAll("[data-publish]").forEach((b) => {
    b.addEventListener("click", () =>
      act(() =>
        api(`/knowledge/drafts/${b.dataset.publish}/publish`, {
          method: "POST",
          body: { note: "published from overlay" },
        })
      )
    );
  });

  document.querySelectorAll("[data-reject-draft]").forEach((b) => {
    b.addEventListener("click", () => {
      const note = prompt("Why are you rejecting this draft?");
      if (note === null) return;
      act(() =>
        api(`/knowledge/drafts/${b.dataset.rejectDraft}/reject`, {
          method: "POST",
          body: { note },
        })
      );
    });
  });
}

function renderPublished(rows) {
  if (!rows.length) {
    $("published-list").innerHTML =
      '<p class="empty">Nothing published. Built-in procedures are still active.</p>';
    return;
  }
  $("published-list").innerHTML = `
    <table>
      <thead><tr><th>Procedure</th><th>By</th><th>When</th><th>Active</th></tr></thead>
      <tbody>
        ${rows.map((r) => `
          <tr>
            <td>${esc(r.name)}<div class="hint" style="margin:0"><code>${esc(r.procedure_id)}</code></div></td>
            <td>${esc(r.published_by)}</td>
            <td>${esc(shortTime(r.published_at))}</td>
            <td>
              <button type="button" class="btn"
                      data-toggle-pub="${esc(r.procedure_id)}"
                      data-enabled="${r.enabled}">
                ${r.enabled ? "Disable" : "Enable"}
              </button>
            </td>
          </tr>`).join("")}
      </tbody>
    </table>`;

  document.querySelectorAll("[data-toggle-pub]").forEach((b) => {
    b.addEventListener("click", async () => {
      try {
        await api(`/knowledge/published/${b.dataset.togglePub}`, {
          method: "PATCH",
          body: { enabled: b.dataset.enabled !== "true" },
        });
        await refreshKnowledge();
        await refreshQueue();
      } catch (err) {
        setMsg($("upload-msg"), err.message, "error");
      }
    });
  });
}

function renderDictionary(entries) {
  if (!entries.length) {
    $("dictionary-list").innerHTML =
      '<p class="empty">No dictionary entries. Upload one to widen matching.</p>';
    return;
  }
  $("dictionary-list").innerHTML = `
    <table>
      <thead><tr><th>Term</th><th>Also written as</th><th>Active</th></tr></thead>
      <tbody>
        ${entries.map((e) => `
          <tr>
            <td>${esc(e.canonical)}</td>
            <td>${esc((e.aliases || []).join(", ")) || '<span class="hint">none</span>'}</td>
            <td>
              <button type="button" class="btn" data-toggle-dict="${esc(e.entry_id)}"
                      data-enabled="${e.enabled}">
                ${e.enabled ? "Disable" : "Enable"}
              </button>
            </td>
          </tr>`).join("")}
      </tbody>
    </table>`;

  document.querySelectorAll("[data-toggle-dict]").forEach((b) => {
    b.addEventListener("click", async () => {
      try {
        await api(`/knowledge/dictionary/${b.dataset.toggleDict}`, {
          method: "PATCH",
          body: { enabled: b.dataset.enabled !== "true" },
        });
        await refreshKnowledge();
      } catch (err) {
        $("dict-test-result").innerHTML =
          `<p class="msg error">${esc(err.message)}</p>`;
      }
    });
  });
}

async function testDictionary() {
  const text = $("dict-test").value.trim();
  const group = $("dict-test-group").value.trim();
  const out = $("dict-test-result");

  if (!text) {
    out.innerHTML = '<p class="msg error">Type a phrase first.</p>';
    return;
  }

  try {
    const r = await api("/knowledge/dictionary/test", {
      method: "POST",
      body: { text, assignment_group: group },
    });

    // Show the near misses. A dictionary can fire and the ticket still not
    // match, because one keyword scores below the threshold.
    out.innerHTML = `
      <div class="result-card">
        <p class="msg ${r.matched ? "ok" : ""}" style="margin:0 0 .35rem">
          ${r.matched
            ? `Matches <strong>${esc(r.procedure_name)}</strong> — score ${r.score} of ${r.threshold} needed.`
            : `No match. ${esc(r.explanation)}`}
        </p>
        <p class="hint" style="margin:0">
          ${r.terms_applied.length
            ? `Dictionary added: <strong>${esc(r.terms_applied.join(", "))}</strong>`
            : "No dictionary terms applied to this text."}
        </p>
        ${r.near_misses.length
          ? `<table class="step-table">
               <thead><tr><th>Candidate</th><th>Score</th><th>Keywords hit</th><th>Group</th></tr></thead>
               <tbody>
                 ${r.near_misses.map((n) => `
                   <tr>
                     <td>${esc(n.procedure_name)}</td>
                     <td>${n.score}</td>
                     <td>${esc(n.matched_keywords.join(", ")) || "—"}</td>
                     <td>${n.group_match ? "yes" : "no"}</td>
                   </tr>`).join("")}
               </tbody>
             </table>`
          : ""}
      </div>`;
  } catch (err) {
    out.innerHTML = `<p class="msg error">${esc(err.message)}</p>`;
  }
}

/* =================================================================
 * LIVE: feed config and inbox
 * ================================================================= */

async function refreshLive() {
  const [status, cfg] = await Promise.all([
    api("/live/status"),
    api("/live/config"),
  ]);

  renderLiveStatus(status);

  // Only repopulate inputs the user is not currently editing.
  const fill = (id, value) => {
    const el = $(id);
    if (el && document.activeElement !== el) el.value = value;
  };
  fill("live-table", cfg.table || "");
  fill("live-query", cfg.query || "");
  fill("live-interval", cfg.interval_seconds ?? 120);
  fill("live-page", cfg.page_size ?? 50);
  fill("live-groups", (cfg.only_groups || []).join(", "));
  $("live-auto").checked = !!cfg.auto_propose;
  $("live-enabled").checked = !!cfg.enabled;

  await refreshInbox();
}

function renderLiveStatus(s) {
  const running = s.enabled && s.running;
  $("live-state").textContent = running
    ? "polling"
    : s.enabled
    ? "enabled, idle"
    : "off";

  const counts = s.counts || {};
  $("count-live").textContent = counts.new ? String(counts.new) : "";

  $("live-status").innerHTML = `
    ${!s.credentials_present
      ? `<div class="warn">
           ServiceNow credentials are not configured. Set
           <code>SERVICENOW_INSTANCE</code>, <code>SERVICENOW_USERNAME</code> and
           <code>SERVICENOW_PASSWORD</code> in <code>.env</code>, then restart.
         </div>`
      : ""}
    ${s.last_error
      ? `<div class="warn"><strong>Last error</strong><br>${esc(s.last_error)}</div>`
      : ""}
    <dl class="kv">
      <dt>Instance</dt><dd>${esc(s.instance || "—")}</dd>
      <dt>Access</dt><dd>${esc(s.access || "")}</dd>
      <dt>Execution</dt><dd>${esc(s.execution || "")}</dd>
      <dt>Last poll</dt>
      <dd>${s.last_poll_at
            ? `${esc(shortTime(s.last_poll_at))} — fetched ${s.last_fetched}, new ${s.last_new}`
            : "never"}</dd>
      <dt>Ingested total</dt><dd>${s.total_ingested ?? 0}</dd>
      <dt>Inbox</dt>
      <dd>${Object.entries(counts).length
            ? Object.entries(counts).map(([k, v]) => `${esc(k.replace(/_/g, " "))}: ${v}`).join(" · ")
            : "empty"}</dd>
    </dl>`;
}

async function saveLiveConfig() {
  const msg = $("live-msg");
  const groups = $("live-groups").value
    .split(",")
    .map((g) => g.trim())
    .filter(Boolean);

  try {
    setMsg(msg, "Saving…");
    await api("/live/config", {
      method: "PATCH",
      body: {
        table: $("live-table").value.trim(),
        query: $("live-query").value.trim(),
        interval_seconds: Number($("live-interval").value),
        page_size: Number($("live-page").value),
        auto_propose: $("live-auto").checked,
        only_groups: groups,
        enabled: $("live-enabled").checked,
      },
    });
    setMsg(msg, "Saved.", "ok");
    await refreshLive();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
}

async function probeLive() {
  const msg = $("live-msg");
  try {
    setMsg(msg, "Testing connection…");
    const r = await api("/live/probe", { method: "POST" });
    setMsg(
      msg,
      `Connected to ${r.instance}, table ${r.table}. Sample: ${r.sample_number || "none returned"}.`,
      "ok"
    );
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
}

async function pollLive() {
  const msg = $("live-msg");
  try {
    setMsg(msg, "Polling…");
    const r = await api("/live/poll", { method: "POST" });
    setMsg(msg, `Fetched ${r.fetched}, ${r.new} new.`, "ok");
    await refreshLive();
    await refreshQueue();
  } catch (err) {
    setMsg(msg, err.message, "error");
  }
}

function filteredInbox() {
  const f = state.inboxFilter;
  const needle = f.search.trim().toLowerCase();

  return state.liveTickets.filter((t) => {
    if (f.status && t.status !== f.status) return false;
    if (f.matchedOnly && !t.match?.matched) return false;
    if (f.group && (t.payload?.assignment_group || "") !== f.group) return false;
    if (needle) {
      const hay = [
        t.ticket_number,
        t.payload?.short_description,
        t.payload?.assignment_group,
        t.match?.procedure_name,
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    return true;
  });
}

function renderInboxFilters() {
  const counts = {};
  state.liveTickets.forEach((t) => {
    counts[t.status] = (counts[t.status] || 0) + 1;
  });

  // Status options carry their counts so you can see where the work is.
  const sel = $("inbox-filter");
  const previous = state.inboxFilter.status;
  sel.innerHTML =
    `<option value="">All statuses (${state.liveTickets.length})</option>` +
    Object.keys(LIVE_STATUS_INFO)
      .filter((k) => counts[k])
      .map(
        (k) =>
          `<option value="${esc(k)}">${esc(LIVE_STATUS_INFO[k].word)} (${counts[k]})</option>`
      )
      .join("");
  sel.value = previous;
  state.inboxFilter.status = sel.value;

  const groups = [
    ...new Set(
      state.liveTickets.map((t) => t.payload?.assignment_group).filter(Boolean)
    ),
  ].sort();
  const gsel = $("inbox-group");
  const prevGroup = state.inboxFilter.group;
  gsel.innerHTML =
    '<option value="">All groups</option>' +
    groups.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("");
  gsel.value = groups.includes(prevGroup) ? prevGroup : "";
  state.inboxFilter.group = gsel.value;
}

async function refreshInbox() {
  const { tickets } = await api("/live/tickets?limit=500");
  state.liveTickets = tickets;
  renderInboxFilters();
  renderInbox();
}

function renderInbox() {
  const rows = filteredInbox();
  const matched = state.liveTickets.filter((t) => t.match?.matched).length;

  $("inbox-count").textContent =
    rows.length === state.liveTickets.length
      ? `${rows.length} total · ${matched} actionable`
      : `${rows.length} of ${state.liveTickets.length}`;

  if (!state.liveTickets.length) {
    $("inbox-list").innerHTML =
      '<p class="empty">Nothing pulled in yet. Press “Poll now”, or turn on background polling.</p>';
    return;
  }

  if (!rows.length) {
    $("inbox-list").innerHTML =
      '<p class="empty">No tickets match these filters.</p>';
    return;
  }

  $("inbox-list").innerHTML = rows.map(renderInboxRow).join("");

  document.querySelectorAll("[data-live-propose]").forEach((b) => {
    b.addEventListener("click", async () => {
      const id = b.dataset.livePropose;
      const msg = $("live-msg");
      try {
        setMsg(msg, "Creating run…");
        await api(`/live/tickets/${id}/propose`, { method: "POST" });
        setMsg(msg, "Run created. See the Queue tab.", "ok");
      } catch (err) {
        if (err.info?.code !== "duplicate_run") {
          setMsg(msg, err.message, "error");
          return;
        }
        const again = confirm(
          `${err.info.ticket_id} already has a run in progress ` +
            `(${String(err.info.existing_state).replace(/_/g, " ")}).\n\n` +
            `Create a second run anyway?`
        );
        if (!again) {
          setMsg(msg, "Left as is. The existing run is in the Queue tab.", "");
          return;
        }
        try {
          await api(`/live/tickets/${id}/propose?allow_duplicate=true`, {
            method: "POST",
          });
          setMsg(msg, "Second run created deliberately.", "ok");
        } catch (e2) {
          setMsg(msg, e2.message, "error");
          return;
        }
      }
      await refreshInbox();
      await refreshQueue();
    });
  });

  document.querySelectorAll("[data-live-ignore]").forEach((b) => {
    b.addEventListener("click", async () => {
      const reason = prompt("Why ignore this ticket?") ?? "";
      try {
        await api(`/live/tickets/${b.dataset.liveIgnore}/ignore`, {
          method: "POST",
          body: { reason },
        });
        await refreshInbox();
      } catch (err) {
        setMsg($("live-msg"), err.message, "error");
      }
    });
  });
}

function renderInboxRow(t) {
  const m = t.match || {};
  const p = t.payload || {};
  const matched = !!m.matched;

  return `
    <div class="inbox-row ${matched ? "is-match" : ""}">
      <div class="inbox-main">
        <div class="inbox-head">
          <strong>${esc(t.ticket_number)}</strong>
          <span class="state-tag ${matched ? "state-you" : "state-all"}"
                title="${esc(LIVE_STATUS_INFO[t.status]?.help || "")}">
            ${esc(LIVE_STATUS_INFO[t.status]?.word || t.status)}
          </span>
          ${matched
            ? `<span class="owner owner-ai">${esc(m.procedure_name)}</span>`
            : ""}
        </div>
        <p class="inbox-desc">${esc(p.short_description || "(no description)")}</p>
        <div class="inbox-meta">
          ${esc(p.assignment_group || "no group")}
          ${p.cmdb_ci ? ` · CI ${esc(p.cmdb_ci)}` : ""}
          ${p.priority ? ` · P${esc(p.priority)}` : ""}
          · fetched ${esc(shortTime(t.fetched_at))}
        </div>
        ${(m.missing_parameters || []).length
          ? `<p class="hint" style="margin:.25rem 0 0">
               Missing parameters: ${esc(m.missing_parameters.join(", "))}
             </p>`
          : ""}
        ${(m.dictionary_terms_applied || []).length
          ? `<p class="hint" style="margin:.15rem 0 0">
               Dictionary helped: ${esc(m.dictionary_terms_applied.join(", "))}
             </p>`
          : ""}
        ${m.auto_propose_error
          ? `<p class="hint" style="margin:.15rem 0 0;color:var(--r3)">
               Auto-propose failed: ${esc(m.auto_propose_error)}
             </p>`
          : ""}
      </div>
      <div class="inbox-actions">
        ${t.run_id
          ? `<span class="hint">run created</span>`
          : matched
          ? `<button type="button" class="btn btn-approve"
                     data-live-propose="${esc(t.live_id)}">Create run</button>`
          : ""}
        ${t.status === "ignored"
          ? ""
          : `<button type="button" class="btn"
                     data-live-ignore="${esc(t.live_id)}">Ignore</button>`}
      </div>
    </div>`;
}

/* ------------------------------------------------------------------ views */

const VIEWS = ["queue", "knowledge", "live"];

async function switchView(name) {
  VIEWS.forEach((v) => {
    $(`view-${v}`).hidden = v !== name;
    const tab = $(`tab-${v}`);
    tab.setAttribute("aria-selected", String(v === name));
  });

  try {
    if (name === "knowledge") await refreshKnowledge();
    if (name === "live") await refreshLive();
  } catch (err) {
    console.error(err);
  }
}

/* ------------------------------------------------------------------ policy */

async function showPolicy() {
  const { policy } = await api("/risk-policy");
  $("policy-table").innerHTML = `
    <table>
      <thead>
        <tr><th>Action</th><th>Risk</th><th>Owner</th><th>Requires</th><th>Why</th></tr>
      </thead>
      <tbody>
        ${policy.map((p) => `
          <tr>
            <td><code>${esc(p.action)}</code></td>
            <td>${riskBadge(p.risk, p.risk_label)}</td>
            <td>${esc(p.owner)}</td>
            <td>${esc(p.policy.replace(/_/g, " "))}</td>
            <td>${esc(p.reason)}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;
  $("policy-dialog").showModal();
}

/* -------------------------------------------------------------------- boot */

function currentView() {
  return VIEWS.find((v) => !$(`view-${v}`).hidden) || "queue";
}

async function boot() {
  // queue
  $("propose").addEventListener("click", proposeSelected);
  $("refresh").addEventListener("click", async () => {
    const view = currentView();
    await refreshQueue();
    if (view === "queue" && state.selectedId) await select(state.selectedId);
    if (view === "knowledge") await refreshKnowledge();
    if (view === "live") await refreshLive();
  });

  // tabs
  document.querySelectorAll("[data-view]").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });

  // queue filters -- debounced so typing stays responsive
  let searchTimer;
  $("queue-search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.queueFilter.search = e.target.value;
      renderQueue();
    }, 150);
  });
  $("queue-group").addEventListener("change", (e) => {
    state.queueFilter.group = e.target.value;
    renderQueue();
  });
  $("queue-sort").addEventListener("change", (e) => {
    state.queueFilter.sort = e.target.value;
    renderQueue();
  });

  // inbox filters
  let inboxTimer;
  $("inbox-search").addEventListener("input", (e) => {
    clearTimeout(inboxTimer);
    inboxTimer = setTimeout(() => {
      state.inboxFilter.search = e.target.value;
      renderInbox();
    }, 150);
  });
  $("inbox-group").addEventListener("change", (e) => {
    state.inboxFilter.group = e.target.value;
    renderInbox();
  });
  $("inbox-matched").addEventListener("change", (e) => {
    state.inboxFilter.matchedOnly = e.target.checked;
    renderInbox();
  });

  // knowledge
  $("do-upload").addEventListener("click", doUpload);
  $("do-dict-test").addEventListener("click", testDictionary);
  $("dict-test").addEventListener("keydown", (e) => {
    if (e.key === "Enter") testDictionary();
  });

  // live
  $("save-live").addEventListener("click", saveLiveConfig);
  $("probe-live").addEventListener("click", probeLive);
  $("poll-live").addEventListener("click", pollLive);
  $("inbox-filter").addEventListener("change", (e) => {
    state.inboxFilter.status = e.target.value;
    renderInbox();
  });

  // policy dialog
  $("show-policy").addEventListener("click", showPolicy);
  $("close-policy").addEventListener("click", () => $("policy-dialog").close());

  try {
    await loadFormats();
    await loadTickets();
    await refreshQueue();
  } catch (err) {
    setMsg($("intake-msg"), `Cannot reach the API: ${err.message}`, "error");
  }
}

document.addEventListener("DOMContentLoaded", boot);
